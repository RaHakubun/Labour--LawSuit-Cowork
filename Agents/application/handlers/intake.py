from __future__ import annotations

from collections.abc import AsyncIterator

from Agents.application.decisions import (
    AskClarificationDecision,
    ContinueCurrentStageDecision,
    ControllerDecisionProvider,
    RequestAnalysisDecision,
    RequestDocumentDecision,
    RequestFactConfirmationDecision,
    RouteScenarioDecision,
)
from Agents.application.handlers.legal import LegalCommandHandler
from Agents.application.handlers.scenario import ScenarioStageHandler
from Agents.domain.case_state import (
    CaseAggregate,
    CaseStage,
    PendingQuestion,
    PendingConfirmation,
)
from Agents.domain.commands import (
    CaseCommand,
    SubmitUserMessagePayload,
)
from Agents.domain.events import EventDraft
from Agents.domain.patches import CasePatch, SetInteraction
from Agents.scene_catalog import validate_scene_id

from .base import ExecutionBatch


class ControllerCommandHandler:
    def __init__(
        self,
        decision_provider: ControllerDecisionProvider,
        scenario_handler: ScenarioStageHandler,
        *,
        legal_handler: LegalCommandHandler | None = None,
    ) -> None:
        self._decision_provider = decision_provider
        self._scenario_handler = scenario_handler
        self._legal_handler = legal_handler

    def supports(self, command_type: str) -> bool:
        return command_type == "submit_user_message"

    async def execute(
        self,
        command: CaseCommand,
        aggregate: CaseAggregate,
    ) -> AsyncIterator[ExecutionBatch]:
        if isinstance(command.payload, SubmitUserMessagePayload):
            async for batch in self._submit_message(command, aggregate):
                yield batch
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
            scene_id = validate_scene_id(decision.scene_id)
            patch = CasePatch(
                producer="ControllerAgent",
                base_version=aggregate.version,
                operations=[
                    SetInteraction(
                        stage=CaseStage.EVIDENCE_PROCESSING,
                        active_agent="ScenarioAgent",
                        active_scene_id=scene_id,
                        current_goal=decision.current_goal,
                        pending_questions=[],
                        blocked_on=[],
                    )
                ],
            )
            yield ExecutionBatch(
                patch=patch,
                events=[
                    EventDraft(
                        event_type="state.transitioned",
                        producer="ControllerAgent",
                        visibility="internal",
                        payload={
                            "from_stage": aggregate.state.interaction.stage.value,
                            "to_stage": CaseStage.EVIDENCE_PROCESSING.value,
                            "reason": decision.reason,
                            "scene_id": scene_id,
                        },
                    ),
                    EventDraft(
                        event_type="agent.stage_started",
                        producer="ScenarioAgent",
                        visibility="user",
                        payload={"stage": "scenario", "scene_id": scene_id},
                    ),
                ],
            )
            async for batch in self._scenario_handler.execute_stage(
                command,
                aggregate,
                scene_id=scene_id,
            ):
                yield batch
            return

        if isinstance(decision, RequestFactConfirmationDecision):
            confirmation = PendingConfirmation(
                confirmation_type="fact",
                prompt=decision.reason,
                target_ids=decision.fact_ids,
            )
            yield ExecutionBatch(
                patch=CasePatch(
                    producer="ControllerAgent",
                    base_version=aggregate.version,
                    operations=[
                        SetInteraction(
                            pending_confirmation=confirmation,
                            blocked_on=decision.fact_ids,
                        )
                    ],
                ),
                events=[
                    EventDraft(
                        event_type="fact.confirmation_requested",
                        producer="ControllerAgent",
                        visibility="user",
                        payload=confirmation.model_dump(mode="json"),
                    )
                ],
            )
            return

        if isinstance(decision, RequestAnalysisDecision):
            if self._legal_handler is None:
                raise RuntimeError("legal handler is required for request_analysis")
            async for batch in self._legal_handler.execute_analysis(command, aggregate):
                yield batch
            return

        if isinstance(decision, RequestDocumentDecision):
            if self._legal_handler is None:
                raise RuntimeError("legal handler is required for request_document")
            async for batch in self._legal_handler.execute_document(
                command,
                aggregate,
                decision.document_type,
            ):
                yield batch
            return

        if isinstance(decision, ContinueCurrentStageDecision):
            if decision.continuation_type != "active_scenario":
                raise ValueError(
                    "Controller provider may only continue an active scenario; "
                    f"typed capability required for {decision.continuation_type}"
                )
            active_scene_id = aggregate.state.interaction.active_scene_id
            if not active_scene_id:
                raise ValueError("cannot continue current stage without an active scene")
            scene_id = validate_scene_id(active_scene_id)
            async for batch in self._scenario_handler.execute_stage(
                command,
                aggregate,
                scene_id=scene_id,
            ):
                yield batch
            return

        raise ValueError(f"unsupported controller decision: {decision}")
