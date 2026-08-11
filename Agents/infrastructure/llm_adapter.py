from __future__ import annotations

import json
import os
from pathlib import Path
import re
from typing import Any, cast

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
RUNTIME_INPUT_MARKER = "## 运行时输入（渲染为独立 user message）"
_PLACEHOLDER_RE = re.compile(r"\{([A-Za-z][A-Za-z0-9_]*)\}")
_DECISION_ADAPTER: TypeAdapter[ControllerDecision] = TypeAdapter(ControllerDecision)
_SCENARIO_RESULT_ADAPTER: TypeAdapter[ScenarioResult] = TypeAdapter(ScenarioResult)
_LEGAL_RESULT_ADAPTER: TypeAdapter[LegalAnalysisResult] = TypeAdapter(LegalAnalysisResult)
_DOCUMENT_RESULT_ADAPTER: TypeAdapter[DocumentDraftResult] = TypeAdapter(DocumentDraftResult)


def _render_placeholders(
    template: str,
    values: dict[str, object],
    *,
    section_name: str,
) -> str:
    placeholders = set(_PLACEHOLDER_RE.findall(template))
    missing = sorted(placeholders.difference(values))
    if missing:
        raise ValueError(
            f"{section_name} has unresolved placeholders: {', '.join(missing)}"
        )
    return _PLACEHOLDER_RE.sub(
        lambda match: json.dumps(
            values[match.group(1)],
            ensure_ascii=False,
            separators=(",", ":"),
        ),
        template,
    )


def _build_chat_messages(
    *,
    template: str,
    system_values: dict[str, object],
    input_payload: dict[str, object],
) -> list[dict[str, str]]:
    if template.count(RUNTIME_INPUT_MARKER) != 1:
        raise ValueError(
            "prompt template must contain exactly one runtime input marker: "
            f"{RUNTIME_INPUT_MARKER}"
        )
    system_template, user_template = template.split(RUNTIME_INPUT_MARKER, maxsplit=1)
    system_content = _render_placeholders(
        system_template.strip(),
        system_values,
        section_name="system template",
    )
    user_content = _render_placeholders(
        user_template.strip(),
        input_payload,
        section_name="runtime input template",
    )
    return [
        {"role": "system", "content": system_content},
        {"role": "user", "content": user_content},
    ]


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
            from openai.types.chat import ChatCompletionMessageParam
        except ImportError as exc:
            raise ImportError("Install the openai dependency to run ControllerAgent") from exc

        template = self._template_path.read_text(encoding="utf-8")
        case_snapshot = case_state.model_dump(mode="json")
        messages = _build_chat_messages(
            template=template,
            system_values={
                "output_schema": _DECISION_ADAPTER.json_schema(),
            },
            input_payload={
                "user_input": user_input,
                "conversation_context": case_snapshot,
                "attachments_meta": list(case_snapshot["evidence"]["items"].values()),
            },
        )
        client = AsyncOpenAI(
            base_url=self._base_url,
            api_key=self._api_key,
            timeout=self._timeout_seconds,
        )
        response = await client.chat.completions.create(
            model=self._model,
            messages=cast(list[ChatCompletionMessageParam], messages),
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
            from openai.types.chat import ChatCompletionMessageParam
        except ImportError as exc:
            raise ImportError("Install the openai dependency to run ScenarioAgent") from exc

        role = validate_role_id(role_id)
        scene = validate_scene_id(scene_id)
        template_path = PROJECT_ROOT / get_role_scene_template_path(role, scene)
        template = template_path.read_text(encoding="utf-8")
        case_snapshot = case_state.model_dump(mode="json")
        messages = _build_chat_messages(
            template=template,
            system_values={
                "output_schema": _SCENARIO_RESULT_ADAPTER.json_schema(),
            },
            input_payload={
                "user_role": role,
                "user_input": case_state.interaction.last_user_input,
                "conversation_context": case_snapshot,
                "attachments_meta": list(case_snapshot["evidence"]["items"].values()),
            },
        )
        client = AsyncOpenAI(
            base_url=self._base_url,
            api_key=self._api_key,
            timeout=self._timeout_seconds,
        )
        response = await client.chat.completions.create(
            model=self._model,
            messages=cast(list[ChatCompletionMessageParam], messages),
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
        payload = await self._complete(
            actor_name="LegalAnalysisAgent",
            messages=self._legal_messages(
                context,
                output_task="legal_analysis",
                document_type=None,
                output_schema=_LEGAL_RESULT_ADAPTER.json_schema(),
            ),
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
        payload = await self._complete(
            actor_name="DocumentDraftAgent",
            messages=self._legal_messages(
                context,
                output_task=f"document:{label}",
                document_type=document_type,
                output_schema=_DOCUMENT_RESULT_ADAPTER.json_schema(),
            ),
        )
        try:
            return _DOCUMENT_RESULT_ADAPTER.validate_python(payload)
        except ValidationError as exc:
            raise ValueError(f"DocumentDraftAgent protocol violation: {exc}") from exc

    def _legal_messages(
        self,
        context: dict[str, object],
        *,
        output_task: str,
        document_type: str | None,
        output_schema: dict[str, Any],
    ) -> list[dict[str, str]]:
        template = (PROJECT_ROOT / "Prompt_Template" / "LegalAnalysisAgent.md").read_text(
            encoding="utf-8"
        )
        scenario_input = {
            key: value
            for key, value in context.items()
            if key not in {"authorities", "rule_results"}
        }
        tool_history = {
            "authorities": context.get("authorities", []),
            "rule_results": context.get("rule_results", []),
        }
        return _build_chat_messages(
            template=template,
            system_values={"output_schema": output_schema},
            input_payload={
                "output_task": output_task,
                "document_type": document_type,
                "Scenario_Agent_Input": scenario_input,
                "Tool_Call_History": tool_history,
            },
        )

    async def _complete(
        self,
        *,
        actor_name: str,
        messages: list[dict[str, str]],
    ) -> dict[str, Any]:
        try:
            from openai import AsyncOpenAI
            from openai.types.chat import ChatCompletionMessageParam
        except ImportError as exc:
            raise ImportError("Install the openai dependency to run legal generation") from exc
        client = AsyncOpenAI(
            base_url=self._base_url,
            api_key=self._api_key,
            timeout=self._timeout_seconds,
        )
        response = await client.chat.completions.create(
            model=self._model,
            messages=cast(list[ChatCompletionMessageParam], messages),
        )
        return _parse_json_object(
            response.choices[0].message.content or "",
            actor_name=actor_name,
        )
