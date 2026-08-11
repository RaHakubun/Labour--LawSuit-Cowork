from __future__ import annotations

from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query

from Agents.api.auth import BearerTokenAuthenticator
from Agents.infrastructure.uow import CaseUnitOfWork

from .common import OwnedCase, case_summary


def query_router(
    *,
    authenticator: BearerTokenAuthenticator,
    uow: CaseUnitOfWork,
    owned_case: OwnedCase,
) -> APIRouter:
    router = APIRouter(prefix="/api/v1")

    @router.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok", "runtime": "async-case-v1"}

    @router.get("/cases")
    async def list_cases(
        actor: str = Depends(authenticator.actor_id),
    ) -> dict[str, Any]:
        return {
            "cases": [case_summary(aggregate) for aggregate in await uow.list_cases(actor)]
        }

    @router.get("/cases/{case_id}")
    async def get_case(
        case_id: UUID,
        actor: str = Depends(authenticator.actor_id),
    ) -> dict[str, Any]:
        return case_summary(await owned_case(case_id, actor))

    @router.get("/cases/{case_id}/events/history")
    async def event_history(
        case_id: UUID,
        after_sequence: int = Query(default=0, ge=0),
        limit: int = Query(default=100, ge=1, le=200),
        actor: str = Depends(authenticator.actor_id),
    ) -> dict[str, Any]:
        await owned_case(case_id, actor)
        events = await uow.list_events(
            case_id,
            after_sequence=after_sequence,
            user_visible_only=True,
            limit=limit + 1,
        )
        has_more = len(events) > limit
        page = events[:limit]
        return {
            "case_id": str(case_id),
            "events": [event.model_dump(mode="json") for event in page],
            "next_after_sequence": page[-1].sequence if has_more and page else None,
        }

    @router.get("/cases/{case_id}/artifacts")
    async def list_artifacts(
        case_id: UUID,
        actor: str = Depends(authenticator.actor_id),
    ) -> dict[str, Any]:
        aggregate = await owned_case(case_id, actor)
        return {
            "case_id": str(case_id),
            "artifacts": [
                item.model_dump(
                    mode="json",
                    exclude={"revisions": {"__all__": {"content"}}},
                )
                for item in aggregate.state.outputs.artifacts.values()
            ],
        }

    @router.get("/cases/{case_id}/artifacts/{artifact_id}")
    async def get_artifact(
        case_id: UUID,
        artifact_id: UUID,
        actor: str = Depends(authenticator.actor_id),
    ) -> dict[str, Any]:
        aggregate = await owned_case(case_id, actor)
        artifact = aggregate.state.outputs.artifacts.get(artifact_id)
        if artifact is None:
            raise HTTPException(
                status_code=404,
                detail={"code": "artifact_not_found", "message": "artifact not found"},
            )
        return artifact.model_dump(mode="json")

    return router
