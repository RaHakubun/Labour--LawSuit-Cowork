from __future__ import annotations

from typing import Annotated, Literal
from uuid import UUID, uuid4

from pydantic import Field

from .case_state import (
    AuthorityRef,
    CaseStage,
    EvidenceItem,
    FactItem,
    IssueCard,
    OutputArtifact,
    PendingQuestion,
    RuleResult,
    StrictModel,
)


class SetInteraction(StrictModel):
    operation_type: Literal["set_interaction"] = "set_interaction"
    stage: CaseStage | None = None
    active_agent: str | None = None
    current_goal: str | None = None
    active_scene_id: str | None = None
    last_user_input: str | None = None
    pending_questions: list[PendingQuestion] | None = None
    blocked_on: list[str] | None = None


class UpsertFact(StrictModel):
    operation_type: Literal["upsert_fact"] = "upsert_fact"
    fact: FactItem


class ResolveFactConflict(StrictModel):
    operation_type: Literal["resolve_fact_conflict"] = "resolve_fact_conflict"
    conflict_id: UUID
    confirmed_fact: FactItem
    resolution_note: str = Field(min_length=1)


class RegisterEvidence(StrictModel):
    operation_type: Literal["register_evidence"] = "register_evidence"
    evidence: EvidenceItem


class LinkEvidenceToFact(StrictModel):
    operation_type: Literal["link_evidence_to_fact"] = "link_evidence_to_fact"
    evidence_id: UUID
    fact_id: str = Field(min_length=1)


class UpsertIssue(StrictModel):
    operation_type: Literal["upsert_issue"] = "upsert_issue"
    issue: IssueCard


class AddAuthority(StrictModel):
    operation_type: Literal["add_authority"] = "add_authority"
    authority: AuthorityRef


class AddRuleResult(StrictModel):
    operation_type: Literal["add_rule_result"] = "add_rule_result"
    rule_result: RuleResult


class AddArtifactRevision(StrictModel):
    operation_type: Literal["add_artifact_revision"] = "add_artifact_revision"
    artifact: OutputArtifact


CasePatchOperation = Annotated[
    SetInteraction
    | UpsertFact
    | ResolveFactConflict
    | RegisterEvidence
    | LinkEvidenceToFact
    | UpsertIssue
    | AddAuthority
    | AddRuleResult
    | AddArtifactRevision,
    Field(discriminator="operation_type"),
]


class CasePatch(StrictModel):
    schema_version: Literal["1.0"] = "1.0"
    patch_id: UUID = Field(default_factory=uuid4)
    producer: str = Field(min_length=1)
    base_version: int = Field(ge=0)
    operations: list[CasePatchOperation] = Field(min_length=1)


class PatchResult(StrictModel):
    patch_id: UUID
    accepted: bool
    base_version: int
    new_version: int
    applied_operations: int = 0
    errors: list[str] = Field(default_factory=list)
