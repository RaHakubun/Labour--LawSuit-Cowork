from __future__ import annotations

from collections.abc import AsyncIterator

from Agents.application.legal_models import LegalResultProvider
from Agents.domain.case_state import (
    ArtifactRevision,
    CaseAggregate,
    CaseStage,
    IssueCard,
    OutputArtifact,
)
from Agents.domain.commands import CaseCommand, RequestAnalysisPayload, RequestDocumentPayload
from Agents.domain.events import EventDraft
from Agents.domain.patches import AddArtifactRevision, CasePatch, SetInteraction, UpsertIssue
from Agents.services.context_builders import LegalAnalysisContextBuilder

from .base import ExecutionBatch


class LegalCommandHandler:
    def __init__(self, provider: LegalResultProvider) -> None:
        self._provider = provider
        self._context_builder = LegalAnalysisContextBuilder()

    def supports(self, command_type: str) -> bool:
        return command_type in {"request_analysis", "request_document"}

    async def execute(self, command: CaseCommand, aggregate: CaseAggregate) -> AsyncIterator[ExecutionBatch]:
        if isinstance(command.payload, RequestAnalysisPayload):
            async for batch in self._analyze(aggregate):
                yield batch
            return
        if isinstance(command.payload, RequestDocumentPayload):
            async for batch in self._draft(aggregate, command.payload.document_type):
                yield batch
            return
        raise TypeError("legal handler received an invalid payload")

    async def _analyze(self, aggregate: CaseAggregate) -> AsyncIterator[ExecutionBatch]:
        self._context_builder.assert_ready(aggregate)
        if aggregate.state.interaction.stage is not CaseStage.ANALYSIS_READY:
            yield ExecutionBatch(
                patch=CasePatch(
                    producer="LegalAnalysisAgent",
                    base_version=aggregate.version,
                    operations=[SetInteraction(stage=CaseStage.ANALYSIS_READY, active_agent="ControllerAgent")],
                ),
                events=[EventDraft(event_type="state.transitioned", producer="ControllerAgent", visibility="internal", payload={"to_stage": "analysis_ready", "reason": "analysis_requested"})],
            )
        yield ExecutionBatch(
            patch=CasePatch(
                producer="LegalAnalysisAgent",
                base_version=aggregate.version,
                operations=[SetInteraction(stage=CaseStage.ANALYZING, active_agent="LegalAnalysisAgent")],
            ),
            events=[EventDraft(event_type="agent.stage_started", producer="LegalAnalysisAgent", visibility="user", payload={"stage": "legal_analysis"})],
        )
        result = await self._provider.analyze(context=self._context_builder.build(aggregate))
        issues = [
            IssueCard(
                title=item.title,
                conclusion=item.conclusion,
                fact_ids=item.fact_ids,
                evidence_ids=item.evidence_ids,
                authority_ids=item.authority_ids,
            )
            for item in result.issues
        ]
        artifact = self._artifact_revision(
            aggregate,
            artifact_type="legal_analysis_report",
            title="法律分析报告",
            content=result.report_markdown,
            fact_ids=sorted({fact_id for item in result.issues for fact_id in item.fact_ids}),
            evidence_ids=sorted({evidence_id for item in result.issues for evidence_id in item.evidence_ids}, key=str),
            authority_ids=sorted({authority_id for item in result.issues for authority_id in item.authority_ids}, key=str),
        )
        yield ExecutionBatch(
            patch=CasePatch(
                producer="LegalAnalysisAgent",
                base_version=aggregate.version,
                operations=[
                    *[UpsertIssue(issue=issue) for issue in issues],
                    AddArtifactRevision(artifact=artifact),
                    SetInteraction(stage=CaseStage.DOCUMENT_READY, active_agent="ControllerAgent"),
                ],
            ),
            events=[
                EventDraft(event_type="analysis.updated", producer="LegalAnalysisAgent", visibility="user", payload={"summary": result.summary, "issue_ids": [str(item.issue_id) for item in issues]}),
                EventDraft(event_type="artifact.generated", producer="LegalAnalysisAgent", visibility="user", payload={"artifact_id": str(artifact.artifact_id), "artifact_type": artifact.artifact_type, "revision": artifact.revisions[0].revision}),
            ],
        )

    async def _draft(self, aggregate: CaseAggregate, document_type: str) -> AsyncIterator[ExecutionBatch]:
        self._context_builder.assert_ready(aggregate)
        if not aggregate.state.analysis.issues:
            raise ValueError("request analysis before generating a document")
        result = await self._provider.draft_document(
            context=self._context_builder.build(aggregate),
            document_type=document_type,
        )
        artifact = self._artifact_revision(
            aggregate,
            artifact_type=document_type,
            title=result.title,
            content=result.content,
            fact_ids=result.fact_ids,
            evidence_ids=result.evidence_ids,
            authority_ids=result.authority_ids,
            rule_result_ids=result.rule_result_ids,
        )
        yield ExecutionBatch(
            patch=CasePatch(
                producer="LegalAnalysisAgent",
                base_version=aggregate.version,
                operations=[AddArtifactRevision(artifact=artifact)],
            ),
            events=[EventDraft(event_type="artifact.generated", producer="LegalAnalysisAgent", visibility="user", payload={"artifact_id": str(artifact.artifact_id), "artifact_type": artifact.artifact_type, "revision": artifact.revisions[0].revision})],
        )

    def _artifact_revision(
        self,
        aggregate: CaseAggregate,
        *,
        artifact_type: str,
        title: str,
        content: str,
        fact_ids,
        evidence_ids,
        authority_ids,
        rule_result_ids=(),
    ) -> OutputArtifact:
        existing = next(
            (
                item
                for item in aggregate.state.outputs.artifacts.values()
                if item.artifact_type == artifact_type
            ),
            None,
        )
        revision = ArtifactRevision(
            revision=len(existing.revisions) + 1 if existing else 1,
            content=content,
            case_version=aggregate.version,
            fact_ids=list(fact_ids),
            evidence_ids=list(evidence_ids),
            authority_ids=list(authority_ids),
            rule_result_ids=list(rule_result_ids),
        )
        if existing is not None:
            return OutputArtifact(
                artifact_id=existing.artifact_id,
                artifact_type=artifact_type,
                title=title,
                revisions=[revision],
            )
        return OutputArtifact(
            artifact_type=artifact_type,
            title=title,
            revisions=[revision],
        )
