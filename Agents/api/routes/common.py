from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any, TypeAlias
from uuid import UUID

from Agents.domain.case_state import CaseAggregate

OwnedCase: TypeAlias = Callable[[UUID, str], Awaitable[CaseAggregate]]


def case_summary(aggregate: CaseAggregate) -> dict[str, Any]:
    interaction = aggregate.state.interaction
    return {
        "case_id": str(aggregate.case_id),
        "owner_id": aggregate.owner_id,
        "role_id": aggregate.role_id,
        "version": aggregate.version,
        "stage": interaction.stage.value,
        "active_agent": interaction.active_agent,
        "active_scene_id": interaction.active_scene_id or None,
        "current_goal": interaction.current_goal,
        "pending_questions": [
            item.model_dump(mode="json") for item in interaction.pending_questions
        ],
        "pending_confirmation": (
            interaction.pending_confirmation.model_dump(mode="json")
            if interaction.pending_confirmation
            else None
        ),
        "candidate_facts": [
            item.model_dump(mode="json")
            for item in aggregate.state.facts.items.values()
        ],
        "authorities": [
            {
                "authority_id": str(item.authority_id),
                "tool_name": item.tool_name,
                "query": item.query,
                "source_id": item.source_id,
                "title": item.title,
                "source_url": item.source_url,
                "content_hash": item.content_hash,
                "parsed_status": item.parsed_status,
                "retrieved_at": item.retrieved_at.isoformat(),
            }
            for item in aggregate.state.analysis.authorities.values()
        ],
        "evidence": [
            item.model_dump(mode="json")
            for item in aggregate.state.evidence.items.values()
        ],
        "fact_conflicts": [
            item.model_dump(mode="json")
            for item in aggregate.state.facts.conflicts.values()
            if not item.resolved
        ],
        "issues": [
            item.model_dump(mode="json")
            for item in aggregate.state.analysis.issues.values()
        ],
        "rule_results": [
            item.model_dump(mode="json")
            for item in aggregate.state.analysis.rule_results.values()
        ],
        "analysis_notes": [
            item.model_dump(mode="json")
            for item in aggregate.state.analysis.notes.values()
        ],
        "missing_information": [
            item.model_dump(mode="json")
            for item in aggregate.state.analysis.missing_information
        ],
        "artifacts": [
            {
                "artifact_id": str(item.artifact_id),
                "artifact_type": item.artifact_type,
                "title": item.title,
                "stale": item.stale,
                "revision_count": len(item.revisions),
            }
            for item in aggregate.state.outputs.artifacts.values()
        ],
        "updated_at": aggregate.updated_at.isoformat(),
    }
