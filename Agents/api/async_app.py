from __future__ import annotations

import tempfile
from collections.abc import Awaitable, Callable
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any
from uuid import UUID

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from Agents.application.command_service import CaseCommandService
from Agents.application.decisions import ControllerDecisionProvider
from Agents.application.handlers.base import CommandHandler
from Agents.application.handlers.evidence import EvidenceCommandHandler
from Agents.application.handlers.facts import ConfirmFactCommandHandler
from Agents.application.handlers.intake import ControllerCommandHandler
from Agents.application.handlers.legal import LegalCommandHandler
from Agents.application.handlers.rules import RuleCalculationCommandHandler
from Agents.application.handlers.scenario import ScenarioStageHandler
from Agents.application.legal_models import LegalResultProvider
from Agents.application.orchestrator import CaseOrchestrator
from Agents.application.scenario_models import ScenarioResultProvider
from Agents.infrastructure.evidence_storage import LocalEvidenceStorage
from Agents.infrastructure.integration_credentials import PostgresIntegrationCredentialStore
from Agents.infrastructure.memory_uow import InMemoryCaseUnitOfWork
from Agents.infrastructure.uow import CaseUnitOfWork
from Agents.runtime.registry import CaseRuntimeRegistry
from Agents.services.evidence_parser import EvidenceParser
from Agents.services.ocr import VisionOcrAdapter
from Agents.services.tool_hub import ToolHub

from .auth import BearerTokenAuthenticator
from .routes.commands import command_router
from .routes.events import event_stream_router
from .routes.evidence import evidence_router
from .routes.integrations import integration_router
from .routes.queries import query_router


def create_async_case_app(
    *,
    decision_provider: ControllerDecisionProvider,
    scenario_provider: ScenarioResultProvider,
    tool_hub: ToolHub,
    legal_provider: LegalResultProvider,
    evidence_storage: LocalEvidenceStorage | None = None,
    authenticator: BearerTokenAuthenticator,
    unit_of_work: CaseUnitOfWork | None = None,
    allowed_origins: list[str] | None = None,
    shutdown_callbacks: list[Callable[[], Awaitable[None]]] | None = None,
    vision_ocr: VisionOcrAdapter | None = None,
    integration_store: PostgresIntegrationCredentialStore | None = None,
    integration_admin_actor_ids: frozenset[str] | None = None,
) -> FastAPI:
    uow = unit_of_work or InMemoryCaseUnitOfWork()
    storage = evidence_storage or LocalEvidenceStorage(
        Path(tempfile.gettempdir()) / "labour-lawsuit-evidence"
    )
    rule_handler = RuleCalculationCommandHandler()
    scenario_handler = ScenarioStageHandler(
        scenario_provider=scenario_provider,
        tool_hub=tool_hub,
        rule_handler=rule_handler,
    )
    legal_handler = LegalCommandHandler(
        legal_provider,
        text_reader=storage.read_extracted_text,
    )
    stage_handlers: list[CommandHandler] = [
        EvidenceCommandHandler(
            EvidenceParser(
                path_resolver=storage.path_for,
                text_writer=storage.save_extracted_text,
                vision_ocr=vision_ocr,
            )
        ),
        ConfirmFactCommandHandler(),
        rule_handler,
        legal_handler,
    ]
    controller_handler = ControllerCommandHandler(
        decision_provider,
        scenario_handler,
        legal_handler=legal_handler,
    )
    handlers: list[CommandHandler] = [
        CaseOrchestrator(
            controller_handler=controller_handler,
            stage_handlers=stage_handlers,
        )
    ]
    registry = CaseRuntimeRegistry(unit_of_work=uow, handlers=handlers)
    command_service = CaseCommandService(
        unit_of_work=uow,
        runtime_registry=registry,
    )

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        await registry.recover()
        yield
        await registry.shutdown()
        for callback in shutdown_callbacks or []:
            await callback()

    app = FastAPI(
        title="Labour Lawsuit Case Runtime",
        version="2.0.0",
        lifespan=lifespan,
    )
    app.state.case_uow = uow
    app.state.runtime_registry = registry
    app.state.command_service = command_service
    app.state.evidence_storage = storage
    app.state.integration_store = integration_store

    def error_body(
        *,
        code: str,
        message: str,
        retryable: bool,
        request: Request,
        command_id: str | None = None,
        fields: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        return {
            "code": code,
            "message": message,
            "retryable": retryable,
            "case_id": request.path_params.get("case_id"),
            "command_id": command_id,
            "fields": fields or [],
        }

    @app.exception_handler(HTTPException)
    async def http_error(request: Request, exc: HTTPException) -> JSONResponse:
        detail: dict[str, Any] = exc.detail if isinstance(exc.detail, dict) else {}
        default_codes = {
            400: "bad_request",
            401: "authentication_required",
            403: "case_access_denied",
            404: "resource_not_found",
            409: "case_conflict",
            422: "unprocessable_request",
            429: "case_busy",
            503: "runtime_unavailable",
        }
        message = str(detail.get("message") or exc.detail)
        return JSONResponse(
            status_code=exc.status_code,
            content=error_body(
                code=str(detail.get("code") or default_codes.get(exc.status_code, "api_error")),
                message=message,
                retryable=bool(
                    detail.get("retryable", exc.status_code in {429, 502, 503, 504})
                ),
                request=request,
                command_id=detail.get("command_id"),
                fields=detail.get("fields"),
            ),
            headers=exc.headers,
        )

    @app.exception_handler(RequestValidationError)
    async def validation_error(
        request: Request,
        exc: RequestValidationError,
    ) -> JSONResponse:
        fields = [
            {
                key: value
                for key, value in error.items()
                if key not in {"ctx", "input", "url"}
            }
            for error in exc.errors()
        ]
        return JSONResponse(
            status_code=422,
            content=error_body(
                code="request_validation_failed",
                message="request validation failed",
                retryable=False,
                request=request,
                fields=fields,
            ),
        )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=allowed_origins
        or ["http://localhost:5173", "http://127.0.0.1:5173"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    async def owned_case(case_id: UUID, actor: str):
        try:
            aggregate = await uow.get_case(case_id)
        except KeyError as exc:
            raise HTTPException(
                status_code=404,
                detail={"code": "case_not_found", "message": "case not found"},
            ) from exc
        if aggregate.owner_id != actor:
            raise HTTPException(
                status_code=403,
                detail={"code": "case_access_denied", "message": "case access denied"},
            )
        return aggregate

    app.include_router(
        command_router(
            authenticator=authenticator,
            command_service=command_service,
            owned_case=owned_case,
        )
    )
    app.include_router(
        query_router(authenticator=authenticator, uow=uow, owned_case=owned_case)
    )
    app.include_router(
        evidence_router(
            authenticator=authenticator,
            command_service=command_service,
            storage=storage,
            owned_case=owned_case,
        )
    )
    app.include_router(
        event_stream_router(
            authenticator=authenticator,
            registry=registry,
            owned_case=owned_case,
        )
    )
    if integration_store is not None:
        admin_actor_ids = integration_admin_actor_ids
        if admin_actor_ids is None:
            if len(authenticator.actor_ids) != 1:
                raise RuntimeError(
                    "INTEGRATION_ADMIN_ACTOR_IDS is required when multiple actors are configured"
                )
            admin_actor_ids = authenticator.actor_ids
        app.include_router(
            integration_router(
                authenticator=authenticator,
                store=integration_store,
                admin_actor_ids=admin_actor_ids,
            )
        )
    return app
