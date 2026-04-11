from __future__ import annotations

from dataclasses import dataclass, field
import re
from typing import Any


MCP_CALL_RE = re.compile(
    r'mcp_query\(\s*["“](?P<tool>[^"”]+)["”]\s*,\s*["“](?P<input>[^"”]+)["”]\s*\)'
)


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


def _require_askmore(payload: dict[str, Any]) -> str:
    askmore = payload.get("askmore")
    if not isinstance(askmore, str):
        raise ValueError('payload.askmore must be a string "yes" or "no"')
    normalized = askmore.strip().lower()
    if normalized not in {"yes", "no"}:
        raise ValueError('payload.askmore must be "yes" or "no"')
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
        return [
            DisplayBlock(
                kind="agent_message",
                title="ControllerAgent 追问",
                text=ask,
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


def adapt_scenario_payload(payload: dict[str, Any]) -> list[DisplayBlock]:
    askmore = _require_askmore(payload)

    if askmore == "yes":
        ask = _require_text(payload.get("ask"), "payload.ask")
        return [
            DisplayBlock(
                kind="agent_message",
                title="ScenarioAgent 追问",
                text=ask,
            )
        ]

    analysis = payload.get("analysis")
    if not isinstance(analysis, dict):
        raise ValueError("payload.analysis must be an object when askmore=no")

    scene_id = _require_text(analysis.get("scene_id"), "payload.analysis.scene_id")
    current_status = _require_text(analysis.get("current_status"), "payload.analysis.current_status")
    user_appeal = _require_text(analysis.get("user_appeal"), "payload.analysis.user_appeal")
    faced_problems = _require_text(analysis.get("faced_problems"), "payload.analysis.faced_problems")
    route_plan = _require_text(analysis.get("route_plan"), "payload.analysis.route_plan")

    tool_calls = _extract_tool_calls(payload.get("tool"))
    blocks: list[DisplayBlock] = [
        DisplayBlock(
            kind="agent_message",
            title="ScenarioAgent 分析结果",
            text="场景阶段已完成，准备执行工具调用并移交法律分析阶段。",
            metadata={"scene_id": scene_id},
        ),
        DisplayBlock(kind="section", title="当前状态", text=current_status),
        DisplayBlock(kind="section", title="用户诉求", text=user_appeal),
        DisplayBlock(kind="section", title="核心问题", text=faced_problems),
        DisplayBlock(kind="section", title="场景内处理计划", text=route_plan),
    ]
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
    askmore = _require_askmore(payload)

    if askmore == "yes":
        tool_calls = _extract_tool_calls(payload.get("tool"))
        blocks = [
            DisplayBlock(
                kind="agent_message",
                title="LegalAnalysisAgent 工具补证请求",
                text="法律分析阶段需要继续检索工具依据。",
            )
        ]
        if tool_calls:
            blocks.append(
                DisplayBlock(
                    kind="tool_plan",
                    title="计划调用的工具",
                    items=tool_calls,
                )
            )
        return blocks

    analysis_markdown = _require_text(payload.get("analysis"), "payload.analysis")
    return [
        DisplayBlock(
            kind="legal_report",
            title="LegalAnalysisAgent 最终分析",
            text=analysis_markdown,
        )
    ]


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


def adapt_payload_by_agent(agent_name: str, payload: dict[str, Any]) -> list[DisplayBlock]:
    normalized = agent_name.strip()
    if normalized == "ControllerAgent":
        return adapt_controller_payload(payload)
    if normalized == "ScenarioAgent":
        return adapt_scenario_payload(payload)
    if normalized == "LegalAnalysisAgent":
        return adapt_legal_payload(payload)
    raise ValueError(f"unsupported agent_name: {agent_name}")
