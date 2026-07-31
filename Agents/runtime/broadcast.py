from __future__ import annotations

import asyncio
from uuid import UUID, uuid4

from Agents.domain.events import EventEnvelope


class CaseBroadcast:
    def __init__(self, subscriber_capacity: int = 256) -> None:
        self._subscriber_capacity = subscriber_capacity
        self._subscribers: dict[UUID, asyncio.Queue[EventEnvelope | None]] = {}
        self._lock = asyncio.Lock()

    async def subscribe(self) -> tuple[UUID, asyncio.Queue[EventEnvelope | None]]:
        subscriber_id = uuid4()
        queue: asyncio.Queue[EventEnvelope | None] = asyncio.Queue(
            maxsize=self._subscriber_capacity
        )
        async with self._lock:
            self._subscribers[subscriber_id] = queue
        return subscriber_id, queue

    async def unsubscribe(self, subscriber_id: UUID) -> None:
        async with self._lock:
            self._subscribers.pop(subscriber_id, None)

    async def publish(self, event: EventEnvelope) -> None:
        async with self._lock:
            subscribers = list(self._subscribers.values())
        overflowed: list[asyncio.Queue[EventEnvelope | None]] = []
        for queue in subscribers:
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                overflowed.append(queue)
        if overflowed:
            async with self._lock:
                for subscriber_id, queue in list(self._subscribers.items()):
                    if queue not in overflowed:
                        continue
                    self._subscribers.pop(subscriber_id, None)
                    try:
                        queue.get_nowait()
                    except asyncio.QueueEmpty:
                        pass
                    queue.put_nowait(None)
