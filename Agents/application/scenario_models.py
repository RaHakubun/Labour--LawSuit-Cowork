from __future__ import annotations

from typing import Literal, Protocol

from pydantic import Field, model_validator

from Agents.domain.case_state import CaseState, StrictModel
from Agents.scene_catalog import SceneId


class CandidateFact(StrictModel):
    fact_id: str = Field(min_length=1)
    value: object
    confidence: float = Field(ge=0, le=1)
    derivation: str = Field(min_length=1)


class EvidenceRequirement(StrictModel):
    evidence_type: str = Field(min_length=1)
    purpose: str = Field(min_length=1)
    required: bool = True


class AuthorityRetrievalRequest(StrictModel):
    tool_name: Literal[
        "法条识别与溯源",
        "检索司法案例-语义",
        "检索司法案例-关键词",
        "检索法律法规-语义",
    ]
    query: str = Field(min_length=1)
    purpose: str = Field(min_length=1)
    required: bool = True


class RuleCalculationRequest(StrictModel):
    calc_type: Literal[
        "wage_base",
        "overtime",
        "severance",
        "medical_period",
        "annual_leave_unused",
        "double_wage_unsigned_contract",
    ]
    input_fact_map: dict[str, str] = Field(min_length=1)


class ScenarioResult(StrictModel):
    scene_id: SceneId
    confidence: float = Field(ge=0, le=1)
    candidate_facts: list[CandidateFact] = Field(default_factory=list)
    missing_fact_questions: list[str] = Field(default_factory=list)
    evidence_requirements: list[EvidenceRequirement] = Field(default_factory=list)
    retrieval_plan: list[AuthorityRetrievalRequest] = Field(default_factory=list)
    rule_calculation_requests: list[RuleCalculationRequest] = Field(default_factory=list)
    summary: str = Field(min_length=1)

    @model_validator(mode="after")
    def has_actionable_next_step(self) -> "ScenarioResult":
        if not any(
            (
                self.candidate_facts,
                self.missing_fact_questions,
                self.evidence_requirements,
                self.retrieval_plan,
                self.rule_calculation_requests,
            )
        ):
            raise ValueError("ScenarioResult requires at least one actionable result")
        if len(self.missing_fact_questions) > 3:
            raise ValueError("ScenarioResult may ask at most three questions")
        return self


class ScenarioResultProvider(Protocol):
    async def analyze(
        self,
        *,
        role_id: str,
        scene_id: str,
        case_state: CaseState,
    ) -> ScenarioResult: ...
