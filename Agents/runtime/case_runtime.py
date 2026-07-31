from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Sequence

from Agents.application.handlers.base import CommandHandler
from Agents.domain.commands import CaseCommand
from Agents.domain.events import EventEnvelope
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

    async def submit(self, command: CaseCommand) -> AcceptCommandResult:
        if self._closed:
            raise RuntimeError("case runtime is closed")
        if command.case_id != self.case_id:
            raise ValueError("command case_id does not match runtime")
        accepted = await self._unit_of_work.accept_command(command)
        for event in accepted.events:
            await self._broadcast.publish(event)
        if accepted.is_new:
            try:
                self._queue.put_nowait(command)
            except asyncio.QueueFull as exc:
                failure = await self._unit_of_work.finish_command(
                    command,
                    error=RuntimeError("case command queue is full"),
                )
                await self._broadcast.publish(failure)
                raise RuntimeError("case_busy") from exc
        return accepted

    async def run(self) -> AsyncIterator[EventEnvelope]:
        while not self._closed:
            command = await self._queue.get()
            try:
                aggregate = await self._unit_of_work.get_case(command.case_id)
                handler = self._resolve_handler(command.command_type)
                async for batch in handler.execute(command, aggregate):
                    events = await self._unit_of_work.commit_batch(command, batch)
                    refreshed = await self._unit_of_work.get_case(command.case_id)
                    aggregate.state = refreshed.state
                    aggregate.version = refreshed.version
                    aggregate.updated_at = refreshed.updated_at
                    for event in events:
                        await self._broadcast.publish(event)
                        yield event
                completed = await self._unit_of_work.finish_command(command)
                await self._broadcast.publish(completed)
                yield completed
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                failed = await self._unit_of_work.finish_command(command, error=exc)
                await self._broadcast.publish(failed)
                yield failed
            finally:
                self._queue.task_done()

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

    def _resolve_handler(self, command_type: str) -> CommandHandler:
        matches = [handler for handler in self._handlers if handler.supports(command_type)]
        if len(matches) != 1:
            raise RuntimeError(
                f"expected exactly one handler for {command_type}, got {len(matches)}"
            )
        return matches[0]
