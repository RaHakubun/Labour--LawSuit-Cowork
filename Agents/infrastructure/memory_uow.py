from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from uuid import UUID

from Agents.application.handlers.base import ExecutionBatch
from Agents.domain.case_state import CaseAggregate
from Agents.domain.commands import CaseCommand
from Agents.domain.commands import CancelOperationPayload
from Agents.domain.events import EventDraft, EventEnvelope
from Agents.domain.errors import PatchRejectedError, error_payload
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

    async def list_cases(self, owner_id: str) -> list[CaseAggregate]:
        aggregates = [
            data.aggregate.model_copy(deep=True)
            for data in self._cases.values()
            if data.aggregate.owner_id == owner_id
        ]
        return sorted(aggregates, key=lambda item: item.updated_at, reverse=True)

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
            if (
                not isinstance(command.payload, CancelOperationPayload)
                and command.expected_case_version != data.aggregate.version
            ):
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

    async def reject_command(
        self,
        command: CaseCommand,
        error: Exception,
    ) -> EventEnvelope:
        data = self._require(command.case_id)
        async with data.lock:
            return self._append_event(
                data,
                command,
                EventDraft(
                    event_type="command.rejected",
                    producer="CaseRuntime",
                    visibility="user",
                    payload=error_payload(error),
                ),
            )

    async def find_command(
        self,
        case_id: UUID,
        idempotency_key: str,
    ) -> AcceptCommandResult | None:
        data = self._require(case_id)
        async with data.lock:
            command = data.commands_by_key.get(idempotency_key)
            if command is None:
                return None
            events = [item for item in data.events if item.command_id == command.command_id]
            return AcceptCommandResult(command, False, events)

    async def start_command(self, command: CaseCommand) -> EventEnvelope:
        data = self._require(command.case_id)
        async with data.lock:
            status = data.command_status.get(command.command_id)
            if status == "running":
                return self._terminal_or_event(data, command.command_id, "operation.started")
            if status != "accepted":
                raise ValueError(f"command cannot start from status={status}")
            data.command_status[command.command_id] = "running"
            return self._append_event(
                data,
                command,
                EventDraft(
                    event_type="operation.started",
                    producer="CaseRuntime",
                    visibility="user",
                    payload={"status": "running"},
                ),
            )

    async def list_recoverable_commands(self) -> list[CaseCommand]:
        return await self._commands_with_status("accepted")

    async def list_interrupted_commands(self) -> list[CaseCommand]:
        return await self._commands_with_status("running")

    async def _commands_with_status(self, status: str) -> list[CaseCommand]:
        commands: list[CaseCommand] = []
        async with self._registry_lock:
            data_items = list(self._cases.values())
        for data in data_items:
            async with data.lock:
                commands.extend(
                    command
                    for command in data.commands_by_key.values()
                    if data.command_status.get(command.command_id) == status
                )
        return sorted(commands, key=lambda item: item.created_at)

    async def commit_batch(
        self,
        command: CaseCommand,
        batch: ExecutionBatch,
    ) -> list[EventEnvelope]:
        data = self._require(command.case_id)
        async with data.lock:
            committed: list[EventEnvelope] = []
            if batch.patch is not None:
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
                candidate = data.aggregate.model_copy(deep=True)
                result = self._state_manager.apply_patch(candidate, batch.patch)
                if not result.accepted:
                    committed.append(
                        self._append_event(
                            data,
                            command,
                            EventDraft(
                                event_type="patch.rejected",
                                producer="StateManager",
                                visibility="user",
                                payload={
                                    "patch_id": str(batch.patch.patch_id),
                                    "errors": result.errors,
                                },
                            ),
                        )
                    )
                    raise PatchRejectedError(result.errors, committed)
                data.aggregate = candidate
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
        cancelled: bool = False,
    ) -> EventEnvelope:
        data = self._require(command.case_id)
        async with data.lock:
            current = data.command_status.get(command.command_id)
            if current in {"completed", "failed", "cancelled"}:
                return self._terminal_or_event(
                    data,
                    command.command_id,
                    {
                        "completed": "operation.completed",
                        "failed": "operation.failed",
                        "cancelled": "operation.cancelled",
                    }[current],
                )
            status = "cancelled" if cancelled else "failed" if error else "completed"
            data.command_status[command.command_id] = status
            event_type = (
                "operation.cancelled"
                if cancelled
                else "operation.failed"
                if error
                else "operation.completed"
            )
            payload = (
                error_payload(error)
                if error
                else {"status": status}
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

    def _terminal_or_event(
        self,
        data: _CaseData,
        command_id: UUID,
        event_type: str,
    ) -> EventEnvelope:
        for event in reversed(data.events):
            if event.command_id == command_id and event.event_type == event_type:
                return event
        raise RuntimeError(
            f"command status references missing event: {command_id} {event_type}"
        )

    async def list_events(
        self,
        case_id: UUID,
        *,
        after_sequence: int = 0,
        user_visible_only: bool = False,
        limit: int | None = None,
    ) -> list[EventEnvelope]:
        data = self._require(case_id)
        async with data.lock:
            events = [
                event.model_copy(deep=True)
                for event in data.events
                if event.sequence > after_sequence
                and (not user_visible_only or event.visibility == "user")
            ]
            return events[:limit] if limit is not None else events

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
