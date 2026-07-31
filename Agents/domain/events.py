from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID, uuid4

from pydantic import Field, field_validator

from .case_state import StrictModel, utc_now


EVENT_TYPES = frozenset(
    {
        "command.accepted",
        "command.rejected",
        "operation.started",
        "operation.completed",
        "operation.failed",
        "message.received",
        "agent.stage_started",
        "agent.token_delta",
        "agent.output_received",
        "tool.call_started",
        "tool.call_completed",
        "tool.call_failed",
        "patch.submitted",
        "patch.applied",
        "patch.rejected",
        "state.transitioned",
        "clarification.requested",
        "handoff.requested",
        "handoff.confirmed",
        "handoff.rejected",
        "evidence.registered",
        "evidence.parsed",
        "fact.confirmation_requested",
        "fact.confirmed",
        "analysis.updated",
        "artifact.generated",
    }
)


class EventEnvelope(StrictModel):
    schema_version: Literal["1.0"] = "1.0"
    event_id: UUID = Field(default_factory=uuid4)
    sequence: int = Field(default=0, ge=0)
    case_id: UUID
    command_id: UUID
    correlation_id: UUID
    causation_id: UUID | None = None
    turn_id: int | None = Field(default=None, ge=1)
    case_version: int = Field(ge=0)
    event_type: str
    producer: str = Field(min_length=1)
    visibility: Literal["internal", "user"]
    occurred_at: datetime = Field(default_factory=utc_now)
    payload: dict[str, Any] = Field(default_factory=dict)

    @field_validator("event_type")
    @classmethod
    def known_event_type(cls, value: str) -> str:
        if value not in EVENT_TYPES:
            raise ValueError(f"unsupported event_type: {value}")
        return value


class EventDraft(StrictModel):
    event_type: str
    producer: str = Field(min_length=1)
    visibility: Literal["internal", "user"]
    payload: dict[str, Any] = Field(default_factory=dict)
    causation_id: UUID | None = None

    @field_validator("event_type")
    @classmethod
    def known_event_type(cls, value: str) -> str:
        if value not in EVENT_TYPES:
            raise ValueError(f"unsupported event_type: {value}")
        return value
