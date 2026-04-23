from __future__ import annotations

from dataclasses import dataclass, field
import json
import re
from typing import Any


MCP_CALL_RE = re.compile(
    r'mcp_query\(\s*["“](?P<tool>[^"”]+)["”]\s*,\s*["“](?P<input>[^"”]+)["”]\s*\)'
)


# 业务场景 scene_id → 中文 Agent 名称
_SCENE_AGENT_NAMES: dict[str, str] = {
    "recruitment_probation":      "招聘入职Agent",
    "adjustment_transfer":        "调岗调薪Agent",
    "performance_discipline":     "绩效违纪Agent",
    "salary_overtime_social":     "薪资社保Agent",
    "leave_medical_period":       "假期医疗Agent",
    "female_protection":          "女职工保护Agent",
    "work_injury":                "工伤认定Agent",
    "termination_layoff":         "离职裁员Agent",
    "noncompete_confidentiality": "竞业保密Agent",
    "dispute_arbitration":        "争议仲裁Agent",
    "rules_policy_effectiveness": "制度效力Agent",
    "flexible_employment_relationship": "灵活用工Agent",
    "flexible_platform_employment": "平台用工Agent",
    "law_case_research": "法研检索Agent",
    "legal_qa_proxy": "律师问答代理Agent",
    "evidence_doc_generator": "证据文书Agent",
}


def _scene_agent_name(scene_id: str, suffix: str = "") -> str:
    """Return a human-readable agent name for the given scene_id."""
    base = _SCENE_AGENT_NAMES.get((scene_id or "").strip(), "场景分析Agent")
    return f"{base} {suffix}".strip()


@dataclass(frozen=True)
class DisplayBlock:
    kind: str
    title: str = ""
    text: str = ""
    items: list[str] = field(default_factory=list)
    metadata: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "title": self.title,
            "text": self.text,
            "items": list(self.items),
            "metadata": dict(self.metadata),
        }


def blocks_to_dicts(blocks: list[DisplayBlock]) -> list[dict[str, Any]]:
    return [block.to_dict() for block in blocks]


def build_handoff_block(from_agent: str, to_agent: str, *, scene_id: str = "") -> DisplayBlock:
    text = f"{from_agent} 已完成阶段任务，控制权移交至 {to_agent}。"
    metadata: dict[str, str] = {"from_agent": from_agent, "to_agent": to_agent}
    if scene_id.strip():
        text = f"{text} 场景：{scene_id.strip()}。"
        metadata["scene_id"] = scene_id.strip()
    return DisplayBlock(kind="handoff", title="Agent 控制权移交", text=text, metadata=metadata)


def _serialize_reasoning(reasoning: Any) -> dict[str, str]:
    """把 reasoning 字段（dict 或 str）序列化到 metadata，供前端解析。"""
    if not reasoning:
        return {}
    if isinstance(reasoning, dict):
        return {"reasoning": json.dumps(reasoning, ensure_ascii=False)}
    if isinstance(reasoning, str) and reasoning.strip():
        return {"reasoning": json.dumps({"judgment": reasoning.strip(), "citations": []}, ensure_ascii=False)}
    return {}


def _serialize_legal_trace_data(raw_data: Any) -> dict[str, str]:
    if raw_data is None:
        return {}
    if not isinstance(raw_data, dict):
        raise ValueError("payload.data must be an object when provided")
    return {"legal_trace_data": json.dumps(raw_data, ensure_ascii=False)}


def _require_askmore(payload: dict[str, Any], allowed: set[str] | None = None) -> str:
    valid_values = allowed or {"yes", "no"}
    askmore = payload.get("askmore")
    if not isinstance(askmore, str):
        allowed_text = " | ".join(sorted(valid_values))
        raise ValueError(f'payload.askmore must be a string in [{allowed_text}]')
    normalized = askmore.strip().lower()
    if normalized not in valid_values:
        allowed_text = " | ".join(sorted(valid_values))
        raise ValueError(f'payload.askmore must be in [{allowed_text}]')
    return normalized


def _require_text(value: Any, field_name: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field_name} must be a string")
    normalized = value.strip()
    if not normalized:
        raise ValueError(f"{field_name} must be non-empty")
    return normalized


def _extract_tool_calls(raw_tool: Any) -> list[str]:
    calls: list[str] = []

    if raw_tool is None:
        return calls

    if isinstance(raw_tool, dict):
        for key, value in raw_tool.items():
            if isinstance(value, str):
                matches = list(MCP_CALL_RE.finditer(value))
                if matches:
                    for match in matches:
                        calls.append(
                            f'mcp_query("{match.group("tool").strip()}", "{match.group("input").strip()}")'
                        )
                else:
                    calls.append(f'{key}: {value.strip()}')
            elif isinstance(value, dict) or isinstance(value, list):
                calls.extend(_extract_tool_calls(value))
        return calls

    if isinstance(raw_tool, list):
        for item in raw_tool:
            calls.extend(_extract_tool_calls(item))
        return calls

    if isinstance(raw_tool, str):
        matches = list(MCP_CALL_RE.finditer(raw_tool))
        if matches:
            for match in matches:
                calls.append(
                    f'mcp_query("{match.group("tool").strip()}", "{match.group("input").strip()}")'
                )
        else:
            text = raw_tool.strip()
            if text:
                calls.append(text)
        return calls

    calls.append(str(raw_tool))
    return calls


def adapt_controller_payload(payload: dict[str, Any]) -> list[DisplayBlock]:
    askmore = _require_askmore(payload)

    if askmore == "yes":
        ask = _require_text(payload.get("ask"), "payload.ask")
        metadata = _serialize_reasoning(payload.get("reasoning"))
        return [
            DisplayBlock(
                kind="agent_message",
                title="ControllerAgent 追问",
                text=ask,
                metadata=metadata,
            )
        ]

    user_input = _require_text(payload.get("user_input"), "payload.user_input")
    analysis = payload.get("analysis")
    if not isinstance(analysis, dict):
        raise ValueError("payload.analysis must be an object when askmore=no")

    scene_id = _require_text(analysis.get("scene_id"), "payload.analysis.scene_id")
    confidence_level = _require_text(
        analysis.get("confidence_level"), "payload.analysis.confidence_level"
    )
    escalation_flags = _require_text(
        analysis.get("escalation_flags"), "payload.analysis.escalation_flags"
    )
    current_status = _require_text(analysis.get("current_status"), "payload.analysis.current_status")
    user_appeal = _require_text(analysis.get("user_appeal"), "payload.analysis.user_appeal")
    faced_problems = _require_text(analysis.get("faced_problems"), "payload.analysis.faced_problems")
    route_plan = _require_text(analysis.get("route_plan"), "payload.analysis.route_plan")

    blocks: list[DisplayBlock] = [
        DisplayBlock(
            kind="agent_message",
            title="ControllerAgent 路由结果",
            text="主控阶段已完成，准备进入场景分析阶段。",
            metadata={
                "scene_id": scene_id,
                "confidence_level": confidence_level,
                "escalation_flags": escalation_flags,
            },
        ),
        DisplayBlock(kind="section", title="用户输入", text=user_input),
        DisplayBlock(kind="section", title="当前状态", text=current_status),
        DisplayBlock(kind="section", title="用户诉求", text=user_appeal),
        DisplayBlock(kind="section", title="核心问题", text=faced_problems),
        DisplayBlock(kind="section", title="路由计划", text=route_plan),
    ]
    return blocks


def adapt_scenario_payload(payload: dict[str, Any], scene_id: str = "") -> list[DisplayBlock]:
    askmore = _require_askmore(payload)

    if askmore == "yes":
        ask = _require_text(payload.get("ask"), "payload.ask")
        metadata = _serialize_reasoning(payload.get("reasoning"))
        return [
            DisplayBlock(
                kind="agent_message",
                title=_scene_agent_name(scene_id, "追问"),
                text=ask,
                metadata=metadata,
            )
        ]
    analysis = payload.get("analysis")
    if not isinstance(analysis, dict):
        raise ValueError("payload.analysis must be an object when askmore=no")

    scene_id = _require_text(analysis.get("scene_id"), "payload.analysis.scene_id")

    tool_calls = _extract_tool_calls(payload.get("tool"))
    blocks: list[DisplayBlock] = [
        DisplayBlock(
            kind="agent_message",
            title=_scene_agent_name(scene_id, "分析结果"),
            text="场景阶段已完成，准备执行工具调用并移交法律分析阶段。",
            metadata={"scene_id": scene_id},
        ),
    ]

    # Primary schema (legacy/common scenario templates)
    current_status = str(analysis.get("current_status", "")).strip()
    user_appeal = str(analysis.get("user_appeal", "")).strip()
    faced_problems = str(analysis.get("faced_problems", "")).strip()
    route_plan = str(analysis.get("route_plan", "")).strip()
    if current_status:
        blocks.append(DisplayBlock(kind="section", title="当前状态", text=current_status))
    if user_appeal:
        blocks.append(DisplayBlock(kind="section", title="用户诉求", text=user_appeal))
    if faced_problems:
        blocks.append(DisplayBlock(kind="section", title="核心问题", text=faced_problems))
    if route_plan:
        blocks.append(DisplayBlock(kind="section", title="场景内处理计划", text=route_plan))

    # Lawyer-side schema compatibility
    if not any([current_status, user_appeal, faced_problems, route_plan]):
        case_summary = str(analysis.get("case_summary", "")).strip()
        represented_party = str(analysis.get("represented_party", "")).strip()
        proxy_scene_id = str(analysis.get("proxy_scene_id", "")).strip()
        strategy_notes = str(analysis.get("strategy_notes", "")).strip()
        missing_info_note = str(analysis.get("missing_info_note", "")).strip()
        default_params_applied = str(analysis.get("default_params_applied", "")).strip()

        if case_summary:
            blocks.append(DisplayBlock(kind="section", title="案件摘要", text=case_summary))
        if represented_party or proxy_scene_id:
            detail = "｜".join([item for item in [represented_party, proxy_scene_id] if item])
            if detail:
                blocks.append(DisplayBlock(kind="section", title="代理与路由", text=detail))
        if default_params_applied:
            blocks.append(DisplayBlock(kind="section", title="默认参数", text=default_params_applied))
        if strategy_notes:
            blocks.append(DisplayBlock(kind="section", title="策略提示", text=strategy_notes))
        if missing_info_note:
            blocks.append(DisplayBlock(kind="section", title="待补信息说明", text=missing_info_note))

    if len(blocks) == 1:
        blocks.append(
            DisplayBlock(
                kind="section",
                title="场景输出摘要",
                text=json.dumps(analysis, ensure_ascii=False)[:1200],
            )
        )
    if tool_calls:
        blocks.append(
            DisplayBlock(
                kind="tool_plan",
                title="计划调用的工具",
                items=tool_calls,
            )
        )
    return blocks


def adapt_legal_payload(payload: dict[str, Any]) -> list[DisplayBlock]:
    askmore = _require_askmore(payload, allowed={"yes", "no", "end"})
    tool_calls = _extract_tool_calls(payload.get("tool"))
    query = str(payload.get("query", "")).strip()
    analysis_markdown = str(payload.get("analysis", "")).strip()
    trace_metadata = _serialize_legal_trace_data(payload.get("data"))
    metadata = _serialize_reasoning(payload.get("reasoning"))
    blocks: list[DisplayBlock] = []

    if askmore in {"no", "end"}:
        if not analysis_markdown:
            raise ValueError(f"legal payload askmore={askmore} requires non-empty analysis")
        if "legal_trace_data" not in trace_metadata:
            raise ValueError(f"legal payload askmore={askmore} requires data object")

    if query:
        title = "LegalAnalysisAgent 回复"
        if askmore == "yes":
            title = "LegalAnalysisAgent 追问/补充"
        elif askmore == "end":
            title = "LegalAnalysisAgent 结束说明"
        blocks.append(
            DisplayBlock(
                kind="agent_message",
                title=title,
                text=query,
                metadata=metadata,
            )
        )

    if tool_calls:
        blocks.append(
            DisplayBlock(
                kind="tool_plan",
                title="计划调用的工具",
                items=tool_calls,
            )
        )

    if analysis_markdown:
        legal_report_metadata = dict(trace_metadata)
        blocks.append(
            DisplayBlock(
                kind="legal_report",
                title="LegalAnalysisAgent 最终分析",
                text=analysis_markdown,
                metadata=legal_report_metadata,
            )
        )

    if not blocks:
        raise ValueError("legal payload must include at least one of query/tool/analysis")
    return blocks


def adapt_tool_history_text(tool_history: str) -> list[DisplayBlock]:
    text = tool_history.strip()
    if not text:
        return []

    calls = [m.group(0) for m in MCP_CALL_RE.finditer(text)]
    blocks: list[DisplayBlock] = []

    if calls:
        blocks.append(
            DisplayBlock(
                kind="tool_result_card",
                title="工具调用总览",
                text=f"共识别到 {len(calls)} 次工具调用。",
                items=calls,
                metadata={"card_variant": "tool_summary"},
            )
        )

        parts = re.split(r"(?=\d+\.\s*mcp_query\()", text)
        for part in parts:
            section = part.strip()
            if not section:
                continue
            first_match = MCP_CALL_RE.search(section)
            if not first_match:
                continue
            call_text = first_match.group(0)
            tool_name = first_match.group("tool").strip()
            query_text = first_match.group("input").strip()

            result_text = ""
            if "result:" in section:
                result_text = section.split("result:", 1)[1].strip()
            result_preview = result_text
            if len(result_preview) > 400:
                result_preview = result_preview[:400].rstrip() + "..."

            blocks.append(
                DisplayBlock(
                    kind="tool_result_card",
                    title=f"工具结果：{tool_name}",
                    text=result_preview or "(empty result)",
                    items=[call_text, f"query: {query_text}"],
                    metadata={"card_variant": "tool_result", "tool_name": tool_name},
                )
            )

        blocks.append(
            DisplayBlock(
                kind="tool_history",
                title="工具调用记录",
                items=calls,
            )
        )
    else:
        blocks.append(
            DisplayBlock(
                kind="tool_history",
                title="工具调用记录",
                text=text,
            )
        )
    return blocks


def adapt_payload_by_agent(agent_name: str, payload: dict[str, Any], scene_id: str = "") -> list[DisplayBlock]:
    normalized = agent_name.strip()
    if normalized == "ControllerAgent":
        return adapt_controller_payload(payload)
    if normalized == "ScenarioAgent":
        return adapt_scenario_payload(payload, scene_id=scene_id)
    if normalized == "LegalAnalysisAgent":
        return adapt_legal_payload(payload)
    raise ValueError(f"unsupported agent_name: {agent_name}")
