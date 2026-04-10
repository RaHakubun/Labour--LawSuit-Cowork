from dataclasses import dataclass, field
import json
from pathlib import Path
import re
from typing import Any, Callable, Optional

from . import llm_call
from utils.pkulaw_mcp_client import (
    ALLOWED_MCP_SERVICE_NAMES,
    mcp_query,
    validate_mcp_service_name,
)

DEFAULT_STOP_WORDS = {"exit", "quit", "q", "退出"}
PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCENARIO_TEMPLATE_ROOT = (PROJECT_ROOT / "Prompt_Template" / "ScenarioAgents").resolve()
DEFAULT_MCP_RETRIES = 2
MCP_CALL_RE = re.compile(
    r'mcp_query\(\s*["“](?P<tool>[^"”]+)["”]\s*,\s*["“](?P<input>[^"”]+)["”]\s*\)'
)


@dataclass
class ScenarioTurnResult:
    user_input: str
    conversation_context: str
    injected_prompt: str
    agent_reply: str
    parsed_output: dict[str, Any]
    askmore: str


@dataclass
class ScenarioToolCall:
    tool_name: str
    input: str


@dataclass
class ScenarioToolExecutionResult:
    tool_calls: list[ScenarioToolCall]
    tool_results: list[str]
    tool_call_history: str


@dataclass
class ScenarioAgent:
    main_prompt: str = ""
    model: Optional[str] = None
    llm_callable: Callable[..., str] = field(default=llm_call.chat_completion, repr=False)
    mcp_query_callable: Callable[..., str] = field(default=mcp_query, repr=False)
    allowed_tool_names: set[str] = field(default_factory=lambda: set(ALLOWED_MCP_SERVICE_NAMES))
    conversation_messages: list[tuple[str, str]] = field(default_factory=list, init=False)

    def _append_message(self, role: str, content: str) -> None:
        self.conversation_messages.append((role, content))

    def build_conversation_context(self) -> str:
        return "\n".join(f"{role}: {content}" for role, content in self.conversation_messages)

    def _to_attachments_text(self, attachments_meta: Optional[Any]) -> str:
        if attachments_meta is None:
            return "[]"
        if isinstance(attachments_meta, str):
            return attachments_meta
        return json.dumps(attachments_meta, ensure_ascii=False)

    def _resolve_template_path(self, template_path: str) -> Path:
        path = Path(template_path)
        if path.is_absolute():
            resolved = path.resolve()
        else:
            project_candidate = (PROJECT_ROOT / path).resolve()
            scenario_candidate = (SCENARIO_TEMPLATE_ROOT / path).resolve()
            cwd_candidate = (Path.cwd() / path).resolve()

            if project_candidate.exists():
                resolved = project_candidate
            elif scenario_candidate.exists():
                resolved = scenario_candidate
            else:
                resolved = cwd_candidate

        if SCENARIO_TEMPLATE_ROOT not in resolved.parents and resolved != SCENARIO_TEMPLATE_ROOT:
            raise ValueError(
                f"Template path must be under {SCENARIO_TEMPLATE_ROOT}. Got: {resolved}"
            )
        if not resolved.exists():
            raise FileNotFoundError(f"Template not found: {resolved}")
        return resolved

    def _inject_template_variables(
        self,
        template: str,
        user_input: str,
        conversation_context: str,
        attachments_meta: Optional[Any] = None,
    ) -> str:
        rendered = template.replace("{user_input}", user_input)
        rendered = rendered.replace("{conversation_context}", conversation_context)
        rendered = rendered.replace("{attachments_meta}", self._to_attachments_text(attachments_meta))
        return rendered

    def build_user_prompt(
        self,
        user_input: str,
        template_path: str,
        attachments_meta: Optional[Any] = None,
    ) -> str:
        resolved_template_path = self._resolve_template_path(template_path)
        template = resolved_template_path.read_text(encoding="utf-8")
        return self._inject_template_variables(
            template=template,
            user_input=user_input,
            conversation_context=self.build_conversation_context(),
            attachments_meta=attachments_meta,
        )

    def _extract_json_payload(self, text: str) -> dict[str, Any]:
        raw = text.strip()
        candidates: list[str] = []

        if raw.startswith("```"):
            fence_parts = raw.split("```")
            for i in range(1, len(fence_parts), 2):
                block = fence_parts[i].strip()
                if not block:
                    continue
                if "\n" in block and block.split("\n", 1)[0].strip().lower() == "json":
                    block = block.split("\n", 1)[1].strip()
                candidates.append(block)

        candidates.append(raw)
        if "{" in raw and "}" in raw:
            candidates.append(raw[raw.find("{") : raw.rfind("}") + 1])

        for candidate in candidates:
            try:
                payload = json.loads(candidate)
            except Exception:
                continue
            if isinstance(payload, dict):
                return payload

        raise ValueError("ScenarioAgent output is not valid JSON object.")

    def _extract_askmore(self, payload: dict[str, Any]) -> str:
        askmore = payload.get("askmore")
        if isinstance(askmore, str):
            normalized = askmore.strip().lower()
            if normalized in {"yes", "no"}:
                return normalized
        raise ValueError('Missing or invalid "askmore" in scenario output.')

    def _parse_mcp_query_string(self, text: str) -> list[ScenarioToolCall]:
        calls: list[ScenarioToolCall] = []
        stripped = text.strip()
        if not stripped:
            return calls
        matches = list(MCP_CALL_RE.finditer(stripped))
        for match in matches:
            calls.append(
                ScenarioToolCall(
                    tool_name=match.group("tool").strip(),
                    input=match.group("input").strip(),
                )
            )
        return calls

    def _normalize_tool_item(self, item: Any) -> list[ScenarioToolCall]:
        calls: list[ScenarioToolCall] = []
        if isinstance(item, dict):
            tool_name = (
                item.get("tool_name")
                or item.get("toolname")
                or item.get("name")
                or item.get("service_name")
            )
            tool_input = item.get("input") or item.get("query") or item.get("text")
            if tool_name and tool_input:
                calls.append(ScenarioToolCall(str(tool_name).strip(), str(tool_input).strip()))
                return calls

            for key, value in item.items():
                key_str = str(key).strip()

                if isinstance(value, str):
                    if key_str in self.allowed_tool_names:
                        calls.append(ScenarioToolCall(key_str, value.strip()))
                        continue
                    parsed_from_value = self._parse_mcp_query_string(value)
                    if parsed_from_value:
                        calls.extend(parsed_from_value)
                    continue

                if isinstance(value, dict):
                    nested_calls = self._normalize_tool_item(value)
                    if nested_calls:
                        calls.extend(nested_calls)
                    continue

                if isinstance(value, list):
                    for nested in value:
                        calls.extend(self._normalize_tool_item(nested))
            return calls

        if isinstance(item, str):
            calls.extend(self._parse_mcp_query_string(item))
            return calls
        return calls

    def normalize_tool_calls(self, payload: dict[str, Any]) -> list[ScenarioToolCall]:
        raw_tool = payload.get("tool")
        calls: list[ScenarioToolCall] = []

        if isinstance(raw_tool, list):
            for item in raw_tool:
                calls.extend(self._normalize_tool_item(item))
        elif raw_tool is not None:
            calls.extend(self._normalize_tool_item(raw_tool))

        for call in calls:
            validate_mcp_service_name(call.tool_name, allowed_service_names=self.allowed_tool_names)
            if not call.input:
                raise ValueError(f"empty tool input for tool: {call.tool_name}")
        return calls

    def _render_tool_history(
        self,
        calls: list[ScenarioToolCall],
        results: list[str],
        title: str = "[Scenario Bootstrap] Tool Call(s)",
    ) -> str:
        if not calls:
            return ""
        lines: list[str] = [title]
        for idx, (call, result_text) in enumerate(zip(calls, results), start=1):
            lines.append(f"{idx}. mcp_query(\"{call.tool_name}\", \"{call.input}\")")
            lines.append("result:")
            lines.append(result_text)
        return "\n".join(lines).strip()

    def _call_tool_with_retry(
        self,
        call: ScenarioToolCall,
        retry_times: int,
        continue_on_error: bool,
    ) -> str:
        max_attempts = max(1, retry_times + 1)
        last_error: Optional[Exception] = None
        for attempt in range(1, max_attempts + 1):
            try:
                return self.mcp_query_callable(call.tool_name, call.input)
            except Exception as exc:
                last_error = exc
                if attempt < max_attempts:
                    continue

        if continue_on_error:
            return (
                f"MCP 服务名: {call.tool_name}\n"
                f"查询内容: {call.input}\n\n"
                f"结果是:\nMCP 调用失败（已重试 {max_attempts} 次）：{last_error}"
            )
        raise RuntimeError(
            f"Scenario MCP call failed after {max_attempts} attempts: "
            f"{call.tool_name}, query={call.input}, error={last_error}"
        )

    def execute_tools_from_payload(
        self,
        payload: dict[str, Any],
        retry_times: int = DEFAULT_MCP_RETRIES,
        continue_on_error: bool = True,
    ) -> ScenarioToolExecutionResult:
        calls = self.normalize_tool_calls(payload)
        results: list[str] = []
        for call in calls:
            results.append(
                self._call_tool_with_retry(
                    call=call,
                    retry_times=retry_times,
                    continue_on_error=continue_on_error,
                )
            )
        history = self._render_tool_history(calls, results)
        return ScenarioToolExecutionResult(
            tool_calls=calls,
            tool_results=results,
            tool_call_history=history,
        )

    def run_turn(
        self,
        user_input: str,
        template_path: str,
        attachments_meta: Optional[Any] = None,
        **kwargs: Any,
    ) -> ScenarioTurnResult:
        self._append_message("User", user_input)
        conversation_context = self.build_conversation_context()
        injected_prompt = self.build_user_prompt(
            user_input=user_input,
            template_path=template_path,
            attachments_meta=attachments_meta,
        )
        system_prompt = self.main_prompt if self.main_prompt.strip() else None
        agent_reply = self.llm_callable(
            user_prompt=injected_prompt,
            system_prompt=system_prompt,
            model=self.model,
            **kwargs,
        )
        self._append_message("Agent", agent_reply)

        payload = self._extract_json_payload(agent_reply)
        askmore = self._extract_askmore(payload)

        return ScenarioTurnResult(
            user_input=user_input,
            conversation_context=conversation_context,
            injected_prompt=injected_prompt,
            agent_reply=agent_reply,
            parsed_output=payload,
            askmore=askmore,
        )

    def chat_console(
        self,
        initial_user_input: str,
        template_path: str,
        attachments_meta: Optional[Any] = None,
        stop_words: Optional[set[str]] = None,
        input_func: Callable[[str], str] = input,
        output_func: Callable[[str], None] = print,
        **kwargs: Any,
    ) -> None:
        stop_words = stop_words or DEFAULT_STOP_WORDS
        current_input = initial_user_input
        round_index = 1

        while True:
            try:
                result = self.run_turn(
                    user_input=current_input,
                    template_path=template_path,
                    attachments_meta=attachments_meta,
                    **kwargs,
                )
            except Exception as exc:
                output_func(f"ScenarioAgentError: {exc}")
                output_func("Session ended.")
                break

            output_func(f"[Scenario Round {round_index}]")
            output_func(f"User: {result.user_input}")
            output_func("Conversation Context:")
            output_func(result.conversation_context)
            output_func("Injected Prompt:")
            output_func(result.injected_prompt)
            output_func(f"Agent Raw: {result.agent_reply}")

            if result.askmore == "no":
                output_func("Final JSON:")
                output_func(json.dumps(result.parsed_output, ensure_ascii=False, indent=2))
                output_func("askmore=no, session ended.")
                break

            follow_up = result.parsed_output.get("ask")
            if isinstance(follow_up, str) and follow_up.strip():
                output_func(f"Agent Ask: {follow_up.strip()}")

            next_input = input_func("User> ").strip()
            if next_input in stop_words:
                output_func("Session ended.")
                break
            current_input = next_input
            round_index += 1
