from __future__ import annotations

from typing import Literal, Protocol

from pydantic import Field

from Agents.domain.case_state import CaseState, StrictModel


class AskClarificationDecision(StrictModel):
    decision_type: Literal["ask_clarification"] = "ask_clarification"
    question: str = Field(min_length=1)
    required_fact_ids: list[str] = Field(min_length=1)


class RouteScenarioDecision(StrictModel):
    decision_type: Literal["route_scenario"] = "route_scenario"
    scene_id: str = Field(min_length=1)
    reason: str = Field(min_length=1)
    current_goal: str = Field(min_length=1)


ControllerDecision = AskClarificationDecision | RouteScenarioDecision


class ControllerDecisionProvider(Protocol):
    async def decide(
        self,
        *,
        case_state: CaseState,
        user_input: str,
    ) -> ControllerDecision: ...
