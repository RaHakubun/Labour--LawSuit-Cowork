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


class RegisterEvidencePayload(StrictModel):
    command_type: Literal["register_evidence"] = "register_evidence"
    evidence_id: UUID
    display_name: str = Field(min_length=1)
    storage_key: str = Field(min_length=1)
    media_type: str = Field(min_length=1)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    size: int = Field(ge=0)


class RequestAnalysisPayload(StrictModel):
    command_type: Literal["request_analysis"] = "request_analysis"


class CalculateRulePayload(StrictModel):
    command_type: Literal["calculate_rule"] = "calculate_rule"
    calc_type: Literal[
        "wage_base",
        "overtime",
        "severance",
        "medical_period",
        "annual_leave_unused",
        "double_wage_unsigned_contract",
    ]
    inputs: dict[str, object]
    fact_ids: list[str] = Field(default_factory=list)


class RequestDocumentPayload(StrictModel):
    command_type: Literal["request_document"] = "request_document"
    document_type: Literal["legal_analysis_report", "labour_arbitration_application"]


class CancelOperationPayload(StrictModel):
    command_type: Literal["cancel_operation"] = "cancel_operation"
    operation_id: UUID


CommandPayload = Annotated[
    SubmitUserMessagePayload
    | ConfirmFactPayload
    | RegisterEvidencePayload
    | CalculateRulePayload
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
