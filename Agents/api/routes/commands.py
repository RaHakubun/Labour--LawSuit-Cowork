from __future__ import annotations

from typing import Any, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field, TypeAdapter, ValidationError

from Agents.api.auth import BearerTokenAuthenticator
from Agents.application.command_service import CaseCommandService
from Agents.domain.commands import CommandPayload

from .common import OwnedCase, case_summary

_PAYLOAD_ADAPTER: TypeAdapter[CommandPayload] = TypeAdapter(CommandPayload)


class CreateCaseRequest(BaseModel):
    role_id: Literal["worker", "lawyer", "employer"]


class SubmitCommandRequest(BaseModel):
    idempotency_key: str = Field(min_length=1, max_length=200)
    expected_case_version: int = Field(ge=0)
    payload: dict[str, Any]


def command_router(
    *,
    authenticator: BearerTokenAuthenticator,
    command_service: CaseCommandService,
    owned_case: OwnedCase,
) -> APIRouter:
    router = APIRouter(prefix="/api/v1")

    @router.post("/cases", status_code=status.HTTP_201_CREATED)
    async def create_case(
        request: CreateCaseRequest,
        actor: str = Depends(authenticator.actor_id),
    ) -> dict[str, Any]:
        aggregate = await command_service.create_case(
            owner_id=actor,
            role_id=request.role_id,
        )
        return case_summary(aggregate)

    @router.post("/cases/{case_id}/commands", status_code=status.HTTP_202_ACCEPTED)
    async def submit_command(
        case_id: UUID,
        request: SubmitCommandRequest,
        actor: str = Depends(authenticator.actor_id),
    ) -> dict[str, Any]:
        await owned_case(case_id, actor)
        try:
            payload = _PAYLOAD_ADAPTER.validate_python(request.payload)
            result = await command_service.submit(
                case_id=case_id,
                actor_id=actor,
                idempotency_key=request.idempotency_key,
                expected_case_version=request.expected_case_version,
                payload=payload,
            )
        except ValidationError as exc:
            raise HTTPException(
                status_code=422,
                detail={
                    "code": "invalid_command",
                    "message": "command payload validation failed",
                    "fields": exc.errors(),
                },
            ) from exc
        except PermissionError as exc:
            raise HTTPException(
                status_code=403,
                detail={"code": "case_access_denied", "message": "case access denied"},
            ) from exc
        except ValueError as exc:
            raise HTTPException(
                status_code=409,
                detail={"code": "case_version_conflict", "message": str(exc)},
            ) from exc
        except RuntimeError as exc:
            code = "case_busy" if str(exc) == "case_busy" else "runtime_unavailable"
            raise HTTPException(
                status_code=429 if code == "case_busy" else 503,
                detail={"code": code, "message": str(exc), "retryable": True},
            ) from exc
        return {
            "case_id": str(case_id),
            "command_id": str(result.command.command_id),
            "accepted": result.is_new,
            "idempotent_replay": not result.is_new,
        }

    return router
