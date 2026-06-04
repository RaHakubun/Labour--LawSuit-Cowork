from __future__ import annotations

from collections import defaultdict
from typing import Any, Callable
from uuid import uuid4

from .conversation_contract import now_utc_iso


EventHandler = Callable[[dict[str, Any]], None]


def build_event(
    session_id: str,
    turn_id: int,
    stage: str,
    event_type: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    return {
        "event_id": uuid4().hex,
        "session_id": str(session_id),
        "turn_id": int(turn_id),
        "stage": str(stage),
        "event_type": str(event_type),
        "created_at_utc": now_utc_iso(),
        "payload": dict(payload),
    }


class EventBus:
    """Synchronous in-process event bus for deterministic agent workflows."""

    def __init__(self) -> None:
        self._subscribers: dict[str, list[EventHandler]] = defaultdict(list)

    def subscribe(self, event_type: str, handler: EventHandler) -> None:
        self._subscribers[str(event_type)].append(handler)

    def publish(self, event: dict[str, Any]) -> None:
        event_type = str(event.get("event_type", ""))
        for handler in list(self._subscribers.get(event_type, [])):
            handler(event)

