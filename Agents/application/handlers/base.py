from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Protocol

from Agents.domain.case_state import CaseAggregate
from Agents.domain.commands import CaseCommand
from Agents.domain.events import EventDraft
from Agents.domain.patches import CasePatch


@dataclass(frozen=True)
class ExecutionBatch:
    events: list[EventDraft] = field(default_factory=list)
    patch: CasePatch | None = None


class CommandHandler(Protocol):
    def supports(self, command_type: str) -> bool: ...

    def execute(
        self,
        command: CaseCommand,
        aggregate: CaseAggregate,
    ) -> AsyncIterator[ExecutionBatch]: ...
