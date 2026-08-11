from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol
from uuid import UUID

from Agents.application.handlers.base import ExecutionBatch
from Agents.domain.case_state import CaseAggregate
from Agents.domain.commands import CaseCommand
from Agents.domain.events import EventEnvelope


@dataclass(frozen=True)
class AcceptCommandResult:
    command: CaseCommand
    is_new: bool
    events: list[EventEnvelope]


class CaseUnitOfWork(Protocol):
    async def create_case(self, aggregate: CaseAggregate) -> None: ...

    async def get_case(self, case_id: UUID) -> CaseAggregate: ...

    async def list_cases(self, owner_id: str) -> list[CaseAggregate]: ...

    async def accept_command(self, command: CaseCommand) -> AcceptCommandResult: ...

    async def reject_command(
        self,
        command: CaseCommand,
        error: Exception,
    ) -> EventEnvelope: ...

    async def find_command(
        self,
        case_id: UUID,
        idempotency_key: str,
    ) -> AcceptCommandResult | None: ...

    async def start_command(self, command: CaseCommand) -> EventEnvelope: ...

    async def list_recoverable_commands(self) -> list[CaseCommand]: ...

    async def list_interrupted_commands(self) -> list[CaseCommand]: ...

    async def commit_batch(
        self,
        command: CaseCommand,
        batch: ExecutionBatch,
    ) -> list[EventEnvelope]: ...

    async def finish_command(
        self,
        command: CaseCommand,
        *,
        error: Exception | None = None,
        cancelled: bool = False,
    ) -> EventEnvelope: ...

    async def list_events(
        self,
        case_id: UUID,
        *,
        after_sequence: int = 0,
        user_visible_only: bool = False,
        limit: int | None = None,
    ) -> list[EventEnvelope]: ...
