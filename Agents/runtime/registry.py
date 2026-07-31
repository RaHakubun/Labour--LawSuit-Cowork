from __future__ import annotations

import asyncio
from collections.abc import Sequence
from uuid import UUID

from Agents.application.handlers.base import CommandHandler
from Agents.infrastructure.uow import CaseUnitOfWork

from .case_runtime import CaseRuntime


class CaseRuntimeRegistry:
    def __init__(
        self,
        *,
        unit_of_work: CaseUnitOfWork,
        handlers: Sequence[CommandHandler],
        queue_capacity: int = 64,
    ) -> None:
        self._unit_of_work = unit_of_work
        self._handlers = tuple(handlers)
        self._queue_capacity = queue_capacity
        self._runtimes: dict[UUID, CaseRuntime] = {}
        self._tasks: dict[UUID, asyncio.Task[None]] = {}
        self._lock = asyncio.Lock()
        self._closing = False

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
            return runtime

    async def shutdown(self, timeout: float = 30.0) -> None:
        async with self._lock:
            self._closing = True
            runtimes = list(self._runtimes.values())
            tasks = list(self._tasks.values())
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

    async def _drain(self, runtime: CaseRuntime) -> None:
        async for _event in runtime.run():
            pass
