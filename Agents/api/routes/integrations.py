from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Response, status

from Agents.infrastructure.integration_credentials import (
    IntegrationCredentialInput,
    IntegrationProvider,
    PostgresIntegrationCredentialStore,
)

from ..auth import BearerTokenAuthenticator


def integration_router(
    *,
    authenticator: BearerTokenAuthenticator,
    store: PostgresIntegrationCredentialStore,
    admin_actor_ids: frozenset[str],
) -> APIRouter:
    router = APIRouter(prefix="/api/v1/integrations", tags=["integrations"])

    async def integration_admin(
        actor: str = Depends(authenticator.actor_id),
    ) -> str:
        if actor not in admin_actor_ids:
            raise HTTPException(
                status_code=403,
                detail={
                    "code": "integration_admin_required",
                    "message": "integration administrator access is required",
                    "retryable": False,
                },
            )
        return actor

    @router.get("")
    async def list_integrations(
        _actor: str = Depends(authenticator.actor_id),
    ) -> dict[str, object]:
        return {
            "integrations": [
                item.model_dump(mode="json") for item in await store.list_statuses()
            ]
        }

    @router.put("/{provider}")
    async def update_integration(
        provider: IntegrationProvider,
        payload: IntegrationCredentialInput,
        _actor: str = Depends(integration_admin),
    ) -> dict[str, object]:
        try:
            saved = await store.upsert(provider, payload)
        except ValueError as exc:
            raise HTTPException(
                status_code=422,
                detail={
                    "code": "invalid_integration_configuration",
                    "message": str(exc),
                    "retryable": False,
                },
            ) from exc
        return saved.model_dump(mode="json")

    @router.delete("/{provider}", status_code=status.HTTP_204_NO_CONTENT)
    async def delete_integration(
        provider: IntegrationProvider,
        _actor: str = Depends(integration_admin),
    ) -> Response:
        await store.delete(provider)
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    return router
