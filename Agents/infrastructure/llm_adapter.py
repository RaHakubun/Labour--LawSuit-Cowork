from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from pydantic import TypeAdapter, ValidationError

from Agents.application.decisions import ControllerDecision
from Agents.application.scenario_models import ScenarioResult
from Agents.application.legal_models import (
    DocumentDraftResult,
    LegalAnalysisResult,
)
from Agents.domain.case_state import CaseState
from Agents.scene_catalog import (
    get_role_scene_template_path,
    validate_role_id,
    validate_scene_id,
    validate_template_catalog,
)


PROJECT_ROOT = Path(__file__).resolve().parents[2]
CONTROLLER_TEMPLATE = PROJECT_ROOT / "Prompt_Template" / "ControllerAgent.md"
_DECISION_ADAPTER: TypeAdapter[ControllerDecision] = TypeAdapter(ControllerDecision)
_SCENARIO_RESULT_ADAPTER: TypeAdapter[ScenarioResult] = TypeAdapter(ScenarioResult)
_LEGAL_RESULT_ADAPTER: TypeAdapter[LegalAnalysisResult] = TypeAdapter(LegalAnalysisResult)
_DOCUMENT_RESULT_ADAPTER: TypeAdapter[DocumentDraftResult] = TypeAdapter(DocumentDraftResult)


def _parse_json_object(text: str, *, actor_name: str) -> dict[str, Any]:
    cleaned = text.strip()
    if not cleaned:
        raise ValueError(f"{actor_name} returned an empty response")
    if cleaned.startswith("```"):
        cleaned = cleaned.removeprefix("```json").removeprefix("```")
        cleaned = cleaned.removesuffix("```").strip()
    try:
        payload = json.loads(cleaned)
    except json.JSONDecodeError as exc:
        raise ValueError(f"{actor_name} response is not valid JSON") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"{actor_name} response must be a JSON object")
    return payload


class AsyncOpenAIControllerDecisionProvider:
    """Real asynchronous Controller adapter with a strict response contract."""

    def __init__(
        self,
        *,
        base_url: str | None = None,
        api_key: str | None = None,
        model: str | None = None,
        template_path: Path = CONTROLLER_TEMPLATE,
        timeout_seconds: float = 60.0,
    ) -> None:
        self._base_url = (base_url or os.getenv("LLM_BASE_URL") or "").strip()
        self._api_key = (api_key or os.getenv("LLM_API_KEY") or "").strip()
        self._model = (model or os.getenv("LLM_MODEL") or "").strip()
        self._template_path = template_path
        self._timeout_seconds = timeout_seconds
        if not self._base_url:
            raise RuntimeError("LLM_BASE_URL is required")
        if not self._api_key:
            raise RuntimeError("LLM_API_KEY is required")
        if not self._model:
            raise RuntimeError("LLM_MODEL is required")

    async def decide(
        self,
        *,
        case_state: CaseState,
        user_input: str,
    ) -> ControllerDecision:
        try:
            from openai import AsyncOpenAI
        except ImportError as exc:
            raise ImportError("Install the openai dependency to run ControllerAgent") from exc

        template = self._template_path.read_text(encoding="utf-8")
        contract = (
            "\n\n你必须只输出一个 JSON 对象，不要输出 Markdown。"
            '若信息不足：{"decision_type":"ask_clarification","question":"...",'
            '"required_fact_ids":["..."]}。'
            '若可进入场景：{"decision_type":"route_scenario","scene_id":"...",'
            '"reason":"...","current_goal":"..."}。'
            '若需用户确认事实：{"decision_type":"request_fact_confirmation",'
            '"fact_ids":["..."],"reason":"..."}。'
            '若可分析：{"decision_type":"request_analysis","reason":"..."}。'
            '若可生成文书：{"decision_type":"request_document",'
            '"document_type":"labour_arbitration_application","reason":"..."}。'
            '若应继续当前阶段：{"decision_type":"continue_current_stage","reason":"..."}。'
            "不得虚构缺失事实。"
        )
        prompt = (
            template.replace("{user_input}", user_input)
            .replace(
                "{conversation_context}",
                json.dumps(case_state.model_dump(mode="json"), ensure_ascii=False),
            )
            .replace("{attachments_meta}", "[]")
            + contract
        )
        client = AsyncOpenAI(
            base_url=self._base_url,
            api_key=self._api_key,
            timeout=self._timeout_seconds,
        )
        response = await client.chat.completions.create(
            model=self._model,
            messages=[{"role": "user", "content": prompt}],
        )
        text = response.choices[0].message.content or ""
        payload = _parse_json_object(text, actor_name="ControllerAgent")
        try:
            return _DECISION_ADAPTER.validate_python(payload)
        except ValidationError as exc:
            raise ValueError(f"ControllerAgent protocol violation: {exc}") from exc

class AsyncOpenAIScenarioResultProvider:
    """Scenario adapter that returns only the M4 typed contract."""

    def __init__(
        self,
        *,
        base_url: str | None = None,
        api_key: str | None = None,
        model: str | None = None,
        timeout_seconds: float = 90.0,
    ) -> None:
        self._base_url = (base_url or os.getenv("LLM_BASE_URL") or "").strip()
        self._api_key = (api_key or os.getenv("LLM_API_KEY") or "").strip()
        self._model = (model or os.getenv("LLM_MODEL") or "").strip()
        self._timeout_seconds = timeout_seconds
        if not self._base_url:
            raise RuntimeError("LLM_BASE_URL is required")
        if not self._api_key:
            raise RuntimeError("LLM_API_KEY is required")
        if not self._model:
            raise RuntimeError("LLM_MODEL is required")
        validate_template_catalog(PROJECT_ROOT)

    async def analyze(
        self,
        *,
        role_id: str,
        scene_id: str,
        case_state: CaseState,
    ) -> ScenarioResult:
        try:
            from openai import AsyncOpenAI
        except ImportError as exc:
            raise ImportError("Install the openai dependency to run ScenarioAgent") from exc

        role = validate_role_id(role_id)
        scene = validate_scene_id(scene_id)
        template_path = PROJECT_ROOT / get_role_scene_template_path(role, scene)
        template = template_path.read_text(encoding="utf-8")
        contract = (
            "\n\n忽略模板中旧版输出示例，必须只输出以下结构的 JSON 对象，禁止 Markdown："
            '{"scene_id":"'
            + scene
            + '","confidence":0.0,'
            '"candidate_facts":[{"fact_id":"...","value":"...",'
            '"confidence":0.0,"derivation":"依据用户原文的说明"}],'
            '"missing_fact_questions":["最多三个具体问题"],'
            '"evidence_requirements":[{"evidence_type":"...","purpose":"...",'
            '"required":true}],'
            '"retrieval_plan":[{"tool_name":"检索法律法规-语义",'
            '"query":"针对本案事实的完整查询","purpose":"...","required":true}],'
            '"rule_calculation_requests":[{"rule_name":"...",'
            '"required_fact_ids":["..."]}],"summary":"场景处理摘要"}。'
            "不得在检索完成前生成法律结论或法条内容；没有来源时不得伪造引用。"
        )
        prompt = (
            template.replace(
                "{user_input}",
                case_state.interaction.last_user_input,
            )
            .replace(
                "{conversation_context}",
                json.dumps(case_state.model_dump(mode="json"), ensure_ascii=False),
            )
            .replace("{attachments_meta}", "[]")
            + f"\n\nuser_role={role}\n"
            + contract
        )
        client = AsyncOpenAI(
            base_url=self._base_url,
            api_key=self._api_key,
            timeout=self._timeout_seconds,
        )
        response = await client.chat.completions.create(
            model=self._model,
            messages=[{"role": "user", "content": prompt}],
        )
        text = response.choices[0].message.content or ""
        payload = _parse_json_object(text, actor_name="ScenarioAgent")
        try:
            result = _SCENARIO_RESULT_ADAPTER.validate_python(payload)
        except ValidationError as exc:
            raise ValueError(f"ScenarioAgent protocol violation: {exc}") from exc
        if result.scene_id != scene:
            raise ValueError(
                f"ScenarioAgent returned scene_id={result.scene_id}, expected {scene}"
            )
        return result


class AsyncOpenAILegalResultProvider:
    """Legal adapter restricted to the context builder projection and typed outputs."""

    def __init__(
        self,
        *,
        base_url: str | None = None,
        api_key: str | None = None,
        model: str | None = None,
        timeout_seconds: float = 120.0,
    ) -> None:
        self._base_url = (base_url or os.getenv("LLM_BASE_URL") or "").strip()
        self._api_key = (api_key or os.getenv("LLM_API_KEY") or "").strip()
        self._model = (model or os.getenv("LLM_MODEL") or "").strip()
        self._timeout_seconds = timeout_seconds
        if not self._base_url:
            raise RuntimeError("LLM_BASE_URL is required")
        if not self._api_key:
            raise RuntimeError("LLM_API_KEY is required")
        if not self._model:
            raise RuntimeError("LLM_MODEL is required")

    async def analyze(self, *, context: dict[str, object]) -> LegalAnalysisResult:
        contract = (
            "只输出 JSON，不得输出 Markdown 代码围栏。结构必须为："
            '{"summary":"...","report_markdown":"## 简要回复\\n...",'
            '"issues":[{"title":"...","conclusion":"...","fact_ids":["..."],'
            '"evidence_ids":[],"authority_ids":["UUID"]}]}。'
            "每项结论必须引用输入中真实存在的事实 ID 和 authority UUID；"
            "不得生成新事实、法条或来源。"
        )
        payload = await self._complete(
            actor_name="LegalAnalysisAgent",
            prompt=self._legal_prompt(context, contract),
        )
        try:
            return _LEGAL_RESULT_ADAPTER.validate_python(payload)
        except ValidationError as exc:
            raise ValueError(f"LegalAnalysisAgent protocol violation: {exc}") from exc

    async def draft_document(
        self,
        *,
        context: dict[str, object],
        document_type: str,
    ) -> DocumentDraftResult:
        label = {
            "legal_analysis_report": "法律分析报告",
            "labour_arbitration_application": "劳动仲裁申请书",
        }[document_type]
        contract = (
            f"生成完整的{label}，只输出 JSON，不得输出 Markdown 代码围栏。结构必须为："
            '{"title":"...","content":"...","fact_ids":["..."],'
            '"evidence_ids":[],"authority_ids":["UUID"],"rule_result_ids":[]}。'
            "引用 ID 必须来自输入；劳动仲裁申请书必须包含当事人、仲裁请求、事实与理由、"
            "证据目录和落款字段，对缺失身份信息使用明确待填写标记，不得虚构。"
        )
        payload = await self._complete(
            actor_name="DocumentDraftAgent",
            prompt=self._legal_prompt(context, contract),
        )
        try:
            return _DOCUMENT_RESULT_ADAPTER.validate_python(payload)
        except ValidationError as exc:
            raise ValueError(f"DocumentDraftAgent protocol violation: {exc}") from exc

    def _legal_prompt(self, context: dict[str, object], contract: str) -> str:
        template = (PROJECT_ROOT / "Prompt_Template" / "LegalAnalysisAgent.md").read_text(
            encoding="utf-8"
        )
        return (
            template
            + "\n\n以下是经过权限过滤和状态标注的唯一案件上下文：\n"
            + json.dumps(context, ensure_ascii=False)
            + "\n\n"
            + contract
        )

    async def _complete(self, *, actor_name: str, prompt: str) -> dict[str, Any]:
        try:
            from openai import AsyncOpenAI
        except ImportError as exc:
            raise ImportError("Install the openai dependency to run legal generation") from exc
        client = AsyncOpenAI(
            base_url=self._base_url,
            api_key=self._api_key,
            timeout=self._timeout_seconds,
        )
        response = await client.chat.completions.create(
            model=self._model,
            messages=[{"role": "user", "content": prompt}],
        )
        return _parse_json_object(
            response.choices[0].message.content or "",
            actor_name=actor_name,
        )
