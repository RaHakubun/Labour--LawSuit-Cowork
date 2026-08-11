from __future__ import annotations

from dataclasses import dataclass

from Agents.application.scenario_models import RuleCalculationRequest
from Agents.domain.case_state import CaseAggregate, FactStatus
from Agents.domain.commands import CalculateRulePayload


@dataclass(frozen=True)
class RuleExecutionPlan:
    payload: CalculateRulePayload | None
    missing_fact_ids: tuple[str, ...]
    unconfirmed_fact_ids: tuple[str, ...]


class ScenarioRulePlanner:
    def plan(
        self,
        request: RuleCalculationRequest,
        aggregate: CaseAggregate,
    ) -> RuleExecutionPlan:
        fact_ids = list(dict.fromkeys(request.input_fact_map.values()))
        missing = tuple(
            fact_id
            for fact_id in fact_ids
            if fact_id not in aggregate.state.facts.items
        )
        unconfirmed = tuple(
            fact_id
            for fact_id in fact_ids
            if fact_id in aggregate.state.facts.items
            and aggregate.state.facts.items[fact_id].status is not FactStatus.CONFIRMED
        )
        if missing or unconfirmed:
            return RuleExecutionPlan(
                payload=None,
                missing_fact_ids=missing,
                unconfirmed_fact_ids=unconfirmed,
            )
        return RuleExecutionPlan(
            payload=CalculateRulePayload(
                calc_type=request.calc_type,
                inputs={
                    input_name: aggregate.state.facts.items[fact_id].value
                    for input_name, fact_id in request.input_fact_map.items()
                },
                fact_ids=fact_ids,
            ),
            missing_fact_ids=(),
            unconfirmed_fact_ids=(),
        )
