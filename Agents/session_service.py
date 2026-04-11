from __future__ import annotations

from dataclasses import dataclass, field
import json
from pathlib import Path
from typing import Any, Callable
from uuid import uuid4

from .agent import Agent
from .conversation_contract import (
    ConversationMessagePayload,
    HandoffPayload,
    RenderBlockPayload,
    SessionTurnResponse,
    build_handoff_payload,
    build_message_payload,
    now_utc_iso,
)
from .legal_analysis_agent import LEGAL_ANALYSIS_TEMPLATE_DEFAULT, LegalAnalysisAgent
from .module_services import run_direct_module_service, supports_direct_module_service
from .presentation_adapter import (
    DisplayBlock,
    adapt_payload_by_agent,
    adapt_tool_history_text,
    build_handoff_block,
)
from .scene_catalog import (
    get_scene_template_filename,
    list_module_scene_hints,
    list_role_modules,
    validate_module_key,
    validate_role_id,
    validate_scene_id,
)
from .scenario_agent import DEFAULT_MCP_RETRIES, SCENARIO_TEMPLATE_ROOT, ScenarioAgent


PROJECT_ROOT = Path(__file__).resolve().parents[1]
CONTROLLER_TEMPLATE_DEFAULT = str(PROJECT_ROOT / "Prompt_Template/ControllerAgent.md")
LEGAL_TEMPLATE_DEFAULT = str(PROJECT_ROOT / LEGAL_ANALYSIS_TEMPLATE_DEFAULT)
SESSION_STORAGE_DEFAULT = PROJECT_ROOT / "storage" / "sessions"
REJECTED_TERMINATION_PREFIX = (
    "用户拒绝了本次Agent的终止，也许是还有需要澄清的地方，这次的输入如下："
)


def _strict_json_object(text: str) -> dict[str, Any]:
    payload = json.loads(text)
    if not isinstance(payload, dict):
        raise ValueError("agent output must be a JSON object")
    return payload


def _extract_askmore(payload: dict[str, Any]) -> str:
    value = payload.get("askmore")
    if not isinstance(value, str):
        raise ValueError('payload.askmore must be a string "yes" or "no"')
    normalized = value.strip().lower()
    if normalized not in {"yes", "no"}:
        raise ValueError('payload.askmore must be "yes" or "no"')
    return normalized


def _to_render_blocks(blocks: list[Any]) -> list[RenderBlockPayload]:
    out: list[RenderBlockPayload] = []
    for block in blocks:
        kind = str(getattr(block, "kind")).strip()
        title = str(getattr(block, "title", "")).strip()
        text = str(getattr(block, "text", "")).strip()
        items = list(getattr(block, "items", []) or [])
        metadata = dict(getattr(block, "metadata", {}) or {})
        out.append(
            RenderBlockPayload(
                kind=kind,
                title=title,
                text=text,
                items=[str(i) for i in items],
                metadata={str(k): str(v) for k, v in metadata.items()},
            )
        )
    return out


def _message_from_dict(data: dict[str, Any]) -> ConversationMessagePayload:
    raw_blocks = data.get("display_blocks")
    if not isinstance(raw_blocks, list):
        raise ValueError("message.display_blocks must be list")
    blocks: list[RenderBlockPayload] = []
    for raw_block in raw_blocks:
        if not isinstance(raw_block, dict):
            raise ValueError("message.display_blocks item must be object")
        blocks.append(
            RenderBlockPayload(
                kind=str(raw_block.get("kind", "")),
                title=str(raw_block.get("title", "")),
                text=str(raw_block.get("text", "")),
                items=[str(i) for i in list(raw_block.get("items", []) or [])],
                metadata={
                    str(k): str(v)
                    for k, v in dict(raw_block.get("metadata", {}) or {}).items()
                },
            )
        )
    return ConversationMessagePayload(
        session_id=str(data["session_id"]),
        turn_id=int(data["turn_id"]),
        speaker_type=str(data["speaker_type"]),
        speaker_agent=str(data["speaker_agent"]),
        role_id=str(data["role_id"]),
        created_at_utc=str(data["created_at_utc"]),
        display_blocks=blocks,
    )


def _handoff_from_dict(data: dict[str, Any]) -> HandoffPayload:
    return HandoffPayload(
        session_id=str(data["session_id"]),
        turn_id=int(data["turn_id"]),
        from_agent=str(data["from_agent"]),
        to_agent=str(data["to_agent"]),
        reason=str(data["reason"]),
        created_at_utc=str(data["created_at_utc"]),
        scene_id=str(data.get("scene_id", "")),
    )


def _extract_module_key(attachments_meta: Any | None) -> str:
    if not isinstance(attachments_meta, dict):
        return ""
    raw_module_key = attachments_meta.get("module_key")
    if raw_module_key is None:
        return ""
    if not isinstance(raw_module_key, str):
        raise ValueError("attachments_meta.module_key must be string")
    return validate_module_key(raw_module_key.strip())


@dataclass
class SessionState:
    session_id: str
    role_id: str
    controller_agent: Agent
    scenario_agent: ScenarioAgent
    legal_analysis_agent: LegalAnalysisAgent
    controller_template_path: str
    legal_template_path: str
    scenario_template_root: Path
    created_at_utc: str = field(default_factory=now_utc_iso)
    updated_at_utc: str = field(default_factory=now_utc_iso)
    stage: str = "controller"  # controller | scenario | legal | done
    turn_id: int = 0
    scene_id: str = ""
    scenario_template_path: str = ""
    scenario_output: dict[str, Any] | None = None
    scenario_tool_history: str = ""
    pending_transition: dict[str, Any] | None = None
    pending_stage: str = ""
    pending_payload: dict[str, Any] = field(default_factory=dict)
    inject_rejection_prefix_on_next_turn: bool = False
    current_module_key: str = ""
    messages: list[Any] = field(default_factory=list)
    handoffs: list[HandoffPayload] = field(default_factory=list)
    event_log: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class MultiAgentSessionService:
    controller_factory: Callable[[], Agent] = field(default_factory=lambda: (lambda: Agent(main_prompt="")))
    scenario_factory: Callable[[], ScenarioAgent] = field(
        default_factory=lambda: (lambda: ScenarioAgent(main_prompt=""))
    )
    legal_factory: Callable[[], LegalAnalysisAgent] = field(
        default_factory=lambda: (lambda: LegalAnalysisAgent(main_prompt=""))
    )
    controller_template_path: str = CONTROLLER_TEMPLATE_DEFAULT
    legal_template_path: str = LEGAL_TEMPLATE_DEFAULT
    scenario_template_root: Path = field(default_factory=lambda: Path(SCENARIO_TEMPLATE_ROOT))
    scenario_tool_retry_times: int = DEFAULT_MCP_RETRIES
    legal_max_rounds: int = 8
    storage_root: Path = field(default_factory=lambda: SESSION_STORAGE_DEFAULT)
    sessions: dict[str, SessionState] = field(default_factory=dict, init=False)

    def __post_init__(self) -> None:
        self.scenario_template_root = self.scenario_template_root.resolve()
        self.storage_root = self.storage_root.resolve()
        self.storage_root.mkdir(parents=True, exist_ok=True)
        self._load_sessions_from_storage()

    def _session_snapshot_path(self, session_id: str) -> Path:
        return self.storage_root / f"{session_id}.json"

    def _state_snapshot(self, state: SessionState) -> dict[str, Any]:
        return {
            "session_id": state.session_id,
            "role_id": state.role_id,
            "stage": state.stage,
            "created_at_utc": state.created_at_utc,
            "updated_at_utc": state.updated_at_utc,
            "turn_id": state.turn_id,
            "scene_id": state.scene_id,
            "scenario_template_path": state.scenario_template_path,
            "scenario_output": state.scenario_output,
            "scenario_tool_history": state.scenario_tool_history,
            "controller_template_path": state.controller_template_path,
            "legal_template_path": state.legal_template_path,
            "scenario_template_root": str(state.scenario_template_root),
            "pending_transition": state.pending_transition,
            "pending_stage": state.pending_stage,
            "pending_payload": state.pending_payload,
            "inject_rejection_prefix_on_next_turn": state.inject_rejection_prefix_on_next_turn,
            "current_module_key": state.current_module_key,
            "messages": [message.to_dict() for message in state.messages],
            "handoffs": [handoff.to_dict() for handoff in state.handoffs],
            "event_log": [dict(item) for item in state.event_log],
            "controller_conversation_messages": [
                [str(role), str(content)]
                for role, content in state.controller_agent.conversation_messages
            ],
            "scenario_conversation_messages": [
                [str(role), str(content)]
                for role, content in state.scenario_agent.conversation_messages
            ],
        }

    def _persist_state(self, state: SessionState) -> None:
        state.updated_at_utc = now_utc_iso()
        snapshot = self._state_snapshot(state)
        snapshot_path = self._session_snapshot_path(state.session_id)
        snapshot_path.write_text(
            json.dumps(snapshot, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def _load_sessions_from_storage(self) -> None:
        for snapshot_path in sorted(self.storage_root.glob("*.json")):
            snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
            if not isinstance(snapshot, dict):
                raise ValueError(f"invalid snapshot format: {snapshot_path}")

            session_id = str(snapshot["session_id"])
            role_id = validate_role_id(str(snapshot["role_id"]))
            controller_agent = self.controller_factory()
            scenario_agent = self.scenario_factory()
            legal_agent = self.legal_factory()

            raw_controller_messages = list(snapshot.get("controller_conversation_messages", []))
            raw_scenario_messages = list(snapshot.get("scenario_conversation_messages", []))
            controller_agent.conversation_messages = [
                (str(item[0]), str(item[1]))
                for item in raw_controller_messages
                if isinstance(item, list) and len(item) == 2
            ]
            scenario_agent.conversation_messages = [
                (str(item[0]), str(item[1]))
                for item in raw_scenario_messages
                if isinstance(item, list) and len(item) == 2
            ]

            raw_messages = list(snapshot.get("messages", []))
            raw_handoffs = list(snapshot.get("handoffs", []))
            messages = [_message_from_dict(item) for item in raw_messages if isinstance(item, dict)]
            handoffs = [_handoff_from_dict(item) for item in raw_handoffs if isinstance(item, dict)]

            state = SessionState(
                session_id=session_id,
                role_id=role_id,
                controller_agent=controller_agent,
                scenario_agent=scenario_agent,
                legal_analysis_agent=legal_agent,
                controller_template_path=str(
                    snapshot.get("controller_template_path", self.controller_template_path)
                ),
                legal_template_path=str(
                    snapshot.get("legal_template_path", self.legal_template_path)
                ),
                scenario_template_root=Path(
                    str(snapshot.get("scenario_template_root", self.scenario_template_root))
                ).resolve(),
                stage=str(snapshot.get("stage", "controller")),
                created_at_utc=str(snapshot.get("created_at_utc", now_utc_iso())),
                updated_at_utc=str(snapshot.get("updated_at_utc", now_utc_iso())),
                turn_id=int(snapshot.get("turn_id", 0)),
                scene_id=str(snapshot.get("scene_id", "")),
                scenario_template_path=str(snapshot.get("scenario_template_path", "")),
                scenario_output=snapshot.get("scenario_output"),
                scenario_tool_history=str(snapshot.get("scenario_tool_history", "")),
                pending_transition=snapshot.get("pending_transition"),
                pending_stage=str(snapshot.get("pending_stage", "")),
                pending_payload=dict(snapshot.get("pending_payload", {}) or {}),
                inject_rejection_prefix_on_next_turn=bool(
                    snapshot.get("inject_rejection_prefix_on_next_turn", False)
                ),
                current_module_key=str(snapshot.get("current_module_key", "")),
                messages=messages,
                handoffs=handoffs,
                event_log=[
                    dict(item)
                    for item in list(snapshot.get("event_log", []))
                    if isinstance(item, dict)
                ],
            )
            scenario_agent.template_root = state.scenario_template_root
            self.sessions[session_id] = state

    def create_session(self, role_id: str) -> SessionState:
        role = validate_role_id(role_id)
        session_id = uuid4().hex
        scenario_agent = self.scenario_factory()
        scenario_agent.template_root = self.scenario_template_root
        state = SessionState(
            session_id=session_id,
            role_id=role,
            controller_agent=self.controller_factory(),
            scenario_agent=scenario_agent,
            legal_analysis_agent=self.legal_factory(),
            controller_template_path=self.controller_template_path,
            legal_template_path=self.legal_template_path,
            scenario_template_root=self.scenario_template_root.resolve(),
            created_at_utc=now_utc_iso(),
            updated_at_utc=now_utc_iso(),
        )
        self.sessions[session_id] = state
        self._persist_state(state)
        return state

    def get_session(self, session_id: str) -> SessionState:
        if session_id not in self.sessions:
            raise ValueError(f"session not found: {session_id}")
        return self.sessions[session_id]

    def _public_pending_transition(self, state: SessionState) -> dict[str, Any] | None:
        if not isinstance(state.pending_transition, dict):
            return None
        return {
            "from_agent": str(state.pending_transition.get("from_agent", "")),
            "to_agent": str(state.pending_transition.get("to_agent", "")),
            "reason": str(state.pending_transition.get("reason", "")),
            "scene_id": str(state.pending_transition.get("scene_id", "")),
            "trigger_turn_id": int(state.pending_transition.get("trigger_turn_id", state.turn_id)),
            "proposed_at": str(state.pending_transition.get("proposed_at", "")),
        }

    def _derive_session_title(self, state: SessionState) -> str:
        for message in state.messages:
            if getattr(message, "speaker_type", "") != "user":
                continue
            for block in list(getattr(message, "display_blocks", []) or []):
                text = str(getattr(block, "text", "")).strip()
                if text:
                    return text[:30]
        return "新会话"

    def _build_event(
        self,
        state: SessionState,
        event_type: str,
        payload: dict[str, Any],
        *,
        turn_id: int | None = None,
    ) -> dict[str, Any]:
        return {
            "event_id": uuid4().hex,
            "session_id": state.session_id,
            "turn_id": int(state.turn_id if turn_id is None else turn_id),
            "stage": state.stage,
            "event_type": event_type,
            "created_at_utc": now_utc_iso(),
            "payload": dict(payload),
        }

    def _append_event(
        self,
        state: SessionState,
        event_type: str,
        payload: dict[str, Any],
        collector: list[dict[str, Any]],
        *,
        turn_id: int | None = None,
    ) -> dict[str, Any]:
        event = self._build_event(state, event_type, payload, turn_id=turn_id)
        state.event_log.append(event)
        collector.append(event)
        self._persist_state(state)
        return event

    def _validate_role_module_access(self, role_id: str, module_key: str) -> None:
        role_modules = set(list_role_modules(role_id))
        if module_key not in role_modules:
            allowed = ", ".join(sorted(role_modules))
            raise ValueError(
                f"module_key={module_key} is not allowed for role_id={role_id}. allowed: {allowed}"
            )

    def _response(
        self,
        state: SessionState,
        *,
        active_agent: str,
        askmore: str,
        messages: list[ConversationMessagePayload],
        handoffs: list[HandoffPayload],
        events: list[dict[str, Any]],
    ) -> SessionTurnResponse:
        return SessionTurnResponse(
            session_id=state.session_id,
            role_id=state.role_id,
            active_agent=active_agent,
            askmore=askmore,
            messages=messages,
            handoffs=handoffs,
            pending_transition=self._public_pending_transition(state),
            requires_handoff_confirmation=bool(state.pending_transition),
            events=events,
        )

    def _try_run_direct_module_service(
        self,
        state: SessionState,
        user_input: str,
        attachments_meta: Any | None,
        pre_events: list[dict[str, Any]] | None = None,
    ) -> SessionTurnResponse | None:
        module_key = _extract_module_key(attachments_meta)
        if not module_key:
            return None
        self._validate_role_module_access(state.role_id, module_key)
        if not supports_direct_module_service(module_key):
            return None

        state.turn_id += 1
        new_messages: list[ConversationMessagePayload] = []
        new_events: list[dict[str, Any]] = list(pre_events or [])

        user_message = self._append_user_message(
            state,
            user_input.strip(),
            speaker_agent="EmployerModuleAgent",
            event_collector=new_events,
        )
        new_messages.append(user_message)

        service_result = run_direct_module_service(
            module_key=module_key,
            user_input=user_input,
            attachments_meta=attachments_meta,
        )
        agent_message = self._append_agent_blocks(
            state,
            service_result.active_agent,
            service_result.blocks,
            event_collector=new_events,
        )
        new_messages.append(agent_message)
        state.stage = "done"
        self._persist_state(state)
        return self._response(
            state,
            active_agent=service_result.active_agent,
            askmore=service_result.askmore,
            messages=new_messages,
            handoffs=[],
            events=new_events,
        )

    def _resolve_scenario_template(self, state: SessionState, scene_id: str) -> str:
        scene = validate_scene_id(scene_id)
        filename = get_scene_template_filename(scene)
        path = (state.scenario_template_root / filename).resolve()
        if not path.exists():
            raise FileNotFoundError(f"scenario template not found: {path}")
        return str(path)

    def _append_user_message(
        self,
        state: SessionState,
        user_input: str,
        speaker_agent: str,
        event_collector: list[dict[str, Any]],
    ) -> ConversationMessagePayload:
        blocks = [RenderBlockPayload(kind="user_message", title="用户输入", text=user_input.strip())]
        message = build_message_payload(
            session_id=state.session_id,
            turn_id=state.turn_id,
            speaker_type="user",
            speaker_agent=speaker_agent,
            role_id=state.role_id,
            display_blocks=blocks,
        )
        state.messages.append(message)
        self._append_event(
            state,
            "user_message",
            {
                "speaker_agent": speaker_agent,
                "text": user_input.strip(),
            },
            event_collector,
        )
        self._persist_state(state)
        return message

    def _append_agent_blocks(
        self,
        state: SessionState,
        agent_name: str,
        blocks: list[Any],
        event_collector: list[dict[str, Any]],
    ) -> ConversationMessagePayload:
        message = build_message_payload(
            session_id=state.session_id,
            turn_id=state.turn_id,
            speaker_type="agent",
            speaker_agent=agent_name,
            role_id=state.role_id,
            display_blocks=_to_render_blocks(blocks),
        )
        state.messages.append(message)
        preview = "\n".join(
            filter(None, [str(getattr(block, "title", "")).strip() for block in blocks])
        ).strip()
        self._append_event(
            state,
            "agent_message",
            {
                "speaker_agent": agent_name,
                "block_count": len(blocks),
                "preview": preview,
            },
            event_collector,
        )
        self._persist_state(state)
        return message

    def _append_handoff(
        self,
        state: SessionState,
        from_agent: str,
        to_agent: str,
        reason: str,
        event_collector: list[dict[str, Any]],
        scene_id: str = "",
    ) -> HandoffPayload:
        handoff = build_handoff_payload(
            session_id=state.session_id,
            turn_id=state.turn_id,
            from_agent=from_agent,
            to_agent=to_agent,
            reason=reason,
            scene_id=scene_id,
        )
        state.handoffs.append(handoff)
        handoff_block = build_handoff_block(from_agent, to_agent, scene_id=scene_id)
        self._append_agent_blocks(state, to_agent, [handoff_block], event_collector)
        self._append_event(
            state,
            "handoff_confirmed",
            {
                "from_agent": from_agent,
                "to_agent": to_agent,
                "reason": reason,
                "scene_id": scene_id,
            },
            event_collector,
        )
        self._persist_state(state)
        return handoff

    def _append_handoff_request_message(
        self,
        state: SessionState,
        from_agent: str,
        to_agent: str,
        reason: str,
        scene_id: str,
        event_collector: list[dict[str, Any]],
    ) -> ConversationMessagePayload:
        block = DisplayBlock(
            kind="handoff_request",
            title="等待用户确认移交",
            text=f"{from_agent} 已完成当前阶段，建议移交给 {to_agent}。请确认是否继续。",
            metadata={
                "from_agent": from_agent,
                "to_agent": to_agent,
                "scene_id": scene_id,
                "reason": reason,
            },
        )
        return self._append_agent_blocks(state, from_agent, [block], event_collector)

    def _set_pending_transition(
        self,
        state: SessionState,
        *,
        from_agent: str,
        to_agent: str,
        reason: str,
        scene_id: str,
        pending_stage: str,
        pending_payload: dict[str, Any],
        event_collector: list[dict[str, Any]],
    ) -> None:
        state.pending_transition = {
            "from_agent": from_agent,
            "to_agent": to_agent,
            "reason": reason,
            "scene_id": scene_id,
            "trigger_turn_id": state.turn_id,
            "proposed_at": now_utc_iso(),
        }
        state.pending_stage = pending_stage
        state.pending_payload = dict(pending_payload)
        self._append_event(
            state,
            "handoff_proposed",
            {
                "from_agent": from_agent,
                "to_agent": to_agent,
                "reason": reason,
                "scene_id": scene_id,
                "pending_stage": pending_stage,
            },
            event_collector,
        )
        self._persist_state(state)

    def _clear_pending_transition(self, state: SessionState) -> None:
        state.pending_transition = None
        state.pending_stage = ""
        state.pending_payload = {}
        self._persist_state(state)

    def _decorate_user_input_after_reject(
        self,
        state: SessionState,
        user_input: str,
        event_collector: list[dict[str, Any]],
    ) -> str:
        if not state.inject_rejection_prefix_on_next_turn:
            return user_input
        merged = f"{REJECTED_TERMINATION_PREFIX}{user_input}"
        state.inject_rejection_prefix_on_next_turn = False
        self._append_event(
            state,
            "rejected_termination_context_injected",
            {
                "prefix": REJECTED_TERMINATION_PREFIX,
                "original_user_input": user_input,
                "merged_input": merged,
            },
            event_collector,
        )
        self._persist_state(state)
        return merged

    def _run_scenario_phase(
        self,
        state: SessionState,
        *,
        text: str,
        scenario_attachments: dict[str, Any],
        new_messages: list[ConversationMessagePayload],
        new_handoffs: list[HandoffPayload],
        new_events: list[dict[str, Any]],
    ) -> SessionTurnResponse:
        scenario_turn = state.scenario_agent.run_turn(
            user_input=text,
            template_path=state.scenario_template_path,
            attachments_meta=scenario_attachments,
        )
        scenario_payload = _strict_json_object(scenario_turn.agent_reply)
        scenario_askmore = _extract_askmore(scenario_payload)

        scenario_blocks = adapt_payload_by_agent("ScenarioAgent", scenario_payload)
        new_messages.append(
            self._append_agent_blocks(state, "ScenarioAgent", scenario_blocks, new_events)
        )

        if scenario_askmore == "yes":
            state.stage = "scenario"
            self._persist_state(state)
            return self._response(
                state,
                active_agent="ScenarioAgent",
                askmore="yes",
                messages=new_messages,
                handoffs=new_handoffs,
                events=new_events,
            )

        state.scenario_output = scenario_payload
        state.stage = "scenario"

        self._append_handoff_request_message(
            state,
            from_agent="ScenarioAgent",
            to_agent="LegalAnalysisAgent",
            reason="场景分析已完成，待确认后进入工具检索与法律分析",
            scene_id=state.scene_id,
            event_collector=new_events,
        )
        self._set_pending_transition(
            state,
            from_agent="ScenarioAgent",
            to_agent="LegalAnalysisAgent",
            reason="场景分析与工具引导完成",
            scene_id=state.scene_id,
            pending_stage="legal",
            pending_payload={
                "scenario_output": scenario_payload,
            },
            event_collector=new_events,
        )
        return self._response(
            state,
            active_agent="ScenarioAgent",
            askmore="no",
            messages=new_messages,
            handoffs=new_handoffs,
            events=new_events,
        )

    def _execute_scenario_tools_with_events(
        self,
        state: SessionState,
        new_events: list[dict[str, Any]],
    ) -> str:
        if not isinstance(state.scenario_output, dict):
            raise ValueError("scenario_output must be object before running scenario tools")

        calls = state.scenario_agent.normalize_tool_calls(state.scenario_output)
        results: list[str] = []
        max_attempts = max(1, self.scenario_tool_retry_times + 1)

        for call in calls:
            self._append_event(
                state,
                "tool_call_started",
                {
                    "agent": "ScenarioAgent",
                    "service_name": call.tool_name,
                    "query": call.input,
                    "max_attempts": max_attempts,
                },
                new_events,
            )
            attempt_used = 0
            raw_result = ""
            last_error: Exception | None = None
            for attempt in range(1, max_attempts + 1):
                try:
                    raw_result = state.scenario_agent.mcp_query_callable(call.tool_name, call.input)
                    attempt_used = attempt
                    break
                except Exception as exc:  # noqa: PERF203
                    last_error = exc

            if not raw_result:
                self._append_event(
                    state,
                    "tool_call_failed",
                    {
                        "agent": "ScenarioAgent",
                        "service_name": call.tool_name,
                        "query": call.input,
                        "attempt_used": max_attempts,
                        "max_attempts": max_attempts,
                        "error": str(last_error),
                    },
                    new_events,
                )
                raise RuntimeError(
                    "Scenario MCP call failed after "
                    f"{max_attempts} attempts: {call.tool_name}, query={call.input}, error={last_error}"
                )

            results.append(raw_result)
            summary = raw_result.strip()
            if len(summary) > 320:
                summary = summary[:320].rstrip() + "..."
            self._append_event(
                state,
                "tool_call_finished",
                {
                    "agent": "ScenarioAgent",
                    "service_name": call.tool_name,
                    "query": call.input,
                    "attempt_used": attempt_used,
                    "max_attempts": max_attempts,
                    "status": "success",
                    "result_summary": summary,
                    "result_raw": raw_result,
                },
                new_events,
            )

        history = state.scenario_agent._render_tool_history(calls, results)
        state.scenario_tool_history = history
        self._persist_state(state)
        return history

    def _run_legal_phase(
        self,
        state: SessionState,
        *,
        new_messages: list[ConversationMessagePayload],
        new_handoffs: list[HandoffPayload],
        new_events: list[dict[str, Any]],
    ) -> SessionTurnResponse:
        if not isinstance(state.scenario_output, dict):
            raise ValueError("legal stage requires scenario_output")

        scenario_agent_input = json.dumps(state.scenario_output, ensure_ascii=False, indent=2)
        legal_result = state.legal_analysis_agent.run_until_done(
            scenario_agent_input=scenario_agent_input,
            template_path=state.legal_template_path,
            initial_tool_call_history=state.scenario_tool_history,
            max_rounds=self.legal_max_rounds,
        )
        final_output = legal_result.get("final_output")
        if not isinstance(final_output, dict):
            raise ValueError("legal_result.final_output must be object")
        legal_askmore = _extract_askmore(final_output)
        if legal_askmore != "no":
            raise ValueError("legal final output askmore must be no")

        legal_blocks = adapt_payload_by_agent("LegalAnalysisAgent", final_output)
        new_messages.append(self._append_agent_blocks(state, "LegalAnalysisAgent", legal_blocks, new_events))
        legal_tool_history = str(legal_result.get("tool_call_history", "")).strip()
        legal_tool_blocks = adapt_tool_history_text(legal_tool_history)
        if legal_tool_blocks:
            new_messages.append(
                self._append_agent_blocks(state, "LegalAnalysisAgent", legal_tool_blocks, new_events)
            )
        if legal_tool_history:
            self._append_event(
                state,
                "tool_call_finished",
                {
                    "agent": "LegalAnalysisAgent",
                    "service_name": "legal_tool_history",
                    "query": "batch",
                    "status": "success",
                    "result_summary": legal_tool_history[:320],
                    "result_raw": legal_tool_history,
                },
                new_events,
            )

        state.stage = "done"
        self._persist_state(state)
        return self._response(
            state,
            active_agent="LegalAnalysisAgent",
            askmore="no",
            messages=new_messages,
            handoffs=new_handoffs,
            events=new_events,
        )

    def submit_turn(
        self,
        session_id: str,
        user_input: str,
        attachments_meta: Any | None = None,
    ) -> SessionTurnResponse:
        state = self.get_session(session_id)
        if state.stage == "done":
            raise ValueError("session already completed; start a new session")
        if state.pending_transition:
            raise ValueError("pending handoff confirmation required before sending next user message")

        text = user_input.strip()
        if not text:
            raise ValueError("user_input must be non-empty")
        new_events: list[dict[str, Any]] = []
        text = self._decorate_user_input_after_reject(state, text, new_events)

        module_key = _extract_module_key(attachments_meta)
        if module_key:
            self._validate_role_module_access(state.role_id, module_key)
            state.current_module_key = module_key
            self._persist_state(state)

        direct_result = self._try_run_direct_module_service(
            state=state,
            user_input=text,
            attachments_meta=attachments_meta,
            pre_events=new_events,
        )
        if direct_result is not None:
            return direct_result

        state.turn_id += 1
        new_messages: list[ConversationMessagePayload] = []
        new_handoffs: list[HandoffPayload] = []

        stage_to_agent = {
            "controller": "ControllerAgent",
            "scenario": "ScenarioAgent",
            "legal": "LegalAnalysisAgent",
        }
        current_agent = stage_to_agent[state.stage]
        new_messages.append(
            self._append_user_message(
                state,
                text,
                speaker_agent=current_agent,
                event_collector=new_events,
            )
        )

        if state.stage == "controller":
            controller_attachments: dict[str, Any] = {
                "role_id": state.role_id,
                "attachments": attachments_meta,
            }
            if module_key:
                controller_attachments["module_key"] = module_key
                controller_attachments["module_scene_hints"] = list_module_scene_hints(module_key)
            controller_turn = state.controller_agent.run_turn(
                user_input=text,
                template_path=state.controller_template_path,
                attachments_meta=controller_attachments,
            )
            controller_payload = _strict_json_object(controller_turn.agent_reply)
            controller_askmore = _extract_askmore(controller_payload)

            controller_blocks = adapt_payload_by_agent("ControllerAgent", controller_payload)
            new_messages.append(
                self._append_agent_blocks(state, "ControllerAgent", controller_blocks, new_events)
            )

            if controller_askmore == "yes":
                self._persist_state(state)
                return self._response(
                    state,
                    active_agent="ControllerAgent",
                    askmore="yes",
                    messages=new_messages,
                    handoffs=new_handoffs,
                    events=new_events,
                )

            analysis = controller_payload.get("analysis")
            if not isinstance(analysis, dict):
                raise ValueError("controller payload.analysis must be object when askmore=no")
            raw_scene_id = analysis.get("scene_id")
            if not isinstance(raw_scene_id, str):
                raise ValueError("controller payload.analysis.scene_id must be string")

            scene_id = validate_scene_id(raw_scene_id)
            state.scene_id = scene_id
            state.scenario_template_path = self._resolve_scenario_template(state, scene_id)

            scenario_input = controller_payload.get("user_input")
            if not isinstance(scenario_input, str) or not scenario_input.strip():
                raise ValueError("controller payload.user_input must be non-empty string")

            self._append_handoff_request_message(
                state,
                from_agent="ControllerAgent",
                to_agent="ScenarioAgent",
                reason="主控路由完成",
                scene_id=scene_id,
                event_collector=new_events,
            )
            self._set_pending_transition(
                state,
                from_agent="ControllerAgent",
                to_agent="ScenarioAgent",
                reason="主控路由完成",
                scene_id=scene_id,
                pending_stage="scenario",
                pending_payload={
                    "scenario_input": scenario_input.strip(),
                    "scenario_attachments": {
                        "role_id": state.role_id,
                        "controller_output": controller_payload,
                    },
                },
                event_collector=new_events,
            )
            self._persist_state(state)
            return self._response(
                state,
                active_agent="ControllerAgent",
                askmore="no",
                messages=new_messages,
                handoffs=new_handoffs,
                events=new_events,
            )

        if state.stage == "scenario":
            scenario_attachments = {"role_id": state.role_id, "attachments": attachments_meta}
            return self._run_scenario_phase(
                state,
                text=text,
                scenario_attachments=scenario_attachments,
                new_messages=new_messages,
                new_handoffs=new_handoffs,
                new_events=new_events,
            )

        raise ValueError(f"unsupported stage for submit_turn: {state.stage}")

    def confirm_handoff(self, session_id: str, approve: bool) -> SessionTurnResponse:
        state = self.get_session(session_id)
        if not state.pending_transition:
            raise ValueError("no pending handoff to confirm")
        if state.stage == "done":
            raise ValueError("session already completed")

        transition = dict(state.pending_transition)
        pending_stage = str(state.pending_stage)
        pending_payload = dict(state.pending_payload)

        new_messages: list[ConversationMessagePayload] = []
        new_handoffs: list[HandoffPayload] = []
        new_events: list[dict[str, Any]] = []

        from_agent = str(transition.get("from_agent", ""))
        to_agent = str(transition.get("to_agent", ""))
        reason = str(transition.get("reason", ""))
        scene_id = str(transition.get("scene_id", ""))

        if not approve:
            reject_block = DisplayBlock(
                kind="handoff_rejected",
                title="用户拒绝移交",
                text=f"用户拒绝从 {from_agent} 移交到 {to_agent}，当前阶段保持不变。",
                metadata={
                    "from_agent": from_agent,
                    "to_agent": to_agent,
                    "reason": reason,
                    "scene_id": scene_id,
                },
            )
            new_messages.append(
                self._append_agent_blocks(state, from_agent, [reject_block], new_events)
            )
            self._append_event(
                state,
                "handoff_rejected",
                {
                    "from_agent": from_agent,
                    "to_agent": to_agent,
                    "reason": reason,
                    "scene_id": scene_id,
                },
                new_events,
            )
            state.inject_rejection_prefix_on_next_turn = True
            self._clear_pending_transition(state)
            self._persist_state(state)
            return self._response(
                state,
                active_agent=from_agent or "ControllerAgent",
                askmore="yes",
                messages=new_messages,
                handoffs=new_handoffs,
                events=new_events,
            )

        handoff = self._append_handoff(
            state,
            from_agent=from_agent,
            to_agent=to_agent,
            reason=reason,
            scene_id=scene_id,
            event_collector=new_events,
        )
        new_handoffs.append(handoff)
        self._clear_pending_transition(state)

        if pending_stage == "scenario":
            state.stage = "scenario"
            scenario_input = str(pending_payload.get("scenario_input", "")).strip()
            scenario_attachments = dict(pending_payload.get("scenario_attachments", {}) or {})
            if not scenario_input:
                raise ValueError("missing pending scenario_input for handoff confirmation")
            return self._run_scenario_phase(
                state,
                text=scenario_input,
                scenario_attachments=scenario_attachments,
                new_messages=new_messages,
                new_handoffs=new_handoffs,
                new_events=new_events,
            )

        if pending_stage == "legal":
            state.stage = "legal"
            if isinstance(pending_payload.get("scenario_output"), dict):
                state.scenario_output = dict(pending_payload["scenario_output"])

            scenario_tool_history = self._execute_scenario_tools_with_events(state, new_events)
            tool_history_blocks = adapt_tool_history_text(scenario_tool_history)
            if tool_history_blocks:
                new_messages.append(
                    self._append_agent_blocks(state, "ScenarioAgent", tool_history_blocks, new_events)
                )

            return self._run_legal_phase(
                state,
                new_messages=new_messages,
                new_handoffs=new_handoffs,
                new_events=new_events,
            )

        raise ValueError(f"unsupported pending_stage: {pending_stage}")

    def list_messages(self, session_id: str) -> list[dict[str, Any]]:
        state = self.get_session(session_id)
        return [message.to_dict() for message in state.messages]

    def list_events(self, session_id: str) -> list[dict[str, Any]]:
        state = self.get_session(session_id)
        return [dict(item) for item in state.event_log]

    def get_session_summary(self, session_id: str) -> dict[str, Any]:
        state = self.get_session(session_id)
        return {
            "session_id": state.session_id,
            "role_id": state.role_id,
            "stage": state.stage,
            "title": self._derive_session_title(state),
            "created_at_utc": state.created_at_utc,
            "updated_at_utc": state.updated_at_utc,
            "turn_id": state.turn_id,
            "scene_id": state.scene_id,
            "message_count": len(state.messages),
            "handoff_count": len(state.handoffs),
            "current_module_key": state.current_module_key,
            "pending_transition": self._public_pending_transition(state),
            "event_count": len(state.event_log),
        }

    def list_sessions(self, role_id: str | None = None) -> list[dict[str, Any]]:
        summaries: list[dict[str, Any]] = []
        expected_role = validate_role_id(role_id) if role_id is not None else None
        for session_id, state in self.sessions.items():
            if expected_role is not None and state.role_id != expected_role:
                continue
            summaries.append(self.get_session_summary(session_id))
        summaries.sort(key=lambda item: str(item.get("updated_at_utc", "")), reverse=True)
        return summaries
