from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID, uuid4

from pydantic import Field

from .case_state import StrictModel, utc_now


class SubmitUserMessagePayload(StrictModel):
    command_type: Literal["submit_user_message"] = "submit_user_message"
    text: str = Field(min_length=1)


class ConfirmFactPayload(StrictModel):
    command_type: Literal["confirm_fact"] = "confirm_fact"
    fact_id: str = Field(min_length=1)
    value: object
    conflict_id: UUID | None = None


class ConfirmHandoffPayload(StrictModel):
    command_type: Literal["confirm_handoff"] = "confirm_handoff"
    handoff_id: UUID
    approve: bool


class RegisterEvidencePayload(StrictModel):
    command_type: Literal["register_evidence"] = "register_evidence"
    evidence_id: UUID


class RequestAnalysisPayload(StrictModel):
    command_type: Literal["request_analysis"] = "request_analysis"


class RequestDocumentPayload(StrictModel):
    command_type: Literal["request_document"] = "request_document"
    document_type: Literal["legal_analysis_report", "labour_arbitration_application"]


class CancelOperationPayload(StrictModel):
    command_type: Literal["cancel_operation"] = "cancel_operation"
    operation_id: UUID


CommandPayload = Annotated[
    SubmitUserMessagePayload
    | ConfirmFactPayload
    | ConfirmHandoffPayload
    | RegisterEvidencePayload
    | RequestAnalysisPayload
    | RequestDocumentPayload
    | CancelOperationPayload,
    Field(discriminator="command_type"),
]


class CaseCommand(StrictModel):
    schema_version: Literal["1.0"] = "1.0"
    command_id: UUID = Field(default_factory=uuid4)
    case_id: UUID
    actor_id: str = Field(min_length=1)
    idempotency_key: str = Field(min_length=1, max_length=200)
    expected_case_version: int = Field(ge=0)
    correlation_id: UUID = Field(default_factory=uuid4)
    created_at: datetime = Field(default_factory=utc_now)
    payload: CommandPayload

    @property
    def command_type(self) -> str:
        return self.payload.command_type
