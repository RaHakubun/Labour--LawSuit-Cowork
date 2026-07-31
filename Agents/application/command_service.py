from __future__ import annotations

from uuid import UUID

from Agents.domain.case_state import CaseAggregate
from Agents.domain.commands import CaseCommand, CommandPayload
from Agents.infrastructure.uow import AcceptCommandResult, CaseUnitOfWork
from Agents.runtime.registry import CaseRuntimeRegistry


class CaseCommandService:
    def __init__(
        self,
        *,
        unit_of_work: CaseUnitOfWork,
        runtime_registry: CaseRuntimeRegistry,
    ) -> None:
        self._unit_of_work = unit_of_work
        self._runtime_registry = runtime_registry

    async def create_case(self, *, owner_id: str, role_id: str) -> CaseAggregate:
        aggregate = CaseAggregate.create(owner_id=owner_id, role_id=role_id)
        await self._unit_of_work.create_case(aggregate)
        return aggregate

    async def submit(
        self,
        *,
        case_id: UUID,
        actor_id: str,
        idempotency_key: str,
        expected_case_version: int,
        payload: CommandPayload,
    ) -> AcceptCommandResult:
        command = CaseCommand(
            case_id=case_id,
            actor_id=actor_id,
            idempotency_key=idempotency_key,
            expected_case_version=expected_case_version,
            payload=payload,
        )
        runtime = await self._runtime_registry.get_or_create(case_id)
        return await runtime.submit(command)
