from __future__ import annotations

from collections.abc import AsyncIterator

from Agents.application.decisions import (
    AskClarificationDecision,
    ControllerDecisionProvider,
    RouteScenarioDecision,
)
from Agents.domain.case_state import (
    CaseAggregate,
    CaseStage,
    PendingHandoff,
    PendingQuestion,
)
from Agents.domain.commands import (
    CaseCommand,
    ConfirmHandoffPayload,
    SubmitUserMessagePayload,
)
from Agents.domain.events import EventDraft
from Agents.domain.patches import CasePatch, SetInteraction

from .base import ExecutionBatch


class IntakeCommandHandler:
    def __init__(self, decision_provider: ControllerDecisionProvider) -> None:
        self._decision_provider = decision_provider

    def supports(self, command_type: str) -> bool:
        return command_type in {"submit_user_message", "confirm_handoff"}

    async def execute(
        self,
        command: CaseCommand,
        aggregate: CaseAggregate,
    ) -> AsyncIterator[ExecutionBatch]:
        if isinstance(command.payload, SubmitUserMessagePayload):
            async for batch in self._submit_message(command, aggregate):
                yield batch
            return
        if isinstance(command.payload, ConfirmHandoffPayload):
            yield self._confirm_handoff(command, aggregate)
            return
        raise ValueError(f"unsupported intake command: {command.command_type}")

    async def _submit_message(
        self,
        command: CaseCommand,
        aggregate: CaseAggregate,
    ) -> AsyncIterator[ExecutionBatch]:
        payload = command.payload
        if not isinstance(payload, SubmitUserMessagePayload):
            raise TypeError("submit message handler received an invalid payload")
        next_stage = (
            CaseStage.FACT_COLLECTING
            if aggregate.state.interaction.stage is CaseStage.INTAKE
            else None
        )
        message_patch = CasePatch(
            producer="ControllerAgent",
            base_version=aggregate.version,
            operations=[
                SetInteraction(
                    stage=next_stage,
                    last_user_input=payload.text.strip(),
                    pending_questions=[],
                    blocked_on=[],
                )
            ],
        )
        yield ExecutionBatch(
            patch=message_patch,
            events=[
                EventDraft(
                    event_type="message.received",
                    producer="CaseRuntime",
                    visibility="user",
                    payload={"text": payload.text.strip(), "actor_id": command.actor_id},
                ),
                EventDraft(
                    event_type="agent.stage_started",
                    producer="ControllerAgent",
                    visibility="user",
                    payload={"stage": "controller"},
                ),
            ],
        )

        decision = await self._decision_provider.decide(
            case_state=aggregate.state,
            user_input=payload.text.strip(),
        )
        if isinstance(decision, AskClarificationDecision):
            question = PendingQuestion(
                text=decision.question,
                required_fact_ids=decision.required_fact_ids,
            )
            patch = CasePatch(
                producer="ControllerAgent",
                base_version=aggregate.version,
                operations=[
                    SetInteraction(
                        pending_questions=[question],
                        blocked_on=decision.required_fact_ids,
                    )
                ],
            )
            yield ExecutionBatch(
                patch=patch,
                events=[
                    EventDraft(
                        event_type="clarification.requested",
                        producer="ControllerAgent",
                        visibility="user",
                        payload=question.model_dump(mode="json"),
                    )
                ],
            )
            return

        if isinstance(decision, RouteScenarioDecision):
            handoff = PendingHandoff(
                from_agent="ControllerAgent",
                to_agent="ScenarioAgent",
                reason=decision.reason,
                target_stage=CaseStage.EVIDENCE_PROCESSING,
            )
            patch = CasePatch(
                producer="ControllerAgent",
                base_version=aggregate.version,
                operations=[
                    SetInteraction(
                        current_goal=decision.current_goal,
                        pending_handoff=handoff,
                        pending_questions=[],
                        blocked_on=[],
                    )
                ],
            )
            yield ExecutionBatch(
                patch=patch,
                events=[
                    EventDraft(
                        event_type="handoff.requested",
                        producer="ControllerAgent",
                        visibility="user",
                        payload={
                            **handoff.model_dump(mode="json"),
                            "scene_id": decision.scene_id,
                        },
                    )
                ],
            )
            return

        raise ValueError(f"unsupported controller decision: {decision}")

    def _confirm_handoff(
        self,
        command: CaseCommand,
        aggregate: CaseAggregate,
    ) -> ExecutionBatch:
        payload = command.payload
        if not isinstance(payload, ConfirmHandoffPayload):
            raise TypeError("confirm handoff handler received an invalid payload")
        pending = aggregate.state.interaction.pending_handoff
        if pending is None:
            raise ValueError("no pending handoff")
        if pending.handoff_id != payload.handoff_id:
            raise ValueError("handoff_id does not match pending handoff")
        if payload.approve:
            operation = SetInteraction(
                stage=pending.target_stage,
                active_agent=pending.to_agent,
                clear_pending_handoff=True,
            )
            event = EventDraft(
                event_type="handoff.confirmed",
                producer="StateManager",
                visibility="user",
                payload=pending.model_dump(mode="json"),
            )
        else:
            operation = SetInteraction(clear_pending_handoff=True)
            event = EventDraft(
                event_type="handoff.rejected",
                producer="StateManager",
                visibility="user",
                payload=pending.model_dump(mode="json"),
            )
        return ExecutionBatch(
            patch=CasePatch(
                producer="StateManager",
                base_version=aggregate.version,
                operations=[operation],
            ),
            events=[event],
        )
