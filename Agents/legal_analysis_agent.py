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

DEFAULT_MAX_ROUNDS = 8
PROJECT_ROOT = Path(__file__).resolve().parents[1]
LEGAL_ANALYSIS_TEMPLATE_DEFAULT = "Prompt_Template/LegalAnalysisAgent.md"

MCP_CALL_RE = re.compile(
    r'mcp_query\(\s*["“](?P<tool>[^"”]+)["”]\s*,\s*["“](?P<input>[^"”]+)["”]\s*\)'
)


@dataclass
class ToolCall:
    tool_name: str
    input: str


@dataclass
class LegalAnalysisTurnResult:
    injected_prompt: str
    agent_reply: str
    parsed_output: dict[str, Any]
    askmore: str
    query: str
    tool_calls: list[ToolCall]
    tool_call_history: str


@dataclass
class LegalAnalysisAgent:
    main_prompt: str = ""
    model: Optional[str] = None
    llm_callable: Callable[..., str] = field(default=llm_call.chat_completion, repr=False)
    mcp_query_callable: Callable[..., str] = field(default=mcp_query, repr=False)
    allowed_tool_names: set[str] = field(default_factory=lambda: set(ALLOWED_MCP_SERVICE_NAMES))

    def _resolve_template_path(self, template_path: str) -> Path:
        path = Path(template_path)
        if path.is_absolute():
            resolved = path
        else:
            project_candidate = PROJECT_ROOT / path
            cwd_candidate = Path.cwd() / path
            resolved = project_candidate if project_candidate.exists() else cwd_candidate
        if not resolved.exists():
            raise FileNotFoundError(f"Template not found: {resolved}")
        return resolved

    def _inject_template_variables(
        self,
        template: str,
        scenario_agent_input: str,
        tool_call_history: str,
    ) -> str:
        rendered = template.replace("{Scenario_Agent_Input}", scenario_agent_input)
        rendered = rendered.replace("{Tool_Call_History}", tool_call_history)
        return rendered

    def build_user_prompt(
        self,
        scenario_agent_input: str,
        tool_call_history: str,
        template_path: str,
    ) -> str:
        resolved_template_path = self._resolve_template_path(template_path)
        template = resolved_template_path.read_text(encoding="utf-8")
        return self._inject_template_variables(
            template=template,
            scenario_agent_input=scenario_agent_input,
            tool_call_history=tool_call_history,
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
        raise ValueError("LegalAnalysisAgent output is not valid JSON object.")

    def _extract_askmore(self, payload: dict[str, Any]) -> str:
        askmore = payload.get("askmore")
        if isinstance(askmore, str):
            normalized = askmore.strip().lower()
            if normalized in {"yes", "no", "end"}:
                return normalized
        raise ValueError('Missing or invalid "askmore" in legal analysis output.')

    def _extract_query(self, payload: dict[str, Any]) -> str:
        query = payload.get("query")
        if query is None:
            return ""
        if not isinstance(query, str):
            raise ValueError('payload.query must be a string when provided.')
        return query.strip()

    def _validate_data_object(self, payload: dict[str, Any]) -> dict[str, Any]:
        raw_data = payload.get("data")
        if not isinstance(raw_data, dict):
            raise ValueError('askmore=no/end requires "data" as JSON object.')

        schema_version = raw_data.get("schema_version")
        if not isinstance(schema_version, str) or not schema_version.strip():
            raise ValueError('payload.data.schema_version must be a non-empty string.')

        issues = raw_data.get("issues")
        citations = raw_data.get("citations")
        if not isinstance(issues, list):
            raise ValueError('payload.data.issues must be a list.')
        if not isinstance(citations, list):
            raise ValueError('payload.data.citations must be a list.')

        for idx, issue in enumerate(issues, start=1):
            if not isinstance(issue, dict):
                raise ValueError(f"payload.data.issues[{idx}] must be an object.")
            issue_id = issue.get("issue_id")
            title = issue.get("title")
            conclusion = issue.get("conclusion")
            confidence = issue.get("confidence")
            citation_ids = issue.get("citation_ids")
            if not isinstance(issue_id, str) or not issue_id.strip():
                raise ValueError(f"payload.data.issues[{idx}].issue_id must be non-empty string.")
            if not isinstance(title, str) or not title.strip():
                raise ValueError(f"payload.data.issues[{idx}].title must be non-empty string.")
            if not isinstance(conclusion, str) or not conclusion.strip():
                raise ValueError(f"payload.data.issues[{idx}].conclusion must be non-empty string.")
            if not isinstance(confidence, str) or not confidence.strip():
                raise ValueError(f"payload.data.issues[{idx}].confidence must be non-empty string.")
            if not isinstance(citation_ids, list) or any(
                not isinstance(citation_id, str) or not citation_id.strip()
                for citation_id in citation_ids
            ):
                raise ValueError(
                    f"payload.data.issues[{idx}].citation_ids must be list of non-empty strings."
                )

        for idx, citation in enumerate(citations, start=1):
            if not isinstance(citation, dict):
                raise ValueError(f"payload.data.citations[{idx}] must be an object.")
            required_text_fields = ["citation_id", "kind", "law_name", "article", "title", "quote"]
            for field_name in required_text_fields:
                field_value = citation.get(field_name)
                if not isinstance(field_value, str) or not field_value.strip():
                    raise ValueError(
                        f"payload.data.citations[{idx}].{field_name} must be non-empty string."
                    )

            source = citation.get("source")
            if not isinstance(source, dict):
                raise ValueError(f"payload.data.citations[{idx}].source must be object.")
            tool_name = source.get("tool_name")
            query = source.get("query")
            if not isinstance(tool_name, str) or not tool_name.strip():
                raise ValueError(
                    f"payload.data.citations[{idx}].source.tool_name must be non-empty string."
                )
            validate_mcp_service_name(
                tool_name.strip(),
                allowed_service_names=self.allowed_tool_names,
            )
            if not isinstance(query, str) or not query.strip():
                raise ValueError(
                    f"payload.data.citations[{idx}].source.query must be non-empty string."
                )

        return raw_data

    def _parse_mcp_query_string(self, text: str) -> list[ToolCall]:
        calls: list[ToolCall] = []
        stripped = text.strip()
        if not stripped:
            return calls
        matches = list(MCP_CALL_RE.finditer(stripped))
        for match in matches:
            calls.append(
                ToolCall(
                    tool_name=match.group("tool").strip(),
                    input=match.group("input").strip(),
                )
            )
        return calls

    def _normalize_tool_item(self, item: Any) -> list[ToolCall]:
        calls: list[ToolCall] = []
        if isinstance(item, dict):
            tool_name = (
                item.get("tool_name")
                or item.get("toolname")
                or item.get("name")
                or item.get("service_name")
            )
            tool_input = item.get("input") or item.get("query") or item.get("text")
            if tool_name and tool_input:
                calls.append(ToolCall(str(tool_name).strip(), str(tool_input).strip()))
                return calls
            for key, value in item.items():
                key_str = str(key).strip()

                if isinstance(value, str):
                    # New template may use placeholder keys (toolname1/toolname2...).
                    # In that case, parse actual mcp_query(...) from value.
                    if key_str in self.allowed_tool_names:
                        calls.append(ToolCall(key_str, value.strip()))
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

    def _normalize_tool_calls(self, payload: dict[str, Any]) -> list[ToolCall]:
        raw_tool = payload.get("tool")
        calls: list[ToolCall] = []

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

    def _render_tool_history_round(
        self,
        round_index: int,
        calls: list[ToolCall],
        results: list[str],
    ) -> str:
        lines: list[str] = [f"[Round {round_index}] Tool Call(s)"]
        for idx, (call, result_text) in enumerate(zip(calls, results), start=1):
            lines.append(f"{idx}. mcp_query(\"{call.tool_name}\", \"{call.input}\")")
            lines.append("result:")
            lines.append(result_text)
        return "\n".join(lines).strip()

    def run_turn(
        self,
        scenario_agent_input: str,
        tool_call_history: str,
        template_path: str = LEGAL_ANALYSIS_TEMPLATE_DEFAULT,
        on_token: Optional[Callable[[str], None]] = None,
        **kwargs: Any,
    ) -> LegalAnalysisTurnResult:
        injected_prompt = self.build_user_prompt(
            scenario_agent_input=scenario_agent_input,
            tool_call_history=tool_call_history,
            template_path=template_path,
        )
        system_prompt = self.main_prompt if self.main_prompt.strip() else None
        agent_reply = llm_call.invoke_llm(
            self.llm_callable,
            user_prompt=injected_prompt,
            system_prompt=system_prompt,
            model=self.model,
            on_token=on_token,
            **kwargs,
        )
        payload = self._extract_json_payload(agent_reply)
        askmore = self._extract_askmore(payload)
        query = self._extract_query(payload)
        tool_calls = self._normalize_tool_calls(payload)
        if askmore == "yes" and not tool_calls and not query:
            raise ValueError('askmore=yes requires at least one of "query" or "tool".')
        if askmore in {"no", "end"}:
            analysis = payload.get("analysis")
            if not isinstance(analysis, str) or not analysis.strip():
                raise ValueError(f'askmore={askmore} requires non-empty "analysis".')
            self._validate_data_object(payload)
        return LegalAnalysisTurnResult(
            injected_prompt=injected_prompt,
            agent_reply=agent_reply,
            parsed_output=payload,
            askmore=askmore,
            query=query,
            tool_calls=tool_calls,
            tool_call_history=tool_call_history,
        )

    def run_until_done(
        self,
        scenario_agent_input: str,
        template_path: str = LEGAL_ANALYSIS_TEMPLATE_DEFAULT,
        initial_tool_call_history: str = "",
        max_rounds: int = DEFAULT_MAX_ROUNDS,
        on_token: Optional[Callable[[str], None]] = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        tool_call_history = initial_tool_call_history.strip()
        trace: list[dict[str, Any]] = []

        for round_index in range(1, max_rounds + 1):
            turn = self.run_turn(
                scenario_agent_input=scenario_agent_input,
                tool_call_history=tool_call_history,
                template_path=template_path,
                on_token=on_token,
                **kwargs,
            )
            trace.append(
                {
                    "round": round_index,
                    "agent_reply": turn.agent_reply,
                    "askmore": turn.askmore,
                    "tool_calls": [call.__dict__ for call in turn.tool_calls],
                }
            )

            if turn.askmore in {"no", "end"}:
                return {
                    "final_output": turn.parsed_output,
                    "tool_call_history": tool_call_history,
                    "rounds": round_index,
                    "trace": trace,
                }
            if turn.askmore == "yes" and not turn.tool_calls:
                return {
                    "final_output": turn.parsed_output,
                    "tool_call_history": tool_call_history,
                    "rounds": round_index,
                    "trace": trace,
                }

            results: list[str] = []
            for call in turn.tool_calls:
                result_text = self.mcp_query_callable(call.tool_name, call.input)
                results.append(result_text)
            round_block = self._render_tool_history_round(round_index, turn.tool_calls, results)
            tool_call_history = (
                f"{tool_call_history}\n\n{round_block}".strip()
                if tool_call_history
                else round_block
            )

        raise RuntimeError(
            f"LegalAnalysisAgent exceeded max_rounds={max_rounds} without askmore=no."
        )

    def chat_console(
        self,
        scenario_agent_input: str,
        template_path: str = LEGAL_ANALYSIS_TEMPLATE_DEFAULT,
        initial_tool_call_history: str = "",
        max_rounds: int = DEFAULT_MAX_ROUNDS,
        output_func: Callable[[str], None] = print,
        **kwargs: Any,
    ) -> dict[str, Any]:
        result = self.run_until_done(
            scenario_agent_input=scenario_agent_input,
            template_path=template_path,
            initial_tool_call_history=initial_tool_call_history,
            max_rounds=max_rounds,
            **kwargs,
        )
        output_func("Final JSON:")
        output_func(json.dumps(result["final_output"], ensure_ascii=False, indent=2))
        output_func("Tool Call History:")
        output_func(result["tool_call_history"] or "(empty)")
        return result
