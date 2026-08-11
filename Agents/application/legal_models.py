from __future__ import annotations

from typing import Protocol
from uuid import UUID

from pydantic import Field

from Agents.domain.case_state import StrictModel


class LegalIssueResult(StrictModel):
    title: str = Field(min_length=1)
    conclusion: str = Field(min_length=1)
    fact_ids: list[str] = Field(min_length=1)
    evidence_ids: list[UUID] = Field(default_factory=list)
    authority_ids: list[UUID] = Field(min_length=1)
    rule_result_ids: list[UUID] = Field(default_factory=list)


class LegalAnalysisResult(StrictModel):
    summary: str = Field(min_length=1)
    report_markdown: str = Field(min_length=1)
    issues: list[LegalIssueResult] = Field(min_length=1)


class DocumentDraftResult(StrictModel):
    title: str = Field(min_length=1)
    content: str = Field(min_length=1)
    fact_ids: list[str] = Field(min_length=1)
    evidence_ids: list[UUID] = Field(default_factory=list)
    authority_ids: list[UUID] = Field(min_length=1)
    rule_result_ids: list[UUID] = Field(default_factory=list)


class LegalResultProvider(Protocol):
    async def analyze(self, *, context: dict[str, object]) -> LegalAnalysisResult: ...

    async def draft_document(
        self,
        *,
        context: dict[str, object],
        document_type: str,
    ) -> DocumentDraftResult: ...
