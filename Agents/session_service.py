from __future__ import annotations

from dataclasses import dataclass, field
import json
import re
from pathlib import Path
from typing import Any, Callable
from uuid import uuid4

from .case_state import CaseState, CaseWorkspace
from .event_bus import build_event
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
from .llm_call import chat_completion
from .scene_catalog import (
    get_role_scene_template_path,
    get_scene_template_filename,
    list_module_scene_hints,
    list_role_modules,
    validate_module_key,
    validate_role_id,
    validate_scene_id,
)
from .scenario_agent import DEFAULT_MCP_RETRIES, SCENARIO_TEMPLATE_ROOT, ScenarioAgent
from .state_manager import CasePatch, PatchOperation, StateManager


PROJECT_ROOT = Path(__file__).resolve().parents[1]
CONTROLLER_TEMPLATE_DEFAULT = str(PROJECT_ROOT / "Prompt_Template/ControllerAgent.md")
LEGAL_TEMPLATE_DEFAULT = str(PROJECT_ROOT / LEGAL_ANALYSIS_TEMPLATE_DEFAULT)
SESSION_STORAGE_DEFAULT = PROJECT_ROOT / "storage" / "sessions"
LEGACY_SCENARIO_TEMPLATE_ROOT = Path(SCENARIO_TEMPLATE_ROOT).resolve()
REJECTED_TERMINATION_PREFIX = (
    "用户拒绝了本次Agent的终止，也许是还有需要澄清的地方，这次的输入如下："
)


_SCENE_DISPLAY_NAMES: dict[str, str] = {
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


def _scenario_display_name(scene_id: str) -> str:
    return _SCENE_DISPLAY_NAMES.get((scene_id or "").strip(), "场景分析Agent")


def _strict_json_object(text: str) -> dict[str, Any]:
    if not text or not text.strip():
        raise ValueError("agent returned empty response — LLM may be overloaded, please retry")
    # Try to extract JSON from text that may contain markdown fences
    cleaned = text.strip()
    if cleaned.startswith("```"):
        lines = cleaned.split("\n")
        lines = [line for line in lines if not line.strip().startswith("```")]
        cleaned = "\n".join(lines).strip()
    if "{" in cleaned:
        start = cleaned.index("{")
        end = cleaned.rindex("}") + 1
        cleaned = cleaned[start:end]
    payload = json.loads(cleaned)
    if not isinstance(payload, dict):
        raise ValueError("agent output must be a JSON object")
    return payload


def _extract_askmore(payload: dict[str, Any], *, allowed: set[str] | None = None) -> str:
    valid_values = allowed or {"yes", "no"}
    value = payload.get("askmore")
    if not isinstance(value, str):
        allowed_text = " | ".join(sorted(valid_values))
        raise ValueError(f'payload.askmore must be a string in [{allowed_text}]')
    normalized = value.strip().lower()
    if normalized not in valid_values:
        allowed_text = " | ".join(sorted(valid_values))
        raise ValueError(f'payload.askmore must be in [{allowed_text}]')
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
    controller_ask_count: int = 0  # number of times ControllerAgent has replied with askmore=yes
    legal_report_markdown: str = ""
    legal_report_updated_at_utc: str = ""
    messages: list[Any] = field(default_factory=list)
    handoffs: list[HandoffPayload] = field(default_factory=list)
    event_log: list[dict[str, Any]] = field(default_factory=list)
    workspace: CaseWorkspace = field(default_factory=lambda: CaseWorkspace.new("", ""))
    cached_title: str = ""


@dataclass
class MultiAgentSessionService:
    controller_factory: Callable[[], Agent] = field(
        default_factory=lambda: (lambda: Agent(main_prompt="", model="gemini-3-flash-preview-cli"))
    )
    scenario_factory: Callable[[], ScenarioAgent] = field(
        default_factory=lambda: (lambda: ScenarioAgent(main_prompt="", model="gemini-3-flash-preview-cli"))
    )
    legal_factory: Callable[[], LegalAnalysisAgent] = field(
        default_factory=lambda: (lambda: LegalAnalysisAgent(main_prompt="", model="gemini-3.1-pro-preview-cli"))
    )
    controller_template_path: str = CONTROLLER_TEMPLATE_DEFAULT
    legal_template_path: str = LEGAL_TEMPLATE_DEFAULT
    scenario_template_root: Path = field(default_factory=lambda: Path(SCENARIO_TEMPLATE_ROOT))
    scenario_tool_retry_times: int = DEFAULT_MCP_RETRIES
    legal_max_rounds: int = 8
    storage_root: Path = field(default_factory=lambda: SESSION_STORAGE_DEFAULT)
    state_manager: StateManager = field(default_factory=StateManager)
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
            "controller_ask_count": state.controller_ask_count,
            "legal_report_markdown": state.legal_report_markdown,
            "legal_report_updated_at_utc": state.legal_report_updated_at_utc,
            "messages": [message.to_dict() for message in state.messages],
            "handoffs": [handoff.to_dict() for handoff in state.handoffs],
            "event_log": [dict(item) for item in state.event_log],
            "case_state": state.workspace.case_state.to_dict(),
            "case_version": state.workspace.version,
            "case_workspace": state.workspace.to_dict(),
            "cached_title": state.cached_title,
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

            # Resolve template paths, falling back to current defaults when
            # the stored path no longer exists (e.g. session from another
            # machine, old project copy, or expired temp directory).
            _stored_ctrl_tpl = str(
                snapshot.get("controller_template_path", self.controller_template_path)
            )
            if not Path(_stored_ctrl_tpl).exists():
                _stored_ctrl_tpl = self.controller_template_path
            _stored_legal_tpl = str(
                snapshot.get("legal_template_path", self.legal_template_path)
            )
            if not Path(_stored_legal_tpl).exists():
                _stored_legal_tpl = self.legal_template_path
            _stored_scene_root = Path(
                str(snapshot.get("scenario_template_root", self.scenario_template_root))
            ).resolve()
            if not _stored_scene_root.is_dir():
                _stored_scene_root = self.scenario_template_root.resolve()

            workspace = self._workspace_from_snapshot(snapshot, session_id=session_id, role_id=role_id)

            state = SessionState(
                session_id=session_id,
                role_id=role_id,
                controller_agent=controller_agent,
                scenario_agent=scenario_agent,
                legal_analysis_agent=legal_agent,
                controller_template_path=_stored_ctrl_tpl,
                legal_template_path=_stored_legal_tpl,
                scenario_template_root=_stored_scene_root,
                stage=str(snapshot.get("stage", "controller")),
                created_at_utc=str(snapshot.get("created_at_utc", now_utc_iso())),
                updated_at_utc=str(snapshot.get("updated_at_utc", now_utc_iso())),
                turn_id=int(snapshot.get("turn_id", 0)),
                scene_id=str(snapshot.get("scene_id", "")),
                scenario_template_path=str(snapshot.get("scenario_template_path", ""))
                    if Path(str(snapshot.get("scenario_template_path", ""))).exists()
                    else "",
                scenario_output=snapshot.get("scenario_output"),
                scenario_tool_history=str(snapshot.get("scenario_tool_history", "")),
                pending_transition=snapshot.get("pending_transition"),
                pending_stage=str(snapshot.get("pending_stage", "")),
                pending_payload=dict(snapshot.get("pending_payload", {}) or {}),
                inject_rejection_prefix_on_next_turn=bool(
                    snapshot.get("inject_rejection_prefix_on_next_turn", False)
                ),
                current_module_key=str(snapshot.get("current_module_key", "")),
                controller_ask_count=int(snapshot.get("controller_ask_count", 0)),
                cached_title=str(snapshot.get("cached_title", "")),
                legal_report_markdown=str(snapshot.get("legal_report_markdown", "")),
                legal_report_updated_at_utc=str(snapshot.get("legal_report_updated_at_utc", "")),
                messages=messages,
                handoffs=handoffs,
                event_log=[
                    dict(item)
                    for item in list(snapshot.get("event_log", []))
                    if isinstance(item, dict)
                ],
                workspace=workspace,
            )
            state.workspace.messages = state.messages
            state.workspace.handoffs = state.handoffs
            state.workspace.event_log = state.event_log
            scenario_agent.template_root = state.scenario_template_root
            self.sessions[session_id] = state

    def _workspace_from_snapshot(
        self,
        snapshot: dict[str, Any],
        *,
        session_id: str,
        role_id: str,
    ) -> CaseWorkspace:
        raw_workspace = snapshot.get("case_workspace")
        if isinstance(raw_workspace, dict):
            workspace = CaseWorkspace.from_dict(raw_workspace)
            workspace.session_id = session_id
            workspace.role_id = role_id
            workspace.case_state.interaction["user_role"] = role_id
            return workspace

        workspace = CaseWorkspace.new(session_id=session_id, role_id=role_id)
        raw_case_state = snapshot.get("case_state")
        if isinstance(raw_case_state, dict):
            workspace.case_state = CaseState.from_dict(raw_case_state)
            workspace.case_state.interaction["user_role"] = role_id
            workspace.version = int(snapshot.get("case_version", 0))
            return workspace

        self._migrate_legacy_snapshot_to_workspace(snapshot, workspace)
        return workspace

    def _migrate_legacy_snapshot_to_workspace(
        self,
        snapshot: dict[str, Any],
        workspace: CaseWorkspace,
    ) -> None:
        state = workspace.case_state
        state.interaction["stage"] = str(snapshot.get("stage", "controller"))
        state.interaction["user_role"] = workspace.role_id
        scene_id = str(snapshot.get("scene_id", "")).strip()
        if scene_id:
            state.interaction["scene_id"] = scene_id
        scenario_output = snapshot.get("scenario_output")
        if isinstance(scenario_output, dict):
            analysis = scenario_output.get("analysis")
            if isinstance(analysis, dict):
                status = str(analysis.get("current_status", "")).strip()
                appeal = str(analysis.get("user_appeal", "")).strip()
                problems = str(analysis.get("faced_problems", "")).strip()
                if status:
                    state.facts["items"]["scenario.current_status"] = {
                        "fact_id": "scenario.current_status",
                        "value": status,
                        "status": "user_claimed",
                        "source": {"agent": "ScenarioAgent", "migration": True},
                        "confidence": "",
                        "updated_at": str(snapshot.get("updated_at_utc", now_utc_iso())),
                    }
                if appeal:
                    state.interaction["current_goal"] = appeal
                if problems:
                    state.analysis["issues"].append(
                        {"issue_id": "legacy.faced_problems", "title": problems}
                    )
        markdown = str(snapshot.get("legal_report_markdown", "")).strip()
        if markdown:
            state.outputs["artifacts"].append(
                {
                    "artifact_id": f"legacy-report-{workspace.session_id[:8]}",
                    "type": "legal_report",
                    "title": "法律分析报告",
                    "content": markdown,
                    "generated_by": "LegalAnalysisAgent",
                    "generated_at": str(
                        snapshot.get("legal_report_updated_at_utc")
                        or snapshot.get("updated_at_utc")
                        or now_utc_iso()
                    ),
                    "case_version": workspace.version,
                    "metadata": {"migration": True},
                }
            )

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
            workspace=CaseWorkspace.new(session_id=session_id, role_id=role),
        )
        state.workspace.messages = state.messages
        state.workspace.handoffs = state.handoffs
        state.workspace.event_log = state.event_log
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
        pt = state.pending_transition
        result: dict[str, Any] = {
            "from_agent": str(pt.get("from_agent", "")),
            "to_agent": str(pt.get("to_agent", "")),
            "reason": str(pt.get("reason", "")),
            "scene_id": str(pt.get("scene_id", "")),
            "trigger_turn_id": int(pt.get("trigger_turn_id", state.turn_id)),
            "proposed_at": str(pt.get("proposed_at", "")),
        }
        # Forward analysis summary fields if present
        for key in ("current_status", "user_appeal", "faced_problems", "user_input"):
            if key in pt:
                result[key] = str(pt[key])
        return result

    def _derive_session_title(self, state: SessionState, use_llm: bool = True) -> str:
        if state.cached_title:
            return state.cached_title

        user_texts: list[str] = []
        ai_texts: list[str] = []
        for message in state.messages:
            speaker = getattr(message, "speaker_type", "")
            for block in list(getattr(message, "display_blocks", []) or []):
                text = str(getattr(block, "text", "")).strip()
                if not text:
                    continue
                # strip module selection prefix
                text = re.sub(r"^【[^】]*】\s*\S+\s*", "", text).strip()
                if not text:
                    continue
                if speaker == "user":
                    user_texts.append(text)
                elif speaker == "agent":
                    ai_texts.append(text)
        if not user_texts and not ai_texts:
            return "新会话"

        if not use_llm:
            return (user_texts[0] if user_texts else ai_texts[0])[:20]

        combined = "\n".join(user_texts[:3])[:300]
        if ai_texts:
            combined += "\n---AI回复---\n" + "\n".join(ai_texts[:2])[:300]

        try:
            title = chat_completion(
                user_prompt=f"请用8-15个字概括以下劳动法咨询对话的核心内容，只输出概括文字，不要加标点：\n{combined}",
            ).strip().strip("\"'""''。，、：:")[:20]
            if title:
                state.cached_title = title
                self._persist_state(state)
                return title
        except Exception:
            pass
        fallback = (user_texts[0] if user_texts else ai_texts[0])[:20]
        state.cached_title = fallback
        self._persist_state(state)
        return fallback

    def _build_event(
        self,
        state: SessionState,
        event_type: str,
        payload: dict[str, Any],
        *,
        turn_id: int | None = None,
    ) -> dict[str, Any]:
        return build_event(
            session_id=state.session_id,
            turn_id=int(state.turn_id if turn_id is None else turn_id),
            stage=state.stage,
            event_type=event_type,
            payload=payload,
        )

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
        state.workspace.event_log = state.event_log
        collector.append(event)
        self._persist_state(state)
        return event

    def _apply_case_patch(
        self,
        state: SessionState,
        patch: CasePatch,
        event_collector: list[dict[str, Any]],
    ) -> None:
        self._append_event(
            state,
            "patch_submitted",
            {"patch": patch.to_dict()},
            event_collector,
        )
        result = self.state_manager.apply_patch(state.workspace, patch)
        self._append_event(
            state,
            "patch_applied" if result.accepted else "patch_rejected",
            result.to_dict(),
            event_collector,
        )
        if not result.accepted:
            raise ValueError("; ".join(result.errors))
        self._persist_state(state)

    def _build_controller_case_patch(
        self,
        state: SessionState,
        payload: dict[str, Any],
        *,
        user_input: str,
        askmore: str,
    ) -> CasePatch:
        operations: list[PatchOperation] = [
            PatchOperation("set", "interaction.stage", state.stage),
            PatchOperation("set", "interaction.last_user_input", user_input),
        ]
        if askmore == "yes":
            ask = str(payload.get("ask", "")).strip()
            if ask:
                operations.append(PatchOperation("set", "interaction.pending_questions", [ask]))
        analysis = payload.get("analysis")
        if isinstance(analysis, dict):
            scene_id = str(analysis.get("scene_id", "")).strip()
            user_appeal = str(analysis.get("user_appeal", "")).strip()
            current_status = str(analysis.get("current_status", "")).strip()
            faced_problems = str(analysis.get("faced_problems", "")).strip()
            if scene_id:
                operations.append(PatchOperation("set", "interaction.scene_id", scene_id))
            if user_appeal:
                operations.append(PatchOperation("set", "interaction.current_goal", user_appeal))
            if current_status:
                operations.append(
                    PatchOperation(
                        "append",
                        "analysis.preliminary_conclusions",
                        {
                            "source": "ControllerAgent",
                            "kind": "current_status",
                            "text": current_status,
                        },
                    )
                )
            if faced_problems:
                operations.append(
                    PatchOperation(
                        "append",
                        "analysis.issues",
                        {
                            "issue_id": f"controller.{state.turn_id}.faced_problems",
                            "title": faced_problems,
                            "source": "ControllerAgent",
                        },
                    )
                )
        return CasePatch.new(
            agent_name="ControllerAgent",
            base_version=state.workspace.version,
            operations=operations,
            metadata={"stage": state.stage, "askmore": askmore},
        )

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
        # Prefer role-based mapping only when session uses default legacy root.
        # Custom roots in tests/sandboxes should stay isolated.
        if state.scenario_template_root == LEGACY_SCENARIO_TEMPLATE_ROOT:
            mapped_relative = get_role_scene_template_path(state.role_id, scene)
            mapped_path = (PROJECT_ROOT / mapped_relative).resolve()
            if mapped_path.exists():
                return str(mapped_path)
        else:
            mapped_path = Path("")

        # Backward compatibility: fallback to legacy ScenarioAgents root layout.
        filename = get_scene_template_filename(scene)
        legacy_path = (state.scenario_template_root / filename).resolve()
        if legacy_path.exists():
            return str(legacy_path)

        raise FileNotFoundError(
            f"scenario template not found for role_id={state.role_id}, "
            f"scene_id={scene}. mapped={mapped_path}, legacy={legacy_path}"
        )

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
        analysis_summary: dict[str, str] | None = None,
    ) -> ConversationMessagePayload:
        metadata: dict[str, str] = {
            "from_agent": from_agent,
            "to_agent": to_agent,
            "scene_id": scene_id,
            "reason": reason,
        }
        if analysis_summary:
            for k, v in analysis_summary.items():
                metadata[k] = str(v)
        block = DisplayBlock(
            kind="handoff_request",
            title="等待用户确认移交",
            text=f"{from_agent} 已完成当前阶段，建议移交给 {to_agent}。请确认是否继续。",
            metadata=metadata,
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
        analysis_summary: dict[str, str] | None = None,
    ) -> None:
        state.pending_transition = {
            "from_agent": from_agent,
            "to_agent": to_agent,
            "reason": reason,
            "scene_id": scene_id,
            "trigger_turn_id": state.turn_id,
            "proposed_at": now_utc_iso(),
            **(analysis_summary or {}),
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

    UPLOAD_DIR = Path(__file__).resolve().parent.parent / "storage" / "uploads"

    def _append_file_contents(self, text: str, attachments_meta: Any | None) -> str:
        if not isinstance(attachments_meta, dict):
            return text
        files = attachments_meta.get("files")
        if not isinstance(files, list) or not files:
            return text
        parts = [text]
        for item in files:
            if not isinstance(item, dict):
                continue
            file_id = str(item.get("file_id") or "").strip()
            name = str(item.get("name") or "").strip()
            if not file_id or not name:
                continue
            safe_name = f"{file_id}_{name}"
            path = self.UPLOAD_DIR / safe_name
            if not path.is_file():
                parts.append(f"\n\n[附件: {name}]（文件未找到）")
                continue
            ext = Path(name).suffix.lower()
            if ext in {".txt", ".csv", ".md"}:
                try:
                    content = path.read_text(encoding="utf-8", errors="replace")[:30000]
                    parts.append(f"\n\n--- 附件: {name} ---\n{content}\n--- 附件结束 ---")
                except Exception:
                    parts.append(f"\n\n[附件: {name}]（读取失败）")
            else:
                parts.append(f"\n\n[附件: {name}]（{ext} 格式文件已上传，请基于文件名和用户描述进行分析）")
        return "".join(parts)

    def _build_evidence_case_patch(
        self,
        state: SessionState,
        attachments_meta: Any | None,
    ) -> CasePatch | None:
        if not isinstance(attachments_meta, dict):
            return None
        files = attachments_meta.get("files")
        if not isinstance(files, list) or not files:
            return None
        operations: list[PatchOperation] = []
        for item in files:
            if not isinstance(item, dict):
                continue
            file_id = str(item.get("file_id") or "").strip()
            name = str(item.get("name") or "").strip()
            if not file_id or not name:
                continue
            safe_name = f"{file_id}_{name}"
            path = self.UPLOAD_DIR / safe_name
            ext = Path(name).suffix.lower()
            extracted_text = ""
            authenticity_risk = "unparsed_file"
            if path.is_file() and ext in {".txt", ".csv", ".md"}:
                extracted_text = path.read_text(encoding="utf-8", errors="replace")[:30000]
                authenticity_risk = "text_extracted_unverified"
            elif not path.is_file():
                authenticity_risk = "file_missing"
            operations.append(
                PatchOperation(
                    "upsert_evidence",
                    f"evidence.items.{file_id}",
                    {
                        "evidence_id": file_id,
                        "type": ext.lstrip(".") or "unknown",
                        "source": {"file_id": file_id, "name": name},
                        "extracted_text": extracted_text,
                        "linked_fact_ids": [],
                        "probative_value": "unreviewed",
                        "authenticity_risk": authenticity_risk,
                    },
                )
            )
        if not operations:
            return None
        return CasePatch.new(
            agent_name="EvidenceParser",
            base_version=state.workspace.version,
            operations=operations,
            metadata={"source": "attachments_meta"},
        )

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

    def _build_legal_tool_call_history(self, state: SessionState, *, limit_chars: int = 12000) -> str:
        lines: list[str] = []

        for message in state.messages:
            speaker_type = str(getattr(message, "speaker_type", "")).strip()
            speaker_agent = str(getattr(message, "speaker_agent", "")).strip()
            created_at = str(getattr(message, "created_at_utc", "")).strip()
            speaker = "User" if speaker_type == "user" else (speaker_agent or "Agent")
            lines.append(f"[{created_at}] {speaker}:")
            for block in list(getattr(message, "display_blocks", []) or []):
                kind = str(getattr(block, "kind", "")).strip()
                title = str(getattr(block, "title", "")).strip()
                text = str(getattr(block, "text", "")).strip()
                items = [str(i).strip() for i in list(getattr(block, "items", []) or []) if str(i).strip()]
                if title:
                    lines.append(f"- {kind or 'block'}: {title}")
                if text:
                    lines.append(text)
                if items:
                    lines.extend([f"* {item}" for item in items])
            lines.append("")

        for event in state.event_log:
            event_type = str(event.get("event_type", "")).strip()
            if event_type not in {"report_generation_requested", "report_generated"}:
                continue
            created_at = str(event.get("created_at_utc", "")).strip()
            payload = dict(event.get("payload", {}) or {})
            payload_text = json.dumps(payload, ensure_ascii=False)
            lines.append(f"[{created_at}] EVENT {event_type}: {payload_text}")

        history_text = "\n".join(lines).strip()
        if not history_text:
            return ""
        if len(history_text) <= limit_chars:
            return history_text
        return "[History Truncated]\n" + history_text[-limit_chars:]

    def _run_scenario_phase(
        self,
        state: SessionState,
        *,
        text: str,
        scenario_attachments: dict[str, Any],
        new_messages: list[ConversationMessagePayload],
        new_handoffs: list[HandoffPayload],
        new_events: list[dict[str, Any]],
        on_token: Any | None = None,
    ) -> SessionTurnResponse:
        # Re-resolve template if stored path is missing (e.g. restored from
        # another machine or expired temp dir).
        if not state.scenario_template_path or not Path(state.scenario_template_path).exists():
            state.scenario_template_path = self._resolve_scenario_template(state, state.scene_id)
        scenario_turn = state.scenario_agent.run_turn(
            user_input=text,
            template_path=state.scenario_template_path,
            attachments_meta=scenario_attachments,
            on_token=on_token,
        )
        scenario_payload = _strict_json_object(scenario_turn.agent_reply)
        scenario_askmore = _extract_askmore(scenario_payload)

        self._apply_case_patch(
            state,
            state.scenario_agent.build_case_patch_from_payload(
                state.workspace,
                scenario_payload,
            ),
            new_events,
        )

        scenario_blocks = adapt_payload_by_agent("ScenarioAgent", scenario_payload, scene_id=state.scene_id)
        scenario_display_name = _scenario_display_name(state.scene_id)
        new_messages.append(
            self._append_agent_blocks(state, scenario_display_name, scenario_blocks, new_events)
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
        if calls:
            operations = [
                PatchOperation(
                    "append",
                    "analysis.legal_sources",
                    {
                        "kind": "tool_result",
                        "tool_name": call.tool_name,
                        "query": call.input,
                        "result": result_text,
                        "source": "ScenarioAgent",
                    },
                )
                for call, result_text in zip(calls, results)
            ]
            self._apply_case_patch(
                state,
                CasePatch.new(
                    agent_name="ScenarioAgent",
                    base_version=state.workspace.version,
                    operations=operations,
                    metadata={"source": "scenario_tool_results"},
                ),
                new_events,
            )
        self._persist_state(state)
        return history

    def _run_legal_phase(
        self,
        state: SessionState,
        *,
        new_messages: list[ConversationMessagePayload],
        new_handoffs: list[HandoffPayload],
        new_events: list[dict[str, Any]],
        on_token: Any | None = None,
        user_followup: str = "",
    ) -> SessionTurnResponse:
        if not isinstance(state.scenario_output, dict):
            raise ValueError("legal stage requires scenario_output")

        scenario_agent_input = json.dumps(state.scenario_output, ensure_ascii=False, indent=2)
        # When the user provides follow-up answers (e.g. after seeing the
        # initial legal report), append them so the LLM can incorporate the
        # new facts into an updated report.
        if user_followup:
            scenario_agent_input += (
                "\n\n--- 用户补充信息 ---\n" + user_followup
            )
        legal_history = self._build_legal_tool_call_history(state)
        legal_result = state.legal_analysis_agent.run_until_done(
            scenario_agent_input=scenario_agent_input,
            template_path=state.legal_template_path,
            initial_tool_call_history=legal_history,
            max_rounds=self.legal_max_rounds,
            on_token=on_token,
        )
        final_output = legal_result.get("final_output")
        if not isinstance(final_output, dict):
            raise ValueError("legal_result.final_output must be object")
        legal_askmore = _extract_askmore(final_output, allowed={"yes", "no", "end"})

        legal_blocks = adapt_payload_by_agent("LegalAnalysisAgent", final_output)
        if legal_blocks:
            new_messages.append(self._append_agent_blocks(state, "LegalAnalysisAgent", legal_blocks, new_events))

        legal_markdown = str(final_output.get("analysis", "")).strip()
        if legal_markdown:
            report_ts = now_utc_iso()
            state.legal_report_markdown = legal_markdown
            state.legal_report_updated_at_utc = report_ts
            self._apply_case_patch(
                state,
                state.legal_analysis_agent.build_output_patch(
                    state.workspace,
                    final_output,
                ),
                new_events,
            )
            self._append_event(
                state,
                "report_generated",
                {
                    "source": "legal_agent_output",
                    "length": len(legal_markdown),
                    "updated_at_utc": report_ts,
                },
                new_events,
            )

        legal_tool_history = str(legal_result.get("tool_call_history", "")).strip()
        if legal_tool_history and legal_tool_history != legal_history:
            legal_tool_blocks = adapt_tool_history_text(legal_tool_history)
            if legal_tool_blocks:
                new_messages.append(
                    self._append_agent_blocks(state, "LegalAnalysisAgent", legal_tool_blocks, new_events)
                )
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

        if legal_askmore == "end":
            end_hint = DisplayBlock(
                kind="agent_message",
                title="会话已结束",
                text="当前阶段已完成。如果你还想继续追问，可直接在本会话继续提问。",
            )
            new_messages.append(
                self._append_agent_blocks(state, "LegalAnalysisAgent", [end_hint], new_events)
            )
            state.stage = "done"
            response_askmore = "no"
        else:
            state.stage = "legal"
            response_askmore = legal_askmore
        self._persist_state(state)
        return self._response(
            state,
            active_agent="LegalAnalysisAgent",
            askmore=response_askmore,
            messages=new_messages,
            handoffs=new_handoffs,
            events=new_events,
        )

    def submit_turn(
        self,
        session_id: str,
        user_input: str,
        attachments_meta: Any | None = None,
        on_token: Any | None = None,
    ) -> SessionTurnResponse:
        state = self.get_session(session_id)
        new_events: list[dict[str, Any]] = []
        if state.stage == "done":
            # Continue in the same session after completion.
            if state.legal_report_markdown.strip() or isinstance(state.scenario_output, dict):
                state.stage = "legal"
            else:
                state.stage = "controller"
                state.controller_ask_count = 0
            self._persist_state(state)
        if state.pending_transition:
            raise ValueError("pending handoff confirmation required before sending next user message")

        text = user_input.strip()
        if not text:
            raise ValueError("user_input must be non-empty")

        text = self._append_file_contents(text, attachments_meta)

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

        self._append_event(
            state,
            "user_input_received",
            {
                "text": text,
                "attachments_meta": attachments_meta if isinstance(attachments_meta, dict) else None,
            },
            new_events,
        )
        evidence_patch = self._build_evidence_case_patch(state, attachments_meta)
        if evidence_patch is not None:
            self._apply_case_patch(state, evidence_patch, new_events)

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
                "controller_ask_count": state.controller_ask_count,
                "max_ask_rounds": 3,
            }
            if module_key:
                controller_attachments["module_key"] = module_key
                controller_attachments["module_scene_hints"] = list_module_scene_hints(module_key)

            controller_turn = state.controller_agent.run_turn(
                user_input=text,
                template_path=state.controller_template_path,
                attachments_meta=controller_attachments,
                on_token=on_token,
            )
            controller_payload = _strict_json_object(controller_turn.agent_reply)
            controller_askmore = _extract_askmore(controller_payload)

            self._apply_case_patch(
                state,
                self._build_controller_case_patch(
                    state,
                    controller_payload,
                    user_input=text,
                    askmore=controller_askmore,
                ),
                new_events,
            )

            controller_blocks = adapt_payload_by_agent("ControllerAgent", controller_payload)
            new_messages.append(
                self._append_agent_blocks(state, "ControllerAgent", controller_blocks, new_events)
            )

            if controller_askmore == "yes":
                state.controller_ask_count += 1
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

            controller_analysis = controller_payload.get("analysis", {})
            self._append_handoff_request_message(
                state,
                from_agent="ControllerAgent",
                to_agent="ScenarioAgent",
                reason="主控路由完成",
                scene_id=scene_id,
                event_collector=new_events,
                analysis_summary={
                    "current_status": str(controller_analysis.get("current_status", "")),
                    "user_appeal": str(controller_analysis.get("user_appeal", "")),
                    "faced_problems": str(controller_analysis.get("faced_problems", "")),
                    "user_input": str(controller_payload.get("user_input", "")),
                },
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
                analysis_summary={
                    "current_status": str(controller_analysis.get("current_status", "")),
                    "user_appeal": str(controller_analysis.get("user_appeal", "")),
                    "faced_problems": str(controller_analysis.get("faced_problems", "")),
                    "user_input": str(controller_payload.get("user_input", "")),
                },
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
                on_token=on_token,
            )

        if state.stage == "legal":
            return self._run_legal_phase(
                state,
                new_messages=new_messages,
                new_handoffs=new_handoffs,
                new_events=new_events,
                on_token=on_token,
                user_followup=text,
            )

        raise ValueError(f"unsupported stage for submit_turn: {state.stage}")

    def confirm_handoff(self, session_id: str, approve: bool, on_token: Any | None = None) -> SessionTurnResponse:
        state = self.get_session(session_id)
        if not state.pending_transition:
            raise ValueError("no pending handoff to confirm")

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

        # Save current pending state for rollback if the AI call later fails
        _saved_transition = dict(state.pending_transition) if state.pending_transition else None
        _saved_pending_stage = str(state.pending_stage)
        _saved_pending_payload = dict(state.pending_payload) if state.pending_payload else {}
        _saved_stage = str(state.stage)

        self._clear_pending_transition(state)

        if pending_stage == "scenario":
            state.stage = "scenario"
            scenario_input = str(pending_payload.get("scenario_input", "")).strip()
            scenario_attachments = dict(pending_payload.get("scenario_attachments", {}) or {})
            if not scenario_input:
                raise ValueError("missing pending scenario_input for handoff confirmation")
            try:
                return self._run_scenario_phase(
                    state,
                    text=scenario_input,
                    scenario_attachments=scenario_attachments,
                    new_messages=new_messages,
                    new_handoffs=new_handoffs,
                    new_events=new_events,
                    on_token=on_token,
                )
            except Exception:
                state.pending_transition = _saved_transition
                state.pending_stage = _saved_pending_stage
                state.pending_payload = _saved_pending_payload
                state.stage = _saved_stage
                self._persist_state(state)
                raise

        if pending_stage == "legal":
            state.stage = "legal"
            if isinstance(pending_payload.get("scenario_output"), dict):
                state.scenario_output = dict(pending_payload["scenario_output"])

            # Skip re-running tools if results are already cached (e.g. retry after AI failure)
            if state.scenario_tool_history:
                scenario_tool_history = state.scenario_tool_history
            else:
                scenario_tool_history = self._execute_scenario_tools_with_events(state, new_events)
            tool_history_blocks = adapt_tool_history_text(scenario_tool_history)
            if tool_history_blocks:
                new_messages.append(
                    self._append_agent_blocks(state, "ScenarioAgent", tool_history_blocks, new_events)
                )

            try:
                return self._run_legal_phase(
                    state,
                    new_messages=new_messages,
                    new_handoffs=new_handoffs,
                    new_events=new_events,
                    on_token=on_token,
                )
            except Exception:
                # Restore pending_transition so the user can retry handoff confirmation
                state.pending_transition = _saved_transition
                state.pending_stage = _saved_pending_stage
                state.pending_payload = _saved_pending_payload
                state.stage = _saved_stage
                self._persist_state(state)
                raise

        raise ValueError(f"unsupported pending_stage: {pending_stage}")

    def list_messages(self, session_id: str) -> list[dict[str, Any]]:
        state = self.get_session(session_id)
        return [message.to_dict() for message in state.messages]

    def list_events(self, session_id: str) -> list[dict[str, Any]]:
        state = self.get_session(session_id)
        return [dict(item) for item in state.event_log]

    def get_case_state(self, session_id: str) -> dict[str, Any]:
        state = self.get_session(session_id)
        state.workspace.messages = state.messages
        state.workspace.handoffs = state.handoffs
        state.workspace.event_log = state.event_log
        return {
            "session_id": state.session_id,
            "role_id": state.role_id,
            "case_state": state.workspace.case_state.to_dict(),
            "case_version": state.workspace.version,
            "last_patch_results": [dict(item) for item in state.workspace.last_patch_results],
            "events": [dict(item) for item in state.event_log],
        }

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
            "has_legal_report": bool(state.legal_report_markdown.strip()),
            "legal_report_updated_at_utc": state.legal_report_updated_at_utc,
        }

    def get_legal_report(self, session_id: str) -> dict[str, str]:
        state = self.get_session(session_id)
        markdown = state.legal_report_markdown.strip()
        if not markdown:
            raise ValueError("legal report is not ready")
        ts = state.legal_report_updated_at_utc.strip() or state.updated_at_utc
        filename = f"legal-analysis-{state.session_id[:8]}.md"
        return {
            "filename": filename,
            "markdown": markdown,
            "updated_at_utc": ts,
        }

    def list_sessions(self, role_id: str | None = None) -> list[dict[str, Any]]:
        summaries: list[dict[str, Any]] = []
        expected_role = validate_role_id(role_id) if role_id is not None else None
        # snapshot to avoid "dictionary changed size during iteration" under concurrent access
        session_items = list(self.sessions.items())
        for session_id, state in session_items:
            if expected_role is not None and state.role_id != expected_role:
                continue
            summary = self.get_session_summary(session_id)
            # Override title with fast non-LLM version for listing
            summary["title"] = self._derive_session_title(state, use_llm=False)
            summaries.append(summary)
        summaries.sort(key=lambda item: str(item.get("updated_at_utc", "")), reverse=True)
        return summaries

    def delete_session(self, session_id: str) -> None:
        if session_id not in self.sessions:
            raise ValueError(f"session not found: {session_id}")
        del self.sessions[session_id]
        snapshot_path = self._session_snapshot_path(session_id)
        if snapshot_path.is_file():
            snapshot_path.unlink()

    def generate_analysis_report(self, session_id: str) -> dict[str, str]:
        state = self.get_session(session_id)
        if not state.messages:
            raise ValueError("会话中没有对话记录，无法生成报告")
        report_events: list[dict[str, Any]] = []
        self._append_event(
            state,
            "report_generation_requested",
            {"source": "generate_analysis_report_api"},
            report_events,
        )

        conversation_text = []
        for message in state.messages:
            speaker = getattr(message, "speaker_type", "unknown")
            label = "用户" if speaker == "user" else "AI顾问"
            for block in list(getattr(message, "display_blocks", []) or []):
                text = str(getattr(block, "text", "")).strip()
                if text:
                    cleaned = re.sub(r"^【[^】]*】\s*\S+\s*", "", text).strip()
                    if cleaned:
                        conversation_text.append(f"[{label}] {cleaned}")

        if not conversation_text:
            raise ValueError("会话中没有有效对话内容")

        dialogue = "\n".join(conversation_text)[:4000]

        case_snapshot = state.workspace.to_dict()
        case_prompt = state.legal_analysis_agent.build_prompt_from_case_snapshot(case_snapshot)

        prompt = f"""你是一位资深劳动法律顾问。请基于以下案件状态快照和对话记录，生成一份专业的法律分析报告。

## 稳定案件状态
{case_prompt}

## 对话记录
{dialogue}

## 报告要求
请按以下结构输出报告（使用 Markdown 格式）：

# 法律分析报告

## 一、案情简介
简要概述当事人的情况、争议背景和核心诉求。

## 二、核心法律争议焦点
列出本案涉及的 2-4 个核心法律争议焦点。

## 三、争议焦点分析

对每个争议焦点逐一分析，每个焦点必须包含：
1. 焦点概述
2. 法律分析意见
3. **具体法律依据**：引用具体的法律条文全文（如《劳动合同法》第XX条的完整条文内容），并说明该条文如何适用于本案

## 四、综合建议
给出可操作的法律建议。

## 五、参考法律文件
列出本报告引用的所有法律文件及具体条款。

---
*本报告由职引 Pilot 可溯源 AI 劳动法顾问生成，仅供参考，不构成正式法律意见。*
"""

        markdown = chat_completion(user_prompt=prompt)
        ts = now_utc_iso()
        state.legal_report_markdown = markdown
        state.legal_report_updated_at_utc = ts
        self._apply_case_patch(
            state,
            state.legal_analysis_agent.build_output_patch(
                state.workspace,
                {
                    "askmore": "no",
                    "analysis": markdown,
                    "data": {"schema_version": "1.0", "issues": [], "citations": []},
                },
            ),
            report_events,
        )
        self._append_event(
            state,
            "report_generated",
            {
                "source": "generate_analysis_report_api",
                "length": len(markdown),
                "updated_at_utc": ts,
            },
            report_events,
        )
        report_blocks = [
            DisplayBlock(
                kind="legal_report",
                title="LegalAnalysisAgent 最终分析",
                text=markdown,
            )
        ]
        self._append_agent_blocks(
            state,
            "LegalAnalysisAgent",
            report_blocks,
            report_events,
        )
        self._persist_state(state)

        filename = f"legal-analysis-{state.session_id[:8]}.md"
        return {
            "filename": filename,
            "markdown": markdown,
            "updated_at_utc": ts,
        }
