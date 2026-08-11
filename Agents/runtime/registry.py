from __future__ import annotations

import asyncio
from collections.abc import Sequence
from uuid import UUID

from Agents.application.handlers.base import CommandHandler
from Agents.infrastructure.uow import CaseUnitOfWork
from Agents.domain.errors import InterruptedOperationError

from .case_runtime import CaseRuntime


class CaseRuntimeRegistry:
    def __init__(
        self,
        *,
        unit_of_work: CaseUnitOfWork,
        handlers: Sequence[CommandHandler],
        queue_capacity: int = 64,
        idle_timeout: float = 300.0,
        reap_interval: float = 30.0,
    ) -> None:
        self._unit_of_work = unit_of_work
        self._handlers = tuple(handlers)
        self._queue_capacity = queue_capacity
        self._runtimes: dict[UUID, CaseRuntime] = {}
        self._tasks: dict[UUID, asyncio.Task[None]] = {}
        self._lock = asyncio.Lock()
        self._closing = False
        self._idle_timeout = idle_timeout
        self._reap_interval = reap_interval
        self._reaper_task: asyncio.Task[None] | None = None

    async def get_or_create(self, case_id: UUID) -> CaseRuntime:
        async with self._lock:
            if self._closing:
                raise RuntimeError("runtime registry is shutting down")
            existing = self._runtimes.get(case_id)
            if existing is not None:
                return existing
            await self._unit_of_work.get_case(case_id)
            runtime = CaseRuntime(
                case_id=case_id,
                unit_of_work=self._unit_of_work,
                handlers=self._handlers,
                queue_capacity=self._queue_capacity,
            )
            task = asyncio.create_task(
                self._drain(runtime),
                name=f"case-runtime-{case_id}",
            )
            self._runtimes[case_id] = runtime
            self._tasks[case_id] = task
            if self._reaper_task is None:
                self._reaper_task = asyncio.create_task(
                    self._reap_loop(),
                    name="case-runtime-reaper",
                )
            return runtime

    async def shutdown(self, timeout: float = 30.0) -> None:
        async with self._lock:
            self._closing = True
            runtimes = list(self._runtimes.values())
            tasks = list(self._tasks.values())
            reaper = self._reaper_task
        try:
            await asyncio.wait_for(
                asyncio.gather(*(runtime.wait_idle() for runtime in runtimes)),
                timeout=timeout,
            )
        finally:
            for runtime in runtimes:
                runtime.close()
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            if reaper is not None:
                reaper.cancel()
                await asyncio.gather(reaper, return_exceptions=True)

    async def recover(self) -> int:
        interrupted = await self._unit_of_work.list_interrupted_commands()
        for command in interrupted:
            await self._unit_of_work.finish_command(
                command,
                error=InterruptedOperationError(),
            )
        commands = await self._unit_of_work.list_recoverable_commands()
        for command in commands:
            runtime = await self.get_or_create(command.case_id)
            await runtime.restore(command)
        return len(commands)

    async def evict_idle(self, *, idle_seconds: float | None = None) -> int:
        threshold = self._idle_timeout if idle_seconds is None else idle_seconds
        async with self._lock:
            candidates = [
                case_id
                for case_id, runtime in self._runtimes.items()
                if runtime.is_idle_for(threshold)
            ]
            runtimes = [self._runtimes.pop(case_id) for case_id in candidates]
            tasks = [self._tasks.pop(case_id) for case_id in candidates]
        for runtime in runtimes:
            runtime.close()
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        return len(candidates)

    async def _reap_loop(self) -> None:
        while True:
            await asyncio.sleep(self._reap_interval)
            await self.evict_idle()

    async def _drain(self, runtime: CaseRuntime) -> None:
        async for _event in runtime.run():
            pass
