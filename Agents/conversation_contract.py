from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from .scene_catalog import ROLE_IDS, validate_role_id


AGENT_NAMES: tuple[str, ...] = (
    "ControllerAgent",
    "ScenarioAgent",
    "LegalAnalysisAgent",
    "EmployerModuleAgent",
)


def validate_agent_name(agent_name: str) -> str:
    normalized = str(agent_name).strip()
    if normalized not in AGENT_NAMES:
        allowed = ", ".join(AGENT_NAMES)
        raise ValueError(f"invalid agent_name: {agent_name}. allowed: {allowed}")
    return normalized


@dataclass(frozen=True)
class RenderBlockPayload:
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


@dataclass(frozen=True)
class ConversationMessagePayload:
    session_id: str
    turn_id: int
    speaker_type: str
    speaker_agent: str
    role_id: str
    created_at_utc: str
    display_blocks: list[RenderBlockPayload]

    def to_dict(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "turn_id": self.turn_id,
            "speaker_type": self.speaker_type,
            "speaker_agent": self.speaker_agent,
            "role_id": self.role_id,
            "created_at_utc": self.created_at_utc,
            "display_blocks": [block.to_dict() for block in self.display_blocks],
        }


@dataclass(frozen=True)
class HandoffPayload:
    session_id: str
    turn_id: int
    from_agent: str
    to_agent: str
    reason: str
    created_at_utc: str
    scene_id: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "turn_id": self.turn_id,
            "from_agent": self.from_agent,
            "to_agent": self.to_agent,
            "reason": self.reason,
            "created_at_utc": self.created_at_utc,
            "scene_id": self.scene_id,
        }


@dataclass(frozen=True)
class SessionTurnResponse:
    session_id: str
    role_id: str
    active_agent: str
    askmore: str
    messages: list[ConversationMessagePayload]
    handoffs: list[HandoffPayload] = field(default_factory=list)
    pending_transition: dict[str, Any] | None = None
    requires_handoff_confirmation: bool = False
    events: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "role_id": self.role_id,
            "active_agent": self.active_agent,
            "askmore": self.askmore,
            "messages": [message.to_dict() for message in self.messages],
            "handoffs": [handoff.to_dict() for handoff in self.handoffs],
            "pending_transition": dict(self.pending_transition or {}) or None,
            "requires_handoff_confirmation": bool(self.requires_handoff_confirmation),
            "events": [dict(item) for item in self.events],
        }


def now_utc_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def build_message_payload(
    *,
    session_id: str,
    turn_id: int,
    speaker_type: str,
    speaker_agent: str,
    role_id: str,
    display_blocks: list[RenderBlockPayload],
    created_at_utc: str | None = None,
) -> ConversationMessagePayload:
    if speaker_type not in {"user", "agent"}:
        raise ValueError('speaker_type must be "user" or "agent"')
    validate_agent_name(speaker_agent)
    validate_role_id(role_id)
    timestamp = created_at_utc or now_utc_iso()
    return ConversationMessagePayload(
        session_id=session_id,
        turn_id=turn_id,
        speaker_type=speaker_type,
        speaker_agent=speaker_agent,
        role_id=role_id,
        created_at_utc=timestamp,
        display_blocks=list(display_blocks),
    )


def build_handoff_payload(
    *,
    session_id: str,
    turn_id: int,
    from_agent: str,
    to_agent: str,
    reason: str,
    scene_id: str = "",
    created_at_utc: str | None = None,
) -> HandoffPayload:
    validate_agent_name(from_agent)
    validate_agent_name(to_agent)
    if from_agent == to_agent:
        raise ValueError("handoff from_agent and to_agent must be different")
    if not reason.strip():
        raise ValueError("handoff reason must be non-empty")
    timestamp = created_at_utc or now_utc_iso()
    return HandoffPayload(
        session_id=session_id,
        turn_id=turn_id,
        from_agent=from_agent,
        to_agent=to_agent,
        reason=reason.strip(),
        scene_id=scene_id.strip(),
        created_at_utc=timestamp,
    )


def list_agent_names() -> list[str]:
    return list(AGENT_NAMES)


def list_role_ids() -> list[str]:
    return list(ROLE_IDS)
