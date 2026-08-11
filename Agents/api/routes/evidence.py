from __future__ import annotations

from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile, status

from Agents.api.auth import BearerTokenAuthenticator
from Agents.application.command_service import CaseCommandService
from Agents.domain.commands import RegisterEvidencePayload
from Agents.infrastructure.evidence_storage import LocalEvidenceStorage

from .common import OwnedCase


def evidence_router(
    *,
    authenticator: BearerTokenAuthenticator,
    command_service: CaseCommandService,
    storage: LocalEvidenceStorage,
    owned_case: OwnedCase,
) -> APIRouter:
    router = APIRouter(prefix="/api/v1")

    @router.post("/cases/{case_id}/evidence", status_code=status.HTTP_202_ACCEPTED)
    async def upload_evidence(
        case_id: UUID,
        file: UploadFile = File(...),
        idempotency_key: str = Query(min_length=1, max_length=200),
        expected_case_version: int = Query(ge=0),
        actor: str = Depends(authenticator.actor_id),
    ) -> dict[str, Any]:
        await owned_case(case_id, actor)
        content = await file.read(storage.max_bytes + 1)
        stored = None
        try:
            stored = await storage.save(
                display_name=file.filename or "evidence",
                media_type=file.content_type or "application/octet-stream",
                content=content,
            )
            result = await command_service.submit(
                case_id=case_id,
                actor_id=actor,
                idempotency_key=idempotency_key,
                expected_case_version=expected_case_version,
                payload=RegisterEvidencePayload(
                    evidence_id=stored.evidence_id,
                    display_name=stored.display_name,
                    storage_key=stored.storage_key,
                    media_type=stored.media_type,
                    sha256=stored.sha256,
                    size=stored.size,
                ),
            )
            response_evidence_id = stored.evidence_id
            if not result.is_new:
                await storage.delete(stored.storage_key)
                existing_payload = result.command.payload
                if not isinstance(existing_payload, RegisterEvidencePayload):
                    raise ValueError("idempotency key belongs to another command type")
                response_evidence_id = existing_payload.evidence_id
        except (ValueError, RuntimeError) as exc:
            if stored is not None:
                await storage.delete(stored.storage_key)
            retryable = isinstance(exc, RuntimeError)
            raise HTTPException(
                status_code=503 if retryable else 422,
                detail={
                    "code": "evidence_upload_failed",
                    "message": str(exc),
                    "retryable": retryable,
                },
            ) from exc
        return {
            "case_id": str(case_id),
            "command_id": str(result.command.command_id),
            "evidence_id": str(response_evidence_id),
            "status": "accepted",
            "idempotent_replay": not result.is_new,
        }

    return router
