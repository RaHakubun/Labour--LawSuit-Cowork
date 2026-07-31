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

    async def accept_command(self, command: CaseCommand) -> AcceptCommandResult: ...

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
    ) -> EventEnvelope: ...

    async def list_events(
        self,
        case_id: UUID,
        *,
        after_sequence: int = 0,
        user_visible_only: bool = False,
    ) -> list[EventEnvelope]: ...
