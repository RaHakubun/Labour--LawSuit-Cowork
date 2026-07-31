from __future__ import annotations

from typing import Any

from Agents.domain.case_state import CaseAggregate, EvidenceStatus, FactStatus


class LegalAnalysisContextBuilder:
    """Builds the only state projection LegalAnalysisAgent is allowed to consume."""

    def build(self, aggregate: CaseAggregate) -> dict[str, Any]:
        facts = {
            status.value: [
                item.model_dump(mode="json")
                for item in aggregate.state.facts.items.values()
                if item.status is status
            ]
            for status in FactStatus
        }
        return {
            "case_id": str(aggregate.case_id),
            "case_version": aggregate.version,
            "role_id": aggregate.role_id,
            "scene_id": aggregate.state.interaction.active_scene_id,
            "goal": aggregate.state.interaction.current_goal,
            "facts": facts,
            "evidence": [
                {
                    **item.model_dump(mode="json", exclude={"extracted_text"}),
                    "extracted_text": item.extracted_text[:20_000],
                }
                for item in aggregate.state.evidence.items.values()
                if item.status is EvidenceStatus.PARSED
            ],
            "authorities": [
                item.model_dump(mode="json")
                for item in aggregate.state.analysis.authorities.values()
            ],
            "rule_results": [
                item.model_dump(mode="json")
                for item in aggregate.state.analysis.rule_results.values()
            ],
            "existing_issues": [
                item.model_dump(mode="json")
                for item in aggregate.state.analysis.issues.values()
            ],
        }

    def assert_ready(self, aggregate: CaseAggregate) -> None:
        usable_facts = [
            item
            for item in aggregate.state.facts.items.values()
            if item.status in {FactStatus.CLAIMED, FactStatus.CONFIRMED, FactStatus.INFERRED}
        ]
        unresolved = [
            str(item.conflict_id)
            for item in aggregate.state.facts.conflicts.values()
            if not item.resolved
        ]
        if not usable_facts:
            raise ValueError("legal analysis requires at least one usable fact")
        if unresolved:
            raise ValueError("legal analysis blocked by unresolved fact conflicts")
        if not aggregate.state.analysis.authorities:
            raise ValueError("legal analysis requires at least one parsed authority")
