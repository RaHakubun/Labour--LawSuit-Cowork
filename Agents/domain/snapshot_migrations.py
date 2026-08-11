from __future__ import annotations

from copy import deepcopy
from typing import Any

from .case_state import CaseAggregate, SCHEMA_VERSION


def load_case_aggregate(snapshot: dict[str, Any]) -> CaseAggregate:
    version = str(snapshot.get("schema_version", ""))
    if version == SCHEMA_VERSION:
        return CaseAggregate.model_validate(snapshot)
    if version != "1.0":
        raise ValueError(f"unsupported case snapshot schema_version: {version or 'missing'}")
    return CaseAggregate.model_validate(_migrate_v1_to_v2(snapshot))


def _migrate_v1_to_v2(snapshot: dict[str, Any]) -> dict[str, Any]:
    migrated = deepcopy(snapshot)
    _replace_schema_versions(migrated)
    interaction = migrated.setdefault("state", {}).setdefault("interaction", {})
    interaction.setdefault("pending_confirmation", None)
    evidence = migrated["state"].setdefault("evidence", {})
    evidence.setdefault("extractions", {})
    evidence.setdefault("fact_links", {})
    analysis = migrated["state"].setdefault("analysis", {})
    analysis.setdefault("notes", {})
    analysis["missing_information"] = [
        {
            "schema_version": SCHEMA_VERSION,
            "description": str(item),
            "required_fact_ids": [],
            "blocking": True,
        }
        for item in analysis.get("missing_information", [])
    ]
    return migrated


def _replace_schema_versions(value: Any) -> None:
    if isinstance(value, dict):
        if "schema_version" in value:
            value["schema_version"] = SCHEMA_VERSION
        for item in value.values():
            _replace_schema_versions(item)
    elif isinstance(value, list):
        for item in value:
            _replace_schema_versions(item)
