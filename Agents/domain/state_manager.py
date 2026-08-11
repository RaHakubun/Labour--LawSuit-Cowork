from __future__ import annotations

from typing import Iterable
from uuid import UUID

from pydantic import ValidationError

from .case_state import (
    CaseAggregate,
    CaseState,
    FactConflict,
    FactEvidenceLink,
    FactStatus,
    utc_now,
)
from .patches import (
    AddArtifactRevision,
    AddAuthority,
    AddEvidenceExtraction,
    AddRuleResult,
    CasePatch,
    CasePatchOperation,
    LinkEvidenceToFact,
    PatchResult,
    RegisterEvidence,
    UpdateEvidence,
    ResolveFactConflict,
    SetInteraction,
    UpsertFact,
    UpsertIssue,
)
from .transitions import validate_transition


PRODUCER_OPERATIONS: dict[str, set[type[CasePatchOperation]]] = {
    "ControllerAgent": {SetInteraction, UpsertFact},
    "ScenarioAgent": {SetInteraction, UpsertFact, AddAuthority},
    "EvidenceParser": {
        RegisterEvidence,
        UpdateEvidence,
        AddEvidenceExtraction,
        LinkEvidenceToFact,
        UpsertFact,
    },
    "RuleCalculator": {AddRuleResult},
    "LegalAnalysisAgent": {SetInteraction, UpsertIssue, AddArtifactRevision},
    "StateManager": {SetInteraction, ResolveFactConflict},
    "User": {SetInteraction, ResolveFactConflict, UpsertFact},
}


class DomainStateManager:
    """The only write gate for strongly typed CaseState.

    Validation and mutation happen on a deep copy. The live aggregate is replaced
    only after the complete patch passes validation, so rejected patches cannot
    leak partial state.
    """

    def apply_patch(self, aggregate: CaseAggregate, patch: CasePatch) -> PatchResult:
        errors = self._validate_patch(aggregate, patch)
        if errors:
            return self._rejected(patch, aggregate.version, errors)

        candidate = aggregate.model_copy(deep=True)
        try:
            for operation in patch.operations:
                self._apply_operation(candidate, patch.producer, operation)
            CaseState.model_validate(candidate.state.model_dump(mode="python"))
        except (ValueError, ValidationError, KeyError) as exc:
            return self._rejected(patch, aggregate.version, [str(exc)])

        candidate.version += 1
        candidate.updated_at = utc_now()
        aggregate.state = candidate.state
        aggregate.version = candidate.version
        aggregate.updated_at = candidate.updated_at
        return PatchResult(
            patch_id=patch.patch_id,
            accepted=True,
            base_version=patch.base_version,
            new_version=aggregate.version,
            applied_operations=len(patch.operations),
        )

    def _validate_patch(self, aggregate: CaseAggregate, patch: CasePatch) -> list[str]:
        errors: list[str] = []
        if patch.base_version != aggregate.version:
            errors.append(
                f"patch base_version={patch.base_version} does not match "
                f"case version={aggregate.version}"
            )
        allowed = PRODUCER_OPERATIONS.get(patch.producer)
        if allowed is None:
            errors.append(f"unknown patch producer: {patch.producer}")
            return errors
        for operation in patch.operations:
            if type(operation) not in allowed:
                errors.append(
                    f"{patch.producer} cannot apply {operation.operation_type}"
                )
            errors.extend(self._validate_operation(aggregate, patch.producer, operation))
        return errors

    def _validate_operation(
        self,
        aggregate: CaseAggregate,
        producer: str,
        operation: CasePatchOperation,
    ) -> Iterable[str]:
        if isinstance(operation, UpsertFact):
            fact = operation.fact
            if fact.status is FactStatus.CONFIRMED and producer not in {"User", "StateManager"}:
                yield f"{producer} cannot create confirmed facts"
            if fact.fact_id.startswith("legal."):
                yield "legal conclusions cannot be written to facts"
        elif isinstance(operation, ResolveFactConflict):
            if operation.confirmed_fact.status is not FactStatus.CONFIRMED:
                yield "resolved conflict must produce a confirmed fact"
            conflict = aggregate.state.facts.conflicts.get(operation.conflict_id)
            if conflict is None:
                yield f"fact conflict not found: {operation.conflict_id}"
            elif conflict.resolved:
                yield f"fact conflict already resolved: {operation.conflict_id}"
        elif isinstance(operation, LinkEvidenceToFact):
            if operation.evidence_id not in aggregate.state.evidence.items:
                yield f"evidence not found: {operation.evidence_id}"
            if operation.fact_id not in aggregate.state.facts.items:
                yield f"fact not found: {operation.fact_id}"
        elif isinstance(operation, UpdateEvidence):
            if operation.evidence.evidence_id not in aggregate.state.evidence.items:
                yield f"evidence not found: {operation.evidence.evidence_id}"
        elif isinstance(operation, AddEvidenceExtraction):
            if operation.extraction.evidence_id not in aggregate.state.evidence.items:
                yield f"evidence not found: {operation.extraction.evidence_id}"
        elif isinstance(operation, UpsertIssue):
            yield from self._validate_references(
                aggregate,
                operation.issue.fact_ids,
                operation.issue.evidence_ids,
                operation.issue.authority_ids,
            )
            yield from self._validate_rule_result_references(
                aggregate,
                operation.issue.rule_result_ids,
            )
        elif isinstance(operation, AddRuleResult):
            yield from self._validate_references(
                aggregate,
                operation.rule_result.fact_ids,
                (),
                (),
            )
            if operation.rule_result.input_case_version != aggregate.version:
                yield (
                    "rule result input_case_version="
                    f"{operation.rule_result.input_case_version} does not match "
                    f"case version={aggregate.version}"
                )
        elif isinstance(operation, AddArtifactRevision):
            for revision in operation.artifact.revisions:
                yield from self._validate_references(
                    aggregate,
                    revision.fact_ids,
                    revision.evidence_ids,
                    revision.authority_ids,
                )
                if revision.case_version != aggregate.version:
                    yield (
                        f"artifact revision case_version={revision.case_version} does not "
                        f"match case version={aggregate.version}"
                    )
                yield from self._validate_rule_result_references(
                    aggregate,
                    revision.rule_result_ids,
                )

    def _validate_rule_result_references(
        self,
        aggregate: CaseAggregate,
        result_ids: Iterable[UUID],
    ) -> Iterable[str]:
        for result_id in result_ids:
            result = aggregate.state.analysis.rule_results.get(result_id)
            if result is None:
                yield f"rule result reference not found: {result_id}"
            elif result.stale:
                yield f"stale rule result cannot support analysis: {result_id}"

    def _validate_references(
        self,
        aggregate: CaseAggregate,
        fact_ids: Iterable[str],
        evidence_ids: Iterable[object],
        authority_ids: Iterable[object],
    ) -> Iterable[str]:
        for fact_id in fact_ids:
            fact = aggregate.state.facts.items.get(fact_id)
            if fact is None:
                yield f"fact reference not found: {fact_id}"
            elif fact.status is FactStatus.DISPUTED:
                yield f"disputed fact cannot support analysis: {fact_id}"
        for evidence_id in evidence_ids:
            if evidence_id not in aggregate.state.evidence.items:
                yield f"evidence reference not found: {evidence_id}"
        for authority_id in authority_ids:
            if authority_id not in aggregate.state.analysis.authorities:
                yield f"authority reference not found: {authority_id}"

    def _apply_operation(
        self,
        aggregate: CaseAggregate,
        producer: str,
        operation: CasePatchOperation,
    ) -> None:
        state = aggregate.state
        if isinstance(operation, SetInteraction):
            interaction = state.interaction
            if operation.stage is not None:
                validate_transition(interaction.stage, operation.stage)
                interaction.stage = operation.stage
            if operation.active_agent is not None:
                interaction.active_agent = operation.active_agent
            if operation.current_goal is not None:
                interaction.current_goal = operation.current_goal
            if operation.active_scene_id is not None:
                interaction.active_scene_id = operation.active_scene_id
            if operation.last_user_input is not None:
                interaction.last_user_input = operation.last_user_input
            if operation.pending_questions is not None:
                interaction.pending_questions = operation.pending_questions
            if operation.clear_pending_confirmation:
                interaction.pending_confirmation = None
            elif operation.pending_confirmation is not None:
                interaction.pending_confirmation = operation.pending_confirmation
            if operation.blocked_on is not None:
                interaction.blocked_on = operation.blocked_on
            return

        if isinstance(operation, UpsertFact):
            incoming = operation.fact
            existing = state.facts.items.get(incoming.fact_id)
            if existing is not None and existing.value != incoming.value:
                if producer == "User" and incoming.status is FactStatus.CONFIRMED:
                    state.facts.items[incoming.fact_id] = incoming
                    self._mark_outputs_stale(state, fact_ids={incoming.fact_id})
                    return
                existing.status = FactStatus.DISPUTED
                conflict = FactConflict(
                    fact_id=incoming.fact_id,
                    existing=existing.model_copy(deep=True),
                    incoming=incoming,
                )
                state.facts.conflicts[conflict.conflict_id] = conflict
                self._mark_outputs_stale(state, fact_ids={incoming.fact_id})
                return
            state.facts.items[incoming.fact_id] = incoming
            self._mark_outputs_stale(state, fact_ids={incoming.fact_id})
            return

        if isinstance(operation, ResolveFactConflict):
            conflict = state.facts.conflicts[operation.conflict_id]
            conflict.resolved = True
            conflict.resolution_note = operation.resolution_note
            state.facts.items[operation.confirmed_fact.fact_id] = operation.confirmed_fact
            self._mark_outputs_stale(
                state,
                fact_ids={operation.confirmed_fact.fact_id},
            )
            return

        if isinstance(operation, RegisterEvidence):
            evidence = operation.evidence
            if evidence.evidence_id in state.evidence.items:
                raise ValueError(f"evidence already registered: {evidence.evidence_id}")
            state.evidence.items[evidence.evidence_id] = evidence
            return

        if isinstance(operation, UpdateEvidence):
            state.evidence.items[operation.evidence.evidence_id] = operation.evidence
            self._mark_outputs_stale(
                state,
                evidence_ids={operation.evidence.evidence_id},
            )
            return

        if isinstance(operation, AddEvidenceExtraction):
            extraction = operation.extraction
            if extraction.extraction_id in state.evidence.extractions:
                raise ValueError(
                    f"evidence extraction already exists: {extraction.extraction_id}"
                )
            state.evidence.extractions[extraction.extraction_id] = extraction
            return

        if isinstance(operation, LinkEvidenceToFact):
            evidence = state.evidence.items[operation.evidence_id]
            if operation.fact_id not in evidence.linked_fact_ids:
                evidence.linked_fact_ids.append(operation.fact_id)
            existing_link = next(
                (
                    item
                    for item in state.evidence.fact_links.values()
                    if item.evidence_id == operation.evidence_id
                    and item.fact_id == operation.fact_id
                ),
                None,
            )
            if existing_link is None:
                link = FactEvidenceLink(
                    evidence_id=operation.evidence_id,
                    fact_id=operation.fact_id,
                )
                state.evidence.fact_links[link.link_id] = link
            return

        if isinstance(operation, UpsertIssue):
            state.analysis.issues[operation.issue.issue_id] = operation.issue
            return

        if isinstance(operation, AddAuthority):
            authority = operation.authority
            state.analysis.authorities[authority.authority_id] = authority
            return

        if isinstance(operation, AddRuleResult):
            result = operation.rule_result
            state.analysis.rule_results[result.result_id] = result
            return

        if isinstance(operation, AddArtifactRevision):
            artifact = operation.artifact
            existing_artifact = state.outputs.artifacts.get(artifact.artifact_id)
            if existing_artifact is None:
                state.outputs.artifacts[artifact.artifact_id] = artifact
            else:
                expected = len(existing_artifact.revisions) + 1
                for revision in artifact.revisions:
                    if revision.revision != expected:
                        raise ValueError(
                            f"artifact revision must be {expected}, got {revision.revision}"
                        )
                    existing_artifact.revisions.append(revision)
                    expected += 1
                existing_artifact.stale = False
            return

        raise ValueError(f"unsupported patch operation: {operation}")

    def _mark_outputs_stale(
        self,
        state: CaseState,
        *,
        fact_ids: set[str] | None = None,
        evidence_ids: set[object] | None = None,
    ) -> None:
        changed_facts = fact_ids or set()
        changed_evidence = evidence_ids or set()
        for result in state.analysis.rule_results.values():
            if changed_facts.intersection(result.fact_ids):
                result.stale = True
        for artifact in state.outputs.artifacts.values():
            revision = artifact.revisions[-1]
            if changed_facts.intersection(revision.fact_ids) or changed_evidence.intersection(
                revision.evidence_ids
            ):
                artifact.stale = True

    def _rejected(
        self,
        patch: CasePatch,
        current_version: int,
        errors: list[str],
    ) -> PatchResult:
        return PatchResult(
            patch_id=patch.patch_id,
            accepted=False,
            base_version=patch.base_version,
            new_version=current_version,
            errors=errors,
        )
