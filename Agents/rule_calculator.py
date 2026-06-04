from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .case_state import CaseWorkspace
from .state_manager import CasePatch, PatchOperation
from utils.labour_calculator import LabourCalculatorEngine


@dataclass
class RuleCalculatorAdapter:
    engine: LabourCalculatorEngine = field(default_factory=LabourCalculatorEngine)

    def build_patch(
        self,
        workspace: CaseWorkspace,
        *,
        calc_type: str,
        payload: dict[str, Any],
    ) -> CasePatch:
        result = self.engine.calculate(calc_type, payload or {})
        operations: list[PatchOperation] = []
        if result.get("ok"):
            operations.append(
                PatchOperation(
                    op="append",
                    path="analysis.calculations",
                    value={
                        "calc_type": calc_type,
                        "ok": True,
                        "result": dict(result.get("result", {}) or {}),
                        "breakdown": dict(result.get("breakdown", {}) or {}),
                        "inputs": dict(result.get("inputs", {}) or {}),
                        "rules_applied": list(result.get("rules_applied", []) or []),
                    },
                )
            )
        else:
            operations.append(
                PatchOperation(
                    op="append",
                    path="analysis.missing_information",
                    value={
                        "field": f"{calc_type}.inputs",
                        "reason": "calculation_failed",
                        "errors": list(result.get("errors", []) or []),
                        "source": {"agent": "RuleCalculator"},
                    },
                )
            )
        return CasePatch.new(
            agent_name="RuleCalculator",
            base_version=workspace.version,
            operations=operations,
            metadata={"calc_type": calc_type},
        )

