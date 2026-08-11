from __future__ import annotations

from datetime import datetime, timezone
from enum import StrEnum
from typing import Any, Final, Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator


SCHEMA_VERSION: Final[Literal["2.0"]] = "2.0"


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True)


class CaseStage(StrEnum):
    INTAKE = "intake"
    FACT_COLLECTING = "fact_collecting"
    EVIDENCE_PROCESSING = "evidence_processing"
    ANALYSIS_READY = "analysis_ready"
    ANALYZING = "analyzing"
    DOCUMENT_READY = "document_ready"
    COMPLETED = "completed"


class FactStatus(StrEnum):
    CLAIMED = "claimed"
    PENDING_VERIFICATION = "pending_verification"
    CONFIRMED = "confirmed"
    INFERRED = "inferred"
    DISPUTED = "disputed"


class PendingQuestion(StrictModel):
    schema_version: Literal["2.0"] = SCHEMA_VERSION
    question_id: UUID = Field(default_factory=uuid4)
    text: str = Field(min_length=1)
    required_fact_ids: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=utc_now)


class PendingConfirmation(StrictModel):
    schema_version: Literal["2.0"] = SCHEMA_VERSION
    confirmation_id: UUID = Field(default_factory=uuid4)
    confirmation_type: Literal["fact", "material_authorization", "high_impact_action"]
    prompt: str = Field(min_length=1)
    target_ids: list[str] = Field(min_length=1)
    created_at: datetime = Field(default_factory=utc_now)


class InteractionState(StrictModel):
    schema_version: Literal["2.0"] = SCHEMA_VERSION
    stage: CaseStage = CaseStage.INTAKE
    active_agent: str = "ControllerAgent"
    user_role: str = ""
    current_goal: str = ""
    active_scene_id: str = ""
    last_user_input: str = ""
    pending_questions: list[PendingQuestion] = Field(default_factory=list)
    pending_confirmation: PendingConfirmation | None = None
    blocked_on: list[str] = Field(default_factory=list)


class FactSource(StrictModel):
    schema_version: Literal["2.0"] = SCHEMA_VERSION
    kind: Literal["user", "agent", "evidence", "rule"]
    ref_id: str = Field(min_length=1)


class FactItem(StrictModel):
    schema_version: Literal["2.0"] = SCHEMA_VERSION
    fact_id: str = Field(min_length=1)
    value: Any
    status: FactStatus
    source: FactSource
    confidence: float | None = Field(default=None, ge=0, le=1)
    derivation: str = ""
    updated_at: datetime = Field(default_factory=utc_now)

    @model_validator(mode="after")
    def inferred_fact_has_basis(self) -> "FactItem":
        if self.status is FactStatus.INFERRED:
            if self.confidence is None or not self.derivation.strip():
                raise ValueError("inferred fact requires confidence and derivation")
        return self


class FactConflict(StrictModel):
    schema_version: Literal["2.0"] = SCHEMA_VERSION
    conflict_id: UUID = Field(default_factory=uuid4)
    fact_id: str = Field(min_length=1)
    existing: FactItem
    incoming: FactItem
    resolved: bool = False
    resolution_note: str = ""
    created_at: datetime = Field(default_factory=utc_now)


class FactsState(StrictModel):
    schema_version: Literal["2.0"] = SCHEMA_VERSION
    items: dict[str, FactItem] = Field(default_factory=dict)
    conflicts: dict[UUID, FactConflict] = Field(default_factory=dict)


class EvidenceStatus(StrEnum):
    REGISTERED = "registered"
    PARSING = "parsing"
    PARSED = "parsed"
    FAILED = "failed"


class EvidenceItem(StrictModel):
    schema_version: Literal["2.0"] = SCHEMA_VERSION
    evidence_id: UUID = Field(default_factory=uuid4)
    display_name: str = Field(min_length=1)
    storage_key: str = Field(min_length=1)
    media_type: str = Field(min_length=1)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    size: int = Field(ge=0)
    status: EvidenceStatus = EvidenceStatus.REGISTERED
    extracted_text_ref: str = ""
    parser_name: str = ""
    parsed_at: datetime | None = None
    linked_fact_ids: list[str] = Field(default_factory=list)
    authenticity_risk: str = ""


class EvidenceExtraction(StrictModel):
    schema_version: Literal["2.0"] = SCHEMA_VERSION
    extraction_id: UUID = Field(default_factory=uuid4)
    evidence_id: UUID
    text_ref: str = Field(min_length=1)
    content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    parser_name: str = Field(min_length=1)
    character_count: int = Field(ge=1)
    page_count: int | None = Field(default=None, ge=1)
    created_at: datetime = Field(default_factory=utc_now)


class FactEvidenceLink(StrictModel):
    schema_version: Literal["2.0"] = SCHEMA_VERSION
    link_id: UUID = Field(default_factory=uuid4)
    evidence_id: UUID
    fact_id: str = Field(min_length=1)
    excerpt_ref: str = ""
    confidence: float | None = Field(default=None, ge=0, le=1)


class EvidenceState(StrictModel):
    schema_version: Literal["2.0"] = SCHEMA_VERSION
    items: dict[UUID, EvidenceItem] = Field(default_factory=dict)
    extractions: dict[UUID, EvidenceExtraction] = Field(default_factory=dict)
    fact_links: dict[UUID, FactEvidenceLink] = Field(default_factory=dict)


class AuthorityRef(StrictModel):
    schema_version: Literal["2.0"] = SCHEMA_VERSION
    authority_id: UUID = Field(default_factory=uuid4)
    tool_name: str = Field(min_length=1)
    query: str = Field(min_length=1)
    source_id: str = Field(min_length=1)
    content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    title: str = ""
    source_url: str = ""
    excerpt: str = ""
    parsed_status: Literal["parsed"] = "parsed"
    retrieved_at: datetime = Field(default_factory=utc_now)


class IssueCard(StrictModel):
    schema_version: Literal["2.0"] = SCHEMA_VERSION
    issue_id: UUID = Field(default_factory=uuid4)
    title: str = Field(min_length=1)
    conclusion: str = ""
    fact_ids: list[str] = Field(default_factory=list)
    evidence_ids: list[UUID] = Field(default_factory=list)
    authority_ids: list[UUID] = Field(default_factory=list)


class RuleResult(StrictModel):
    schema_version: Literal["2.0"] = SCHEMA_VERSION
    result_id: UUID = Field(default_factory=uuid4)
    rule: str = Field(min_length=1)
    input_case_version: int = Field(ge=0)
    result: dict[str, Any]
    unit: str = ""
    rounding: str = ""
    fact_ids: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=utc_now)


class AnalysisNote(StrictModel):
    schema_version: Literal["2.0"] = SCHEMA_VERSION
    note_id: UUID = Field(default_factory=uuid4)
    content: str = Field(min_length=1)
    fact_ids: list[str] = Field(default_factory=list)
    evidence_ids: list[UUID] = Field(default_factory=list)
    authority_ids: list[UUID] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=utc_now)


class MissingInformation(StrictModel):
    schema_version: Literal["2.0"] = SCHEMA_VERSION
    information_id: UUID = Field(default_factory=uuid4)
    description: str = Field(min_length=1)
    required_fact_ids: list[str] = Field(default_factory=list)
    blocking: bool = True


class AnalysisState(StrictModel):
    schema_version: Literal["2.0"] = SCHEMA_VERSION
    issues: dict[UUID, IssueCard] = Field(default_factory=dict)
    authorities: dict[UUID, AuthorityRef] = Field(default_factory=dict)
    rule_results: dict[UUID, RuleResult] = Field(default_factory=dict)
    notes: dict[UUID, AnalysisNote] = Field(default_factory=dict)
    missing_information: list[MissingInformation] = Field(default_factory=list)


class ArtifactRevision(StrictModel):
    schema_version: Literal["2.0"] = SCHEMA_VERSION
    revision: int = Field(ge=1)
    content: str = Field(min_length=1)
    case_version: int = Field(ge=0)
    fact_ids: list[str] = Field(default_factory=list)
    evidence_ids: list[UUID] = Field(default_factory=list)
    authority_ids: list[UUID] = Field(default_factory=list)
    rule_result_ids: list[UUID] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=utc_now)


class OutputArtifact(StrictModel):
    schema_version: Literal["2.0"] = SCHEMA_VERSION
    artifact_id: UUID = Field(default_factory=uuid4)
    artifact_type: str = Field(min_length=1)
    title: str = Field(min_length=1)
    revisions: list[ArtifactRevision] = Field(min_length=1)
    stale: bool = False


class OutputsState(StrictModel):
    schema_version: Literal["2.0"] = SCHEMA_VERSION
    artifacts: dict[UUID, OutputArtifact] = Field(default_factory=dict)


class CaseState(StrictModel):
    schema_version: Literal["2.0"] = SCHEMA_VERSION
    interaction: InteractionState = Field(default_factory=InteractionState)
    facts: FactsState = Field(default_factory=FactsState)
    evidence: EvidenceState = Field(default_factory=EvidenceState)
    analysis: AnalysisState = Field(default_factory=AnalysisState)
    outputs: OutputsState = Field(default_factory=OutputsState)


class CaseAggregate(StrictModel):
    schema_version: Literal["2.0"] = SCHEMA_VERSION
    case_id: UUID = Field(default_factory=uuid4)
    owner_id: str = Field(min_length=1)
    role_id: str = Field(min_length=1)
    version: int = Field(default=0, ge=0)
    state: CaseState = Field(default_factory=CaseState)
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)

    @classmethod
    def create(cls, *, owner_id: str, role_id: str) -> "CaseAggregate":
        aggregate = cls(owner_id=owner_id, role_id=role_id)
        aggregate.state.interaction.user_role = role_id
        return aggregate
