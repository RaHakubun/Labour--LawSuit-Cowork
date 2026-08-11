from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator, Sequence
from uuid import UUID

from Agents.application.handlers.base import CommandHandler
from Agents.domain.commands import CaseCommand
from Agents.domain.commands import CancelOperationPayload
from Agents.domain.events import EventEnvelope
from Agents.domain.errors import CaseBusyError, PatchRejectedError
from Agents.infrastructure.uow import AcceptCommandResult, CaseUnitOfWork

from .broadcast import CaseBroadcast


class CaseRuntime:
    def __init__(
        self,
        *,
        case_id,
        unit_of_work: CaseUnitOfWork,
        handlers: Sequence[CommandHandler],
        queue_capacity: int = 64,
    ) -> None:
        self.case_id = case_id
        self._unit_of_work = unit_of_work
        self._handlers = tuple(handlers)
        self._queue: asyncio.Queue[CaseCommand] = asyncio.Queue(maxsize=queue_capacity)
        self._broadcast = CaseBroadcast()
        self._closed = False
        self._active_command: CaseCommand | None = None
        self._active_execution: asyncio.Task[None] | None = None
        self._submitted_operations: set[UUID] = set()
        self._cancelled_operations: set[UUID] = set()
        self._submit_lock = asyncio.Lock()
        self._last_activity = time.monotonic()

    async def submit(self, command: CaseCommand) -> AcceptCommandResult:
        if self._closed:
            raise RuntimeError("case runtime is closed")
        if command.case_id != self.case_id:
            raise ValueError("command case_id does not match runtime")
        async with self._submit_lock:
            existing = await self._unit_of_work.find_command(
                command.case_id,
                command.idempotency_key,
            )
            if existing is not None:
                return existing
            if not isinstance(command.payload, CancelOperationPayload) and self._queue.full():
                error = CaseBusyError()
                rejected = await self._unit_of_work.reject_command(command, error)
                await self._broadcast.publish(rejected)
                raise error
            accepted = await self._unit_of_work.accept_command(command)
            self._last_activity = time.monotonic()
            for event in accepted.events:
                await self._broadcast.publish(event)
            if accepted.is_new:
                if isinstance(command.payload, CancelOperationPayload):
                    await self._request_cancellation(command)
                    return accepted
                self._submitted_operations.add(command.command_id)
                self._queue.put_nowait(command)
        return accepted

    async def restore(self, command: CaseCommand) -> None:
        if command.case_id != self.case_id:
            raise ValueError("command case_id does not match runtime")
        self._submitted_operations.add(command.command_id)
        self._last_activity = time.monotonic()
        await self._queue.put(command)

    async def run(self) -> AsyncIterator[EventEnvelope]:
        while not self._closed:
            command = await self._queue.get()
            try:
                if command.command_id in self._cancelled_operations:
                    cancelled = await self._unit_of_work.finish_command(
                        command,
                        cancelled=True,
                    )
                    await self._broadcast.publish(cancelled)
                    yield cancelled
                    continue
                self._active_command = command
                started = await self._unit_of_work.start_command(command)
                await self._broadcast.publish(started)
                yield started
                output: asyncio.Queue[EventEnvelope | None] = asyncio.Queue()
                self._active_execution = asyncio.create_task(
                    self._execute(command, output)
                )
                while True:
                    event = await output.get()
                    if event is None:
                        break
                    yield event
                await self._active_execution
                if command.command_id in self._cancelled_operations:
                    cancelled = await self._unit_of_work.finish_command(
                        command,
                        cancelled=True,
                    )
                    await self._broadcast.publish(cancelled)
                    yield cancelled
                    continue
                completed = await self._unit_of_work.finish_command(command)
                await self._broadcast.publish(completed)
                yield completed
            except asyncio.CancelledError:
                if self._closed:
                    raise
                cancelled = await self._unit_of_work.finish_command(
                    command,
                    cancelled=True,
                )
                await self._broadcast.publish(cancelled)
                yield cancelled
            except Exception as exc:
                failed = await self._unit_of_work.finish_command(command, error=exc)
                await self._broadcast.publish(failed)
                yield failed
            finally:
                self._active_command = None
                self._active_execution = None
                self._submitted_operations.discard(command.command_id)
                self._cancelled_operations.discard(command.command_id)
                self._queue.task_done()
                self._last_activity = time.monotonic()

    async def _execute(
        self,
        command: CaseCommand,
        output: asyncio.Queue[EventEnvelope | None],
    ) -> None:
        aggregate = await self._unit_of_work.get_case(command.case_id)
        handler = self._resolve_handler(command.command_type)
        try:
            async for batch in handler.execute(command, aggregate):
                try:
                    events = await self._unit_of_work.commit_batch(command, batch)
                except PatchRejectedError as exc:
                    for event in exc.events:
                        await self._broadcast.publish(event)
                        await output.put(event)
                    raise
                refreshed = await self._unit_of_work.get_case(command.case_id)
                aggregate.state = refreshed.state
                aggregate.version = refreshed.version
                aggregate.updated_at = refreshed.updated_at
                for event in events:
                    await self._broadcast.publish(event)
                    await output.put(event)
        finally:
            await output.put(None)

    async def _request_cancellation(self, command: CaseCommand) -> None:
        payload = command.payload
        if not isinstance(payload, CancelOperationPayload):
            raise TypeError("cancellation requires CancelOperationPayload")
        if payload.operation_id not in self._submitted_operations:
            failed = await self._unit_of_work.finish_command(
                command,
                error=ValueError(f"operation is not active or queued: {payload.operation_id}"),
            )
            await self._broadcast.publish(failed)
            return
        self._cancelled_operations.add(payload.operation_id)
        if (
            self._active_command is not None
            and self._active_command.command_id == payload.operation_id
            and self._active_execution is not None
        ):
            self._active_execution.cancel()
        completed = await self._unit_of_work.finish_command(command)
        await self._broadcast.publish(completed)

    async def stream(
        self,
        *,
        after_sequence: int = 0,
    ) -> AsyncIterator[EventEnvelope]:
        subscriber_id, queue = await self._broadcast.subscribe()
        last_sequence = after_sequence
        try:
            history = await self._unit_of_work.list_events(
                self.case_id,
                after_sequence=after_sequence,
                user_visible_only=True,
            )
            for event in history:
                last_sequence = max(last_sequence, event.sequence)
                yield event
            while not self._closed:
                queued_event = await queue.get()
                if queued_event is None:
                    raise RuntimeError(
                        "event subscriber overflow; reconnect with Last-Event-ID"
                    )
                if (
                    queued_event.visibility == "user"
                    and queued_event.sequence > last_sequence
                ):
                    last_sequence = queued_event.sequence
                    yield queued_event
        finally:
            await self._broadcast.unsubscribe(subscriber_id)

    async def wait_idle(self) -> None:
        await self._queue.join()

    def close(self) -> None:
        self._closed = True

    def is_idle_for(self, seconds: float) -> bool:
        return (
            self._active_command is None
            and self._queue.empty()
            and time.monotonic() - self._last_activity >= seconds
        )

    def _resolve_handler(self, command_type: str) -> CommandHandler:
        matches = [handler for handler in self._handlers if handler.supports(command_type)]
        if len(matches) != 1:
            raise RuntimeError(
                f"expected exactly one handler for {command_type}, got {len(matches)}"
            )
        return matches[0]
