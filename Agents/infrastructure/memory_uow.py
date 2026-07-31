from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from uuid import UUID

from Agents.application.handlers.base import ExecutionBatch
from Agents.domain.case_state import CaseAggregate
from Agents.domain.commands import CaseCommand
from Agents.domain.events import EventDraft, EventEnvelope
from Agents.domain.state_manager import DomainStateManager
from Agents.infrastructure.uow import AcceptCommandResult


@dataclass
class _CaseData:
    aggregate: CaseAggregate
    events: list[EventEnvelope] = field(default_factory=list)
    commands_by_key: dict[str, CaseCommand] = field(default_factory=dict)
    command_status: dict[UUID, str] = field(default_factory=dict)
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)


class InMemoryCaseUnitOfWork:
    """Atomic development/test adapter with the same boundaries as Postgres UoW."""

    def __init__(self, state_manager: DomainStateManager | None = None) -> None:
        self._cases: dict[UUID, _CaseData] = {}
        self._state_manager = state_manager or DomainStateManager()
        self._registry_lock = asyncio.Lock()

    async def create_case(self, aggregate: CaseAggregate) -> None:
        async with self._registry_lock:
            if aggregate.case_id in self._cases:
                raise ValueError(f"case already exists: {aggregate.case_id}")
            self._cases[aggregate.case_id] = _CaseData(aggregate=aggregate)

    async def get_case(self, case_id: UUID) -> CaseAggregate:
        data = self._require(case_id)
        async with data.lock:
            return data.aggregate.model_copy(deep=True)

    async def accept_command(self, command: CaseCommand) -> AcceptCommandResult:
        data = self._require(command.case_id)
        async with data.lock:
            if data.aggregate.owner_id != command.actor_id:
                raise PermissionError("actor does not own case")
            existing = data.commands_by_key.get(command.idempotency_key)
            if existing is not None:
                existing_events = [
                    item for item in data.events if item.command_id == existing.command_id
                ]
                return AcceptCommandResult(existing, False, existing_events)
            if command.expected_case_version != data.aggregate.version:
                raise ValueError(
                    f"expected_case_version={command.expected_case_version} does not "
                    f"match case version={data.aggregate.version}"
                )
            data.commands_by_key[command.idempotency_key] = command
            data.command_status[command.command_id] = "accepted"
            event = self._append_event(
                data,
                command,
                EventDraft(
                    event_type="command.accepted",
                    producer="CaseCommandService",
                    visibility="user",
                    payload={"command_type": command.command_type},
                ),
            )
            return AcceptCommandResult(command, True, [event])

    async def commit_batch(
        self,
        command: CaseCommand,
        batch: ExecutionBatch,
    ) -> list[EventEnvelope]:
        data = self._require(command.case_id)
        async with data.lock:
            committed: list[EventEnvelope] = []
            if batch.patch is not None:
                candidate = data.aggregate.model_copy(deep=True)
                result = self._state_manager.apply_patch(candidate, batch.patch)
                if not result.accepted:
                    raise ValueError("; ".join(result.errors))
                data.aggregate = candidate
                committed.append(
                    self._append_event(
                        data,
                        command,
                        EventDraft(
                            event_type="patch.submitted",
                            producer=batch.patch.producer,
                            visibility="internal",
                            payload={"patch_id": str(batch.patch.patch_id)},
                        ),
                    )
                )
                committed.append(
                    self._append_event(
                        data,
                        command,
                        EventDraft(
                            event_type="patch.applied",
                            producer="StateManager",
                            visibility="internal",
                            payload={
                                "patch_id": str(batch.patch.patch_id),
                                "new_version": data.aggregate.version,
                            },
                        ),
                    )
                )
            committed.extend(
                self._append_event(data, command, draft) for draft in batch.events
            )
            return committed

    async def finish_command(
        self,
        command: CaseCommand,
        *,
        error: Exception | None = None,
    ) -> EventEnvelope:
        data = self._require(command.case_id)
        async with data.lock:
            status = "failed" if error else "completed"
            data.command_status[command.command_id] = status
            event_type = "operation.failed" if error else "operation.completed"
            payload = (
                {
                    "code": type(error).__name__,
                    "message": str(error),
                    "retryable": False,
                }
                if error
                else {"status": "completed"}
            )
            return self._append_event(
                data,
                command,
                EventDraft(
                    event_type=event_type,
                    producer="CaseRuntime",
                    visibility="user",
                    payload=payload,
                ),
            )

    async def list_events(
        self,
        case_id: UUID,
        *,
        after_sequence: int = 0,
        user_visible_only: bool = False,
    ) -> list[EventEnvelope]:
        data = self._require(case_id)
        async with data.lock:
            return [
                event.model_copy(deep=True)
                for event in data.events
                if event.sequence > after_sequence
                and (not user_visible_only or event.visibility == "user")
            ]

    def _append_event(
        self,
        data: _CaseData,
        command: CaseCommand,
        draft: EventDraft,
    ) -> EventEnvelope:
        envelope = EventEnvelope(
            sequence=len(data.events) + 1,
            case_id=command.case_id,
            command_id=command.command_id,
            correlation_id=command.correlation_id,
            causation_id=draft.causation_id,
            case_version=data.aggregate.version,
            event_type=draft.event_type,
            producer=draft.producer,
            visibility=draft.visibility,
            payload=draft.payload,
        )
        data.events.append(envelope)
        return envelope

    def _require(self, case_id: UUID) -> _CaseData:
        try:
            return self._cases[case_id]
        except KeyError as exc:
            raise KeyError(f"case not found: {case_id}") from exc
