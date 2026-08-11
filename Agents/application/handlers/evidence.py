from __future__ import annotations

from collections.abc import AsyncIterator

from Agents.domain.case_state import CaseAggregate, EvidenceItem, EvidenceStatus
from Agents.domain.commands import CaseCommand, RegisterEvidencePayload
from Agents.domain.events import EventDraft
from Agents.domain.patches import (
    CasePatch,
    AddEvidenceExtraction,
    LinkEvidenceToFact,
    RegisterEvidence,
    UpdateEvidence,
    UpsertFact,
)
from Agents.services.evidence_parser import EvidenceParser

from .base import ExecutionBatch


class EvidenceCommandHandler:
    def __init__(self, parser: EvidenceParser) -> None:
        self._parser = parser

    def supports(self, command_type: str) -> bool:
        return command_type == "register_evidence"

    async def execute(self, command: CaseCommand, aggregate: CaseAggregate) -> AsyncIterator[ExecutionBatch]:
        payload = command.payload
        if not isinstance(payload, RegisterEvidencePayload):
            raise TypeError("evidence handler received an invalid payload")
        evidence = EvidenceItem(
            evidence_id=payload.evidence_id,
            display_name=payload.display_name,
            storage_key=payload.storage_key,
            media_type=payload.media_type,
            sha256=payload.sha256,
            size=payload.size,
            status=EvidenceStatus.REGISTERED,
        )
        yield ExecutionBatch(
            patch=CasePatch(
                producer="EvidenceParser",
                base_version=aggregate.version,
                operations=[RegisterEvidence(evidence=evidence)],
            ),
            events=[EventDraft(event_type="evidence.registered", producer="EvidenceParser", visibility="user", payload={"evidence_id": str(evidence.evidence_id), "display_name": evidence.display_name, "status": evidence.status.value})],
        )
        try:
            parsed = await self._parser.parse(evidence)
        except Exception as exc:
            failed = evidence.model_copy(update={"status": EvidenceStatus.FAILED})
            yield ExecutionBatch(
                patch=CasePatch(
                    producer="EvidenceParser",
                    base_version=aggregate.version,
                    operations=[UpdateEvidence(evidence=failed)],
                ),
                events=[
                    EventDraft(
                        event_type="tool.call_failed",
                        producer="EvidenceParser",
                        visibility="user",
                        payload={
                            "tool_name": "EvidenceParser",
                            "evidence_id": str(evidence.evidence_id),
                            "message": str(exc),
                            "retryable": False,
                        },
                    )
                ],
            )
            raise
        fact_operations = [UpsertFact(fact=fact) for fact in parsed.candidate_facts]
        yield ExecutionBatch(
            patch=CasePatch(
                producer="EvidenceParser",
                base_version=aggregate.version,
                operations=[
                    UpdateEvidence(evidence=parsed.evidence),
                    AddEvidenceExtraction(extraction=parsed.extraction),
                    *fact_operations,
                ],
            ),
            events=[EventDraft(event_type="evidence.parsed", producer="EvidenceParser", visibility="user", payload={"evidence_id": str(evidence.evidence_id), "extraction_id": str(parsed.extraction.extraction_id), "parser_name": parsed.evidence.parser_name, "candidate_fact_ids": [fact.fact_id for fact in parsed.candidate_facts], "text_length": parsed.extraction.character_count})],
        )
        if parsed.candidate_facts:
            yield ExecutionBatch(
                patch=CasePatch(
                    producer="EvidenceParser",
                    base_version=aggregate.version,
                    operations=[LinkEvidenceToFact(evidence_id=evidence.evidence_id, fact_id=fact.fact_id) for fact in parsed.candidate_facts],
                )
            )
