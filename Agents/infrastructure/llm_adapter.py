from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from pydantic import TypeAdapter, ValidationError

from Agents.application.decisions import ControllerDecision
from Agents.domain.case_state import CaseState


PROJECT_ROOT = Path(__file__).resolve().parents[2]
CONTROLLER_TEMPLATE = PROJECT_ROOT / "Prompt_Template" / "ControllerAgent.md"
_DECISION_ADAPTER: TypeAdapter[ControllerDecision] = TypeAdapter(ControllerDecision)


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
        payload = self._parse_json_object(text)
        try:
            return _DECISION_ADAPTER.validate_python(payload)
        except ValidationError as exc:
            raise ValueError(f"ControllerAgent protocol violation: {exc}") from exc

    def _parse_json_object(self, text: str) -> dict[str, Any]:
        cleaned = text.strip()
        if not cleaned:
            raise ValueError("ControllerAgent returned an empty response")
        if cleaned.startswith("```"):
            cleaned = cleaned.removeprefix("```json").removeprefix("```")
            cleaned = cleaned.removesuffix("```").strip()
        try:
            payload = json.loads(cleaned)
        except json.JSONDecodeError as exc:
            raise ValueError("ControllerAgent response is not valid JSON") from exc
        if not isinstance(payload, dict):
            raise ValueError("ControllerAgent response must be a JSON object")
        return payload
