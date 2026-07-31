from __future__ import annotations

from collections.abc import AsyncIterator

from Agents.domain.case_state import CaseAggregate, FactItem, FactSource, FactStatus
from Agents.domain.commands import CaseCommand, ConfirmFactPayload
from Agents.domain.events import EventDraft
from Agents.domain.patches import CasePatch, CasePatchOperation, ResolveFactConflict, UpsertFact

from .base import ExecutionBatch


class ConfirmFactCommandHandler:
    def supports(self, command_type: str) -> bool:
        return command_type == "confirm_fact"

    async def execute(self, command: CaseCommand, aggregate: CaseAggregate) -> AsyncIterator[ExecutionBatch]:
        payload = command.payload
        if not isinstance(payload, ConfirmFactPayload):
            raise TypeError("confirm fact handler received an invalid payload")
        existing = aggregate.state.facts.items.get(payload.fact_id)
        if existing is None:
            raise ValueError(f"fact not found: {payload.fact_id}")
        confirmed = FactItem(
            fact_id=payload.fact_id,
            value=payload.value,
            status=FactStatus.CONFIRMED,
            source=FactSource(kind="user", ref_id=str(command.command_id)),
        )
        operation: CasePatchOperation
        if payload.conflict_id is not None:
            operation = ResolveFactConflict(
                conflict_id=payload.conflict_id,
                confirmed_fact=confirmed,
                resolution_note="用户通过 confirm_fact 命令确认冲突事实。",
            )
        else:
            if existing.status is FactStatus.DISPUTED:
                raise ValueError("disputed fact requires conflict_id")
            operation = UpsertFact(fact=confirmed)
        yield ExecutionBatch(
            patch=CasePatch(producer="User", base_version=aggregate.version, operations=[operation]),
            events=[EventDraft(event_type="fact.confirmed", producer="StateManager", visibility="user", payload={"fact_id": payload.fact_id, "value": payload.value, "conflict_id": str(payload.conflict_id) if payload.conflict_id else None})],
        )
