from __future__ import annotations

from collections.abc import AsyncIterator

from Agents.application.scenario_models import ScenarioResultProvider
from Agents.domain.case_state import (
    AuthorityRef,
    CaseAggregate,
    FactItem,
    FactSource,
    FactStatus,
    PendingQuestion,
)
from Agents.domain.commands import CaseCommand
from Agents.domain.events import EventDraft
from Agents.domain.patches import (
    AddAuthority,
    CasePatch,
    CasePatchOperation,
    SetInteraction,
    UpsertFact,
)
from Agents.scene_catalog import validate_scene_id
from Agents.services.tool_hub import ToolExecutionError, ToolHub

from .base import ExecutionBatch


class ScenarioStageHandler:
    def __init__(
        self,
        *,
        scenario_provider: ScenarioResultProvider,
        tool_hub: ToolHub,
    ) -> None:
        self._scenario_provider = scenario_provider
        self._tool_hub = tool_hub

    async def execute_stage(
        self,
        command: CaseCommand,
        aggregate: CaseAggregate,
        *,
        scene_id: str,
    ) -> AsyncIterator[ExecutionBatch]:
        scene_id = validate_scene_id(scene_id)

        result = await self._scenario_provider.analyze(
            role_id=aggregate.role_id,
            scene_id=scene_id,
            case_state=aggregate.state.model_copy(deep=True),
        )
        operations: list[CasePatchOperation] = [
            UpsertFact(
                fact=FactItem(
                    fact_id=fact.fact_id,
                    value=fact.value,
                    status=FactStatus.CLAIMED,
                    source=FactSource(kind="agent", ref_id=str(command.command_id)),
                    confidence=fact.confidence,
                    derivation=fact.derivation,
                )
            )
            for fact in result.candidate_facts
        ]
        questions = [PendingQuestion(text=text) for text in result.missing_fact_questions]
        operations.append(
            SetInteraction(
                active_agent="ControllerAgent",
                pending_questions=questions,
                blocked_on=[str(item.question_id) for item in questions],
            )
        )
        output_payload = {
            "scene_id": result.scene_id,
            "confidence": result.confidence,
            "summary": result.summary,
            "candidate_fact_ids": [
                item.fact_id for item in result.candidate_facts
            ],
            "evidence_requirements": [
                item.model_dump(mode="json")
                for item in result.evidence_requirements
            ],
            "rule_calculation_requests": [
                item.model_dump(mode="json")
                for item in result.rule_calculation_requests
            ],
        }
        events = [
            EventDraft(
                event_type="agent.output_received",
                producer="ScenarioAgent",
                visibility="internal",
                payload=output_payload,
            ),
            EventDraft(
                event_type="agent.output_received",
                producer="ControllerAgent",
                visibility="user",
                payload={
                    "source_agent": "ScenarioAgent",
                    **output_payload,
                },
            ),
        ]
        if questions:
            events.append(
                EventDraft(
                    event_type="clarification.requested",
                    producer="ControllerAgent",
                    visibility="user",
                    payload={
                        "questions": [
                            question.model_dump(mode="json")
                            for question in questions
                        ],
                        "reason": "scenario_missing_facts",
                    },
                )
            )
        yield ExecutionBatch(
            patch=CasePatch(
                producer="ScenarioAgent",
                base_version=aggregate.version,
                operations=operations,
            ),
            events=events,
        )

        for retrieval in result.retrieval_plan:
            yield ExecutionBatch(
                events=[
                    EventDraft(
                        event_type="tool.call_started",
                        producer="ToolHub",
                        visibility="user",
                        payload={
                            "tool_name": retrieval.tool_name,
                            "query": " ".join(retrieval.query.split()),
                            "purpose": retrieval.purpose,
                        },
                    )
                ]
            )
            try:
                tool_result = await self._tool_hub.retrieve_authorities(retrieval)
            except ToolExecutionError as exc:
                yield ExecutionBatch(
                    events=[
                        EventDraft(
                            event_type="tool.call_failed",
                            producer="ToolHub",
                            visibility="user",
                            payload={
                                "tool_name": retrieval.tool_name,
                                "query": " ".join(retrieval.query.split()),
                                "message": str(exc),
                                "retryable": exc.retryable,
                            },
                        )
                    ]
                )
                raise
            authorities = [
                AuthorityRef(
                    tool_name=tool_result.tool_name,
                    query=tool_result.normalized_query,
                    source_id=document.source_id,
                    content_hash=document.content_hash,
                    title=document.title,
                    source_url=document.source_url,
                    excerpt=document.excerpt[:4000],
                )
                for document in tool_result.documents
            ]
            authority_patch = (
                CasePatch(
                    producer="ScenarioAgent",
                    base_version=aggregate.version,
                    operations=[AddAuthority(authority=item) for item in authorities],
                )
                if authorities
                else None
            )
            yield ExecutionBatch(
                patch=authority_patch,
                events=[
                    EventDraft(
                        event_type="tool.call_completed",
                        producer="ToolHub",
                        visibility="user",
                        payload={
                            "tool_name": tool_result.tool_name,
                            "query": tool_result.normalized_query,
                            "attempts": tool_result.attempts,
                            "authority_ids": [
                                str(item.authority_id) for item in authorities
                            ],
                            "source_count": len(authorities),
                        },
                    )
                ],
            )
