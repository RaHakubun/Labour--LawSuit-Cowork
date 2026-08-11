from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse

from Agents.api.auth import BearerTokenAuthenticator
from Agents.runtime.registry import CaseRuntimeRegistry

from .common import OwnedCase


def event_stream_router(
    *,
    authenticator: BearerTokenAuthenticator,
    registry: CaseRuntimeRegistry,
    owned_case: OwnedCase,
) -> APIRouter:
    router = APIRouter(prefix="/api/v1")

    @router.get("/cases/{case_id}/events")
    async def stream_events(
        case_id: UUID,
        request: Request,
        after_sequence: int = Query(default=0, ge=0),
        actor: str = Depends(authenticator.actor_id),
    ) -> StreamingResponse:
        await owned_case(case_id, actor)
        last_event_id = request.headers.get("Last-Event-ID", "").strip()
        if last_event_id:
            try:
                after_sequence = max(after_sequence, int(last_event_id))
            except ValueError as exc:
                raise HTTPException(
                    status_code=400,
                    detail={
                        "code": "invalid_event_cursor",
                        "message": "Last-Event-ID must be an integer case sequence",
                    },
                ) from exc
        runtime = await registry.get_or_create(case_id)

        async def event_source() -> AsyncIterator[str]:
            queue: asyncio.Queue[Any] = asyncio.Queue(maxsize=256)
            end_marker = object()

            async def forward_events() -> None:
                try:
                    async for event in runtime.stream(after_sequence=after_sequence):
                        await queue.put(event)
                finally:
                    await queue.put(end_marker)

            producer = asyncio.create_task(forward_events())
            try:
                while True:
                    try:
                        item = await asyncio.wait_for(queue.get(), timeout=15)
                    except TimeoutError:
                        yield ": keep-alive\n\n"
                        continue
                    if item is end_marker:
                        break
                    data = json.dumps(item.model_dump(mode="json"), ensure_ascii=False)
                    yield f"id: {item.sequence}\nevent: {item.event_type}\ndata: {data}\n\n"
            finally:
                producer.cancel()
                await asyncio.gather(producer, return_exceptions=True)

        return StreamingResponse(
            event_source(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    return router
