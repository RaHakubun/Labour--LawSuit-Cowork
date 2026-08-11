from __future__ import annotations

from typing import Annotated, Literal, Protocol

from pydantic import Field

from Agents.domain.case_state import CaseState, StrictModel
from Agents.scene_catalog import SceneId


class AskClarificationDecision(StrictModel):
    decision_type: Literal["ask_clarification"]
    question: str = Field(min_length=1)
    required_fact_ids: list[str] = Field(min_length=1)


class RouteScenarioDecision(StrictModel):
    decision_type: Literal["route_scenario"]
    scene_id: SceneId
    reason: str = Field(min_length=1)
    current_goal: str = Field(min_length=1)


class RequestFactConfirmationDecision(StrictModel):
    decision_type: Literal["request_fact_confirmation"]
    fact_ids: list[str] = Field(min_length=1)
    reason: str = Field(min_length=1)


class RequestAnalysisDecision(StrictModel):
    decision_type: Literal["request_analysis"]
    reason: str = Field(min_length=1)


class RequestDocumentDecision(StrictModel):
    decision_type: Literal["request_document"]
    document_type: Literal["legal_analysis_report", "labour_arbitration_application"]
    reason: str = Field(min_length=1)


class ContinueCurrentStageDecision(StrictModel):
    decision_type: Literal["continue_current_stage"]
    continuation_type: Literal[
        "active_scenario",
        "register_evidence",
        "calculate_rule",
        "cancel_operation",
    ]
    reason: str = Field(min_length=1)


ControllerDecision = Annotated[
    AskClarificationDecision
    | RouteScenarioDecision
    | RequestFactConfirmationDecision
    | RequestAnalysisDecision
    | RequestDocumentDecision
    | ContinueCurrentStageDecision,
    Field(discriminator="decision_type"),
]


class ControllerDecisionProvider(Protocol):
    async def decide(
        self,
        *,
        case_state: CaseState,
        user_input: str,
    ) -> ControllerDecision: ...
