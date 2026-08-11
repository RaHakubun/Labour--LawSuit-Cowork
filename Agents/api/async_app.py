from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
from collections.abc import AsyncIterator, Awaitable, Callable
from pathlib import Path
import tempfile
from typing import Any
from uuid import UUID

from fastapi import Depends, FastAPI, File, HTTPException, Query, Request, UploadFile, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field, TypeAdapter, ValidationError

from Agents.application.command_service import CaseCommandService
from Agents.application.orchestrator import CaseOrchestrator
from Agents.application.decisions import ControllerDecisionProvider
from Agents.application.handlers.intake import ControllerCommandHandler
from Agents.application.handlers.evidence import EvidenceCommandHandler
from Agents.application.handlers.base import CommandHandler
from Agents.application.handlers.facts import ConfirmFactCommandHandler
from Agents.application.handlers.legal import LegalCommandHandler
from Agents.application.handlers.rules import RuleCalculationCommandHandler
from Agents.application.legal_models import LegalResultProvider
from Agents.application.handlers.scenario import ScenarioStageHandler
from Agents.application.scenario_models import ScenarioResultProvider
from Agents.domain.commands import CommandPayload, RegisterEvidencePayload
from Agents.infrastructure.evidence_storage import LocalEvidenceStorage
from Agents.infrastructure.memory_uow import InMemoryCaseUnitOfWork
from Agents.infrastructure.uow import CaseUnitOfWork
from Agents.runtime.registry import CaseRuntimeRegistry
from Agents.services.tool_hub import ToolHub
from Agents.services.evidence_parser import EvidenceParser
from Agents.services.ocr import VisionOcrAdapter

from .auth import BearerTokenAuthenticator

_COMMAND_PAYLOAD_ADAPTER: TypeAdapter[CommandPayload] = TypeAdapter(CommandPayload)


class CreateCaseRequest(BaseModel):
    role_id: str = Field(min_length=1)


class SubmitCommandRequest(BaseModel):
    idempotency_key: str = Field(min_length=1, max_length=200)
    expected_case_version: int = Field(ge=0)
    payload: dict[str, Any]


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
    registry = CaseRuntimeRegistry(
        unit_of_work=uow,
        handlers=handlers,
    )
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
    app.add_middleware(
        CORSMiddleware,
        allow_origins=allowed_origins
        or ["http://localhost:5173", "http://127.0.0.1:5173"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    async def owned_case(case_id: UUID, actor: str) -> Any:
        try:
            aggregate = await uow.get_case(case_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="case not found") from exc
        if aggregate.owner_id != actor:
            raise HTTPException(status_code=403, detail="case access denied")
        return aggregate

    @app.get("/api/v1/health")
    async def health() -> dict[str, str]:
        return {"status": "ok", "runtime": "async-case-v1"}

    @app.post("/api/v1/cases", status_code=status.HTTP_201_CREATED)
    async def create_case(
        request: CreateCaseRequest,
        actor: str = Depends(authenticator.actor_id),
    ) -> dict[str, Any]:
        aggregate = await command_service.create_case(
            owner_id=actor,
            role_id=request.role_id,
        )
        return _case_summary(aggregate)

    @app.get("/api/v1/cases")
    async def list_cases(
        actor: str = Depends(authenticator.actor_id),
    ) -> dict[str, Any]:
        return {
            "cases": [
                _case_summary(aggregate)
                for aggregate in await uow.list_cases(actor)
            ]
        }

    @app.get("/api/v1/cases/{case_id}")
    async def get_case(
        case_id: UUID,
        actor: str = Depends(authenticator.actor_id),
    ) -> dict[str, Any]:
        return _case_summary(await owned_case(case_id, actor))

    @app.post(
        "/api/v1/cases/{case_id}/commands",
        status_code=status.HTTP_202_ACCEPTED,
    )
    async def submit_command(
        case_id: UUID,
        request: SubmitCommandRequest,
        actor: str = Depends(authenticator.actor_id),
    ) -> dict[str, Any]:
        await owned_case(case_id, actor)
        try:
            payload = _COMMAND_PAYLOAD_ADAPTER.validate_python(request.payload)
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
                detail={"code": "invalid_command", "fields": exc.errors()},
            ) from exc
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail="case access denied") from exc
        except ValueError as exc:
            raise HTTPException(
                status_code=409,
                detail={"code": "case_version_conflict", "message": str(exc)},
            ) from exc
        except RuntimeError as exc:
            code = "case_busy" if str(exc) == "case_busy" else "runtime_unavailable"
            raise HTTPException(
                status_code=429 if code == "case_busy" else 503,
                detail={"code": code, "message": str(exc)},
            ) from exc
        return {
            "case_id": str(case_id),
            "command_id": str(result.command.command_id),
            "accepted": result.is_new,
            "idempotent_replay": not result.is_new,
        }

    @app.post(
        "/api/v1/cases/{case_id}/evidence",
        status_code=status.HTTP_202_ACCEPTED,
    )
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
            raise HTTPException(
                status_code=422 if isinstance(exc, ValueError) else 503,
                detail={"code": "evidence_upload_failed", "message": str(exc)},
            ) from exc
        return {
            "case_id": str(case_id),
            "command_id": str(result.command.command_id),
            "evidence_id": str(response_evidence_id),
            "status": "accepted",
            "idempotent_replay": not result.is_new,
        }

    @app.get("/api/v1/cases/{case_id}/events/history")
    async def event_history(
        case_id: UUID,
        after_sequence: int = Query(default=0, ge=0),
        actor: str = Depends(authenticator.actor_id),
    ) -> dict[str, Any]:
        await owned_case(case_id, actor)
        events = await uow.list_events(
            case_id,
            after_sequence=after_sequence,
            user_visible_only=True,
        )
        return {
            "case_id": str(case_id),
            "events": [event.model_dump(mode="json") for event in events],
        }

    @app.get("/api/v1/cases/{case_id}/events")
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
                    detail="Last-Event-ID must be an integer case sequence",
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
            headers={
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",
            },
        )

    @app.get("/api/v1/cases/{case_id}/artifacts")
    async def list_artifacts(
        case_id: UUID,
        actor: str = Depends(authenticator.actor_id),
    ) -> dict[str, Any]:
        aggregate = await owned_case(case_id, actor)
        return {
            "case_id": str(case_id),
            "artifacts": [
                item.model_dump(mode="json", exclude={"revisions": {"__all__": {"content"}}})
                for item in aggregate.state.outputs.artifacts.values()
            ],
        }

    @app.get("/api/v1/cases/{case_id}/artifacts/{artifact_id}")
    async def get_artifact(
        case_id: UUID,
        artifact_id: UUID,
        actor: str = Depends(authenticator.actor_id),
    ) -> dict[str, Any]:
        aggregate = await owned_case(case_id, actor)
        artifact = aggregate.state.outputs.artifacts.get(artifact_id)
        if artifact is None:
            raise HTTPException(status_code=404, detail="artifact not found")
        return artifact.model_dump(mode="json")

    return app


def _case_summary(aggregate) -> dict[str, Any]:
    interaction = aggregate.state.interaction
    return {
        "case_id": str(aggregate.case_id),
        "owner_id": aggregate.owner_id,
        "role_id": aggregate.role_id,
        "version": aggregate.version,
        "stage": interaction.stage.value,
        "active_agent": interaction.active_agent,
        "active_scene_id": interaction.active_scene_id or None,
        "current_goal": interaction.current_goal,
        "pending_questions": [
            item.model_dump(mode="json") for item in interaction.pending_questions
        ],
        "candidate_facts": [
            item.model_dump(mode="json")
            for item in aggregate.state.facts.items.values()
        ],
        "authorities": [
            {
                "authority_id": str(item.authority_id),
                "tool_name": item.tool_name,
                "query": item.query,
                "source_id": item.source_id,
                "title": item.title,
                "source_url": item.source_url,
                "content_hash": item.content_hash,
                "parsed_status": item.parsed_status,
                "retrieved_at": item.retrieved_at.isoformat(),
            }
            for item in aggregate.state.analysis.authorities.values()
        ],
        "evidence": [
            item.model_dump(mode="json")
            for item in aggregate.state.evidence.items.values()
        ],
        "fact_conflicts": [
            item.model_dump(mode="json")
            for item in aggregate.state.facts.conflicts.values()
            if not item.resolved
        ],
        "issues": [
            item.model_dump(mode="json")
            for item in aggregate.state.analysis.issues.values()
        ],
        "rule_results": [
            item.model_dump(mode="json")
            for item in aggregate.state.analysis.rule_results.values()
        ],
        "artifacts": [
            {
                "artifact_id": str(item.artifact_id),
                "artifact_type": item.artifact_type,
                "title": item.title,
                "stale": item.stale,
                "revision_count": len(item.revisions),
            }
            for item in aggregate.state.outputs.artifacts.values()
        ],
        "updated_at": aggregate.updated_at.isoformat(),
    }
