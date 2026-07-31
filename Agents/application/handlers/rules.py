from __future__ import annotations

from collections.abc import AsyncIterator

from Agents.domain.case_state import CaseAggregate, FactStatus, RuleResult
from Agents.domain.commands import CalculateRulePayload, CaseCommand
from Agents.domain.events import EventDraft
from Agents.domain.patches import AddRuleResult, CasePatch
from utils.labour_calculator import LabourCalculatorEngine

from .base import ExecutionBatch


class RuleCalculationCommandHandler:
    def __init__(self, engine: LabourCalculatorEngine | None = None) -> None:
        self._engine = engine or LabourCalculatorEngine()

    def supports(self, command_type: str) -> bool:
        return command_type == "calculate_rule"

    async def execute(self, command: CaseCommand, aggregate: CaseAggregate) -> AsyncIterator[ExecutionBatch]:
        payload = command.payload
        if not isinstance(payload, CalculateRulePayload):
            raise TypeError("rule handler received an invalid payload")
        missing = [fact_id for fact_id in payload.fact_ids if fact_id not in aggregate.state.facts.items]
        if missing:
            raise ValueError(f"rule input facts not found: {', '.join(missing)}")
        unconfirmed = [
            fact_id
            for fact_id in payload.fact_ids
            if aggregate.state.facts.items[fact_id].status is not FactStatus.CONFIRMED
        ]
        if unconfirmed:
            raise ValueError(
                f"rule input facts require user confirmation: {', '.join(unconfirmed)}"
            )
        calculation = self._engine.calculate(payload.calc_type, dict(payload.inputs))
        if not calculation.get("ok"):
            raise ValueError("; ".join(calculation.get("errors", ["rule calculation failed"])))
        rule_result = RuleResult(
            rule=payload.calc_type,
            input_case_version=aggregate.version,
            result={
                "result": calculation.get("result", {}),
                "breakdown": calculation.get("breakdown", {}),
                "inputs": calculation.get("inputs", {}),
                "rules_applied": calculation.get("rules_applied", []),
            },
            unit="CNY" if payload.calc_type in {"wage_base", "overtime", "severance", "annual_leave_unused", "double_wage_unsigned_contract"} else "",
            rounding="ROUND_HALF_UP to 0.01 for monetary values",
            fact_ids=payload.fact_ids,
        )
        yield ExecutionBatch(
            patch=CasePatch(producer="RuleCalculator", base_version=aggregate.version, operations=[AddRuleResult(rule_result=rule_result)]),
            events=[EventDraft(event_type="analysis.updated", producer="RuleCalculator", visibility="user", payload={"kind": "rule_result", "result_id": str(rule_result.result_id), "rule": rule_result.rule, "result": rule_result.result})],
        )
