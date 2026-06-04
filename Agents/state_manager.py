from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4

from .case_state import FACT_STATUSES, CaseWorkspace, FactItem
from .conversation_contract import now_utc_iso


@dataclass
class PatchOperation:
    op: str
    path: str
    value: Any

    def to_dict(self) -> dict[str, Any]:
        return {"op": self.op, "path": self.path, "value": self.value}


@dataclass
class CasePatch:
    patch_id: str
    agent_name: str
    base_version: int
    operations: list[PatchOperation] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def new(
        cls,
        *,
        agent_name: str,
        base_version: int,
        operations: list[PatchOperation],
        metadata: dict[str, Any] | None = None,
    ) -> "CasePatch":
        return cls(
            patch_id=uuid4().hex,
            agent_name=agent_name,
            base_version=base_version,
            operations=list(operations),
            metadata=dict(metadata or {}),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "patch_id": self.patch_id,
            "agent_name": self.agent_name,
            "base_version": int(self.base_version),
            "operations": [op.to_dict() for op in self.operations],
            "metadata": dict(self.metadata),
        }


@dataclass
class PatchResult:
    patch_id: str
    accepted: bool
    base_version: int
    new_version: int
    applied_operations: int = 0
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "patch_id": self.patch_id,
            "accepted": bool(self.accepted),
            "base_version": int(self.base_version),
            "new_version": int(self.new_version),
            "applied_operations": int(self.applied_operations),
            "errors": list(self.errors),
            "warnings": list(self.warnings),
        }


class StateManager:
    """The single write gate for CaseState mutations."""

    _LAYER_PERMISSIONS: dict[str, set[str]] = {
        "ControllerAgent": {"interaction", "analysis"},
        "ScenarioAgent": {"facts", "analysis"},
        "EvidenceParser": {"facts", "evidence"},
        "RuleCalculator": {"analysis"},
        "LegalAnalysisAgent": {"analysis", "outputs"},
        "EmployerModuleAgent": {"analysis", "outputs"},
    }

    def apply_patch(self, workspace: CaseWorkspace, patch: CasePatch) -> PatchResult:
        errors = self._validate_patch(workspace, patch)
        if errors:
            result = PatchResult(
                patch_id=patch.patch_id,
                accepted=False,
                base_version=patch.base_version,
                new_version=workspace.version,
                errors=errors,
            )
            self._record_result(workspace, result)
            return result

        applied = 0
        warnings: list[str] = []
        for operation in patch.operations:
            warning = self._apply_operation(workspace, patch.agent_name, operation)
            if warning:
                warnings.append(warning)
            applied += 1

        workspace.version += 1
        result = PatchResult(
            patch_id=patch.patch_id,
            accepted=True,
            base_version=patch.base_version,
            new_version=workspace.version,
            applied_operations=applied,
            warnings=warnings,
        )
        self._record_result(workspace, result)
        return result

    def _record_result(self, workspace: CaseWorkspace, result: PatchResult) -> None:
        workspace.last_patch_results.append(result.to_dict())
        if len(workspace.last_patch_results) > 20:
            workspace.last_patch_results = workspace.last_patch_results[-20:]

    def _validate_patch(self, workspace: CaseWorkspace, patch: CasePatch) -> list[str]:
        errors: list[str] = []
        if patch.base_version != workspace.version:
            errors.append(
                f"patch base_version={patch.base_version} does not match case version={workspace.version}"
            )
        if not patch.agent_name.strip():
            errors.append("patch.agent_name must be non-empty")
        if not patch.operations:
            errors.append("patch.operations must be non-empty")
        for operation in patch.operations:
            errors.extend(self._validate_operation(patch.agent_name, operation))
        return errors

    def _validate_operation(self, agent_name: str, operation: PatchOperation) -> list[str]:
        errors: list[str] = []
        layer = operation.path.split(".", 1)[0].strip()
        if layer not in {"interaction", "facts", "evidence", "analysis", "outputs"}:
            errors.append(f"unsupported patch layer: {layer}")
            return errors
        allowed_layers = self._LAYER_PERMISSIONS.get(agent_name, set())
        if layer not in allowed_layers:
            errors.append(f"{agent_name} cannot write case_state.{layer}")
        if operation.op not in {
            "set",
            "append",
            "upsert_fact",
            "upsert_evidence",
            "add_output",
        }:
            errors.append(f"unsupported patch operation: {operation.op}")
        if operation.op == "upsert_fact":
            fact = dict(operation.value) if isinstance(operation.value, dict) else {}
            status = str(fact.get("status", "pending_verification"))
            if status not in FACT_STATUSES:
                errors.append(f"invalid fact status: {status}")
            if agent_name != "StateManager" and status == "confirmed":
                errors.append(f"{agent_name} cannot write confirmed facts")
            fact_id = self._fact_id_from_path(operation.path)
            if fact_id.startswith("legal.") or fact_id in {"legal_conclusion", "legal.conclusion"}:
                errors.append(f"{agent_name} cannot write legal conclusions to fact layer")
        return errors

    def _apply_operation(
        self,
        workspace: CaseWorkspace,
        agent_name: str,
        operation: PatchOperation,
    ) -> str:
        if operation.op == "set":
            self._set_path(workspace.case_state.to_dict(), operation.path, operation.value)
            self._set_case_state_path(workspace, operation.path, operation.value)
            return ""
        if operation.op == "append":
            self._append_case_state_path(workspace, operation.path, operation.value)
            return ""
        if operation.op == "upsert_fact":
            return self._upsert_fact(workspace, agent_name, operation.path, operation.value)
        if operation.op == "upsert_evidence":
            evidence_id = self._path_tail(operation.path)
            item = dict(operation.value) if isinstance(operation.value, dict) else {}
            item.setdefault("evidence_id", evidence_id)
            workspace.case_state.evidence.setdefault("items", {})[evidence_id] = item
            return ""
        if operation.op == "add_output":
            workspace.case_state.outputs.setdefault("artifacts", []).append(dict(operation.value))
            return ""
        raise ValueError(f"unsupported patch operation: {operation.op}")

    def _upsert_fact(
        self,
        workspace: CaseWorkspace,
        agent_name: str,
        path: str,
        value: Any,
    ) -> str:
        fact_id = self._fact_id_from_path(path)
        data = dict(value) if isinstance(value, dict) else {"value": value}
        data.setdefault("fact_id", fact_id)
        data.setdefault("status", "pending_verification")
        data.setdefault("source", {"agent": agent_name})
        data.setdefault("confidence", "")
        data["updated_at"] = now_utc_iso()
        new_fact = FactItem.from_dict(data).to_dict()

        facts = workspace.case_state.facts.setdefault("items", {})
        existing = facts.get(fact_id)
        if isinstance(existing, dict) and existing.get("value") != new_fact["value"]:
            facts[fact_id] = {
                **existing,
                "status": "disputed",
                "updated_at": now_utc_iso(),
            }
            workspace.case_state.facts.setdefault("disputed_facts", []).append(
                {
                    "fact_id": fact_id,
                    "existing": existing,
                    "incoming": new_fact,
                    "detected_at": now_utc_iso(),
                }
            )
            workspace.case_state.analysis.setdefault("missing_information", []).append(
                {
                    "field": fact_id,
                    "reason": "conflicting_fact_values",
                    "question": f"请确认 {fact_id} 的准确内容。",
                    "source": {"agent": "StateManager"},
                }
            )
            return f"fact conflict detected for {fact_id}"

        facts[fact_id] = new_fact
        return ""

    def _set_case_state_path(self, workspace: CaseWorkspace, path: str, value: Any) -> None:
        root, tail = path.split(".", 1)
        target = getattr(workspace.case_state, root)
        parts = tail.split(".")
        for part in parts[:-1]:
            target = target.setdefault(part, {})
        target[parts[-1]] = value

    def _append_case_state_path(self, workspace: CaseWorkspace, path: str, value: Any) -> None:
        root, tail = path.split(".", 1)
        target = getattr(workspace.case_state, root)
        parts = tail.split(".")
        for part in parts[:-1]:
            target = target.setdefault(part, {})
        bucket = target.setdefault(parts[-1], [])
        if not isinstance(bucket, list):
            raise ValueError(f"patch path is not a list: {path}")
        bucket.append(value)

    def _set_path(self, _state: dict[str, Any], _path: str, _value: Any) -> None:
        # Kept as a tiny seam for tests and future schema-driven validation.
        return None

    def _path_tail(self, path: str) -> str:
        return path.rsplit(".", 1)[-1].strip()

    def _fact_id_from_path(self, path: str) -> str:
        prefix = "facts.items."
        if path.startswith(prefix):
            return path[len(prefix) :].strip()
        return self._path_tail(path)

