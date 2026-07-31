from __future__ import annotations

import asyncio
import concurrent.futures
from pathlib import Path
import os
import uuid
import queue
import re
from typing import Any

from fastapi import FastAPI, HTTPException, Request, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field

from .session_service import MultiAgentSessionService
from utils.city_wage_data import list_cities, list_provinces
from utils.labour_calculator import LabourCalculatorEngine


def _try_mount_rag_app(app: "FastAPI") -> None:
    """Mount RAG only when explicitly enabled; bad configuration fails loudly."""
    if os.getenv("ENABLE_RAG", "").strip().lower() not in {"1", "true", "yes"}:
        return
    from rag_app.main import create_app as create_rag_app
    from rag_app.config import load_settings as load_rag_settings

    rag_settings = load_rag_settings()
    rag_sub = create_rag_app(settings=rag_settings)
    app.mount("/rag", rag_sub)


class CreateSessionRequest(BaseModel):
    role_id: str = Field(..., description="worker | employer | lawyer")


class CreateSessionResponse(BaseModel):
    session_id: str
    role_id: str
    active_agent: str
    stage: str


class SubmitTurnRequest(BaseModel):
    user_input: str = Field(..., min_length=1, description="current user message")
    attachments_meta: Any | None = Field(default=None)


class SessionSummaryResponse(BaseModel):
    session_id: str
    role_id: str
    stage: str
    title: str = ""
    created_at_utc: str = ""
    updated_at_utc: str = ""
    turn_id: int
    scene_id: str
    message_count: int
    handoff_count: int
    current_module_key: str = ""
    pending_transition: dict[str, Any] | None = None
    event_count: int = 0
    has_legal_report: bool = False
    legal_report_updated_at_utc: str = ""


class SessionTurnResponseModel(BaseModel):
    session_id: str
    role_id: str
    active_agent: str
    askmore: str
    messages: list[dict[str, Any]]
    handoffs: list[dict[str, Any]]
    pending_transition: dict[str, Any] | None = None
    requires_handoff_confirmation: bool = False
    events: list[dict[str, Any]] = Field(default_factory=list)


class ConfirmHandoffRequest(BaseModel):
    approve: bool = Field(..., description="true=confirm handoff, false=reject handoff")


class SessionMessagesResponse(BaseModel):
    session_id: str
    messages: list[dict[str, Any]]


class SessionEventsResponse(BaseModel):
    session_id: str
    events: list[dict[str, Any]]


class CaseStateResponse(BaseModel):
    session_id: str
    role_id: str
    case_state: dict[str, Any]
    case_version: int
    last_patch_results: list[dict[str, Any]] = Field(default_factory=list)
    events: list[dict[str, Any]] = Field(default_factory=list)


class SessionsListResponse(BaseModel):
    sessions: list[SessionSummaryResponse]


class LegalReportResponse(BaseModel):
    session_id: str
    filename: str
    markdown: str
    updated_at_utc: str


class CalculatorCatalogResponse(BaseModel):
    calc_types: list[str]
    provinces: list[str]


class CalculatorCitiesResponse(BaseModel):
    province: str
    cities: list[str]


class CalculatorComputeRequest(BaseModel):
    calc_type: str = Field(..., description="severance | overtime | wage_base | medical_period | annual_leave_unused | double_wage_unsigned_contract")
    payload: dict[str, Any] = Field(default_factory=dict)


class CalculatorComputeResponse(BaseModel):
    ok: bool
    result: dict[str, Any]
    breakdown: dict[str, Any]
    inputs: dict[str, Any]
    rules_applied: list[str]
    errors: list[str]


class UploadFileResponse(BaseModel):
    file_id: str
    name: str
    size: int


def create_app(service: MultiAgentSessionService | None = None) -> FastAPI:
    app = FastAPI(title="Labour Multi-Agent Backend", version="1.0.0")

    # Enable browser preflight (OPTIONS) for frontend dev and local deployment.
    raw_origins = os.getenv("ALLOWED_ORIGINS", "").strip()
    if raw_origins:
        allow_origins = [item.strip() for item in raw_origins.split(",") if item.strip()]
    else:
        allow_origins = [
            "http://localhost:5173",
            "http://127.0.0.1:5173",
            "http://localhost:3000",
            "http://127.0.0.1:3000",
        ]

    app.add_middleware(
        CORSMiddleware,
        allow_origins=allow_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    service = service or MultiAgentSessionService()
    calculator_engine = LabourCalculatorEngine()
    _executor = concurrent.futures.ThreadPoolExecutor(max_workers=8)

    _try_mount_rag_app(app)

    @app.exception_handler(ValueError)
    def handle_value_error(_req: Request, exc: ValueError) -> JSONResponse:
        return JSONResponse(status_code=400, content={"detail": str(exc)})

    @app.get("/api/v1/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/api/v1/sessions", response_model=SessionsListResponse)
    async def list_sessions(role_id: str | None = None) -> SessionsListResponse:
        loop = asyncio.get_event_loop()
        summaries = await loop.run_in_executor(_executor, lambda: service.list_sessions(role_id=role_id))
        return SessionsListResponse(sessions=[SessionSummaryResponse(**item) for item in summaries])

    @app.post("/api/v1/sessions", response_model=CreateSessionResponse)
    def create_session(req: CreateSessionRequest) -> CreateSessionResponse:
        state = service.create_session(req.role_id)
        return CreateSessionResponse(
            session_id=state.session_id,
            role_id=state.role_id,
            active_agent="ControllerAgent",
            stage=state.stage,
        )

    @app.get("/api/v1/sessions/{session_id}", response_model=SessionSummaryResponse)
    def get_session(session_id: str) -> SessionSummaryResponse:
        if session_id not in service.sessions:
            raise HTTPException(status_code=404, detail=f"session not found: {session_id}")
        summary = service.get_session_summary(session_id)
        return SessionSummaryResponse(**summary)

    @app.delete("/api/v1/sessions/{session_id}")
    def delete_session(session_id: str) -> dict[str, str]:
        if session_id not in service.sessions:
            raise HTTPException(status_code=404, detail=f"session not found: {session_id}")
        service.delete_session(session_id)
        return {"status": "deleted", "session_id": session_id}

    @app.get("/api/v1/sessions/{session_id}/messages", response_model=SessionMessagesResponse)
    def get_messages(session_id: str) -> SessionMessagesResponse:
        if session_id not in service.sessions:
            raise HTTPException(status_code=404, detail=f"session not found: {session_id}")
        messages = service.list_messages(session_id)
        return SessionMessagesResponse(session_id=session_id, messages=messages)

    @app.get("/api/v1/sessions/{session_id}/events", response_model=SessionEventsResponse)
    def get_events(session_id: str) -> SessionEventsResponse:
        if session_id not in service.sessions:
            raise HTTPException(status_code=404, detail=f"session not found: {session_id}")
        events = service.list_events(session_id)
        return SessionEventsResponse(session_id=session_id, events=events)

    @app.get("/api/v1/sessions/{session_id}/case-state", response_model=CaseStateResponse)
    def get_case_state(session_id: str) -> CaseStateResponse:
        if session_id not in service.sessions:
            raise HTTPException(status_code=404, detail=f"session not found: {session_id}")
        return CaseStateResponse(**service.get_case_state(session_id))

    @app.get("/api/v1/sessions/{session_id}/legal-report", response_model=LegalReportResponse)
    def get_legal_report(session_id: str) -> LegalReportResponse:
        if session_id not in service.sessions:
            raise HTTPException(status_code=404, detail=f"session not found: {session_id}")
        payload = service.get_legal_report(session_id)
        return LegalReportResponse(session_id=session_id, **payload)

    @app.post("/api/v1/sessions/{session_id}/generate-report", response_model=LegalReportResponse)
    async def generate_report(session_id: str) -> LegalReportResponse:
        if session_id not in service.sessions:
            raise HTTPException(status_code=404, detail=f"session not found: {session_id}")
        loop = asyncio.get_event_loop()
        payload = await loop.run_in_executor(_executor, lambda: service.generate_analysis_report(session_id))
        return LegalReportResponse(session_id=session_id, **payload)

    @app.post("/api/v1/sessions/{session_id}/turns", response_model=SessionTurnResponseModel)
    async def submit_turn(session_id: str, req: SubmitTurnRequest) -> SessionTurnResponseModel:
        if session_id not in service.sessions:
            raise HTTPException(status_code=404, detail=f"session not found: {session_id}")
        loop = asyncio.get_event_loop()
        turn = await loop.run_in_executor(
            _executor,
            lambda: service.submit_turn(
                session_id=session_id,
                user_input=req.user_input,
                attachments_meta=req.attachments_meta,
            ),
        )
        return SessionTurnResponseModel(**turn.to_dict())

    @app.post("/api/v1/sessions/{session_id}/turns/stream")
    async def submit_turn_stream(session_id: str, req: SubmitTurnRequest):
        if session_id not in service.sessions:
            raise HTTPException(status_code=404, detail=f"session not found: {session_id}")

        token_queue: queue.Queue = queue.Queue()

        def on_token(text: str):
            token_queue.put(("token", text))

        def run_turn():
            try:
                turn = service.submit_turn(
                    session_id=session_id,
                    user_input=req.user_input,
                    attachments_meta=req.attachments_meta,
                    on_token=on_token,
                )
                token_queue.put(("done", turn.to_dict()))
            except Exception as exc:
                token_queue.put(("error", str(exc)))

        import json as _json
        loop = asyncio.get_event_loop()
        loop.run_in_executor(_executor, run_turn)

        async def event_generator():
            # Send keep-alive comment every 15s to prevent proxy/browser timeouts
            # Poll the queue with short sleeps instead of blocking run_in_executor
            # to avoid exhausting the default thread pool
            POLL_INTERVAL = 0.05   # 50ms poll
            KEEPALIVE_EVERY = 200  # send keep-alive after ~10s of silence (200 * 50ms)
            idle_ticks = 0
            while True:
                try:
                    item = token_queue.get_nowait()
                except queue.Empty:
                    idle_ticks += 1
                    if idle_ticks >= KEEPALIVE_EVERY:
                        idle_ticks = 0
                        yield ": keep-alive\n\n"
                    await asyncio.sleep(POLL_INTERVAL)
                    continue
                except Exception:
                    yield f"event: error\ndata: {_json.dumps({'detail': 'internal error'})}\n\n"
                    break

                idle_ticks = 0
                event_type, payload = item
                if event_type == "token":
                    yield f"event: token\ndata: {_json.dumps({'text': payload}, ensure_ascii=False)}\n\n"
                elif event_type == "done":
                    yield f"event: done\ndata: {_json.dumps(payload, ensure_ascii=False)}\n\n"
                    break
                elif event_type == "error":
                    yield f"event: error\ndata: {_json.dumps({'detail': payload}, ensure_ascii=False)}\n\n"
                    break

        return StreamingResponse(
            event_generator(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",
                "Connection": "keep-alive",
            },
        )

    @app.post(
        "/api/v1/sessions/{session_id}/handoff/confirm",
        response_model=SessionTurnResponseModel,
    )
    async def confirm_handoff(session_id: str, req: ConfirmHandoffRequest) -> SessionTurnResponseModel:
        if session_id not in service.sessions:
            raise HTTPException(status_code=404, detail=f"session not found: {session_id}")
        loop = asyncio.get_event_loop()
        turn = await loop.run_in_executor(_executor, lambda: service.confirm_handoff(session_id=session_id, approve=req.approve))
        return SessionTurnResponseModel(**turn.to_dict())

    @app.post("/api/v1/sessions/{session_id}/handoff/confirm/stream")
    async def confirm_handoff_stream(session_id: str, req: ConfirmHandoffRequest):
        if session_id not in service.sessions:
            raise HTTPException(status_code=404, detail=f"session not found: {session_id}")

        token_queue: queue.Queue = queue.Queue()

        def on_token(text: str):
            token_queue.put(("token", text))

        def run_confirm():
            try:
                turn = service.confirm_handoff(
                    session_id=session_id,
                    approve=req.approve,
                    on_token=on_token,
                )
                token_queue.put(("done", turn.to_dict()))
            except Exception as exc:
                token_queue.put(("error", str(exc)))

        import json as _json
        loop = asyncio.get_event_loop()
        loop.run_in_executor(_executor, run_confirm)

        async def event_generator():
            POLL_INTERVAL = 0.05
            KEEPALIVE_EVERY = 200
            idle_ticks = 0
            while True:
                try:
                    item = token_queue.get_nowait()
                except queue.Empty:
                    idle_ticks += 1
                    if idle_ticks >= KEEPALIVE_EVERY:
                        idle_ticks = 0
                        yield ": keep-alive\n\n"
                    await asyncio.sleep(POLL_INTERVAL)
                    continue
                except Exception:
                    yield f"event: error\ndata: {_json.dumps({'detail': 'internal error'})}\n\n"
                    break

                idle_ticks = 0
                event_type, payload = item
                if event_type == "token":
                    yield f"event: token\ndata: {_json.dumps({'text': payload}, ensure_ascii=False)}\n\n"
                elif event_type == "done":
                    yield f"event: done\ndata: {_json.dumps(payload, ensure_ascii=False)}\n\n"
                    break
                elif event_type == "error":
                    yield f"event: error\ndata: {_json.dumps({'detail': payload}, ensure_ascii=False)}\n\n"
                    break

        return StreamingResponse(
            event_generator(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",
                "Connection": "keep-alive",
            },
        )

    @app.get("/api/v1/calculator/catalog", response_model=CalculatorCatalogResponse)
    def get_calculator_catalog() -> CalculatorCatalogResponse:
        return CalculatorCatalogResponse(
            calc_types=[
                "severance",
                "overtime",
                "wage_base",
                "medical_period",
                "annual_leave_unused",
                "double_wage_unsigned_contract",
            ],
            provinces=list_provinces(),
        )

    @app.get("/api/v1/calculator/cities", response_model=CalculatorCitiesResponse)
    def get_calculator_cities(province: str) -> CalculatorCitiesResponse:
        return CalculatorCitiesResponse(province=province, cities=list_cities(province))

    @app.post("/api/v1/calculator/compute", response_model=CalculatorComputeResponse)
    def compute_calculator(req: CalculatorComputeRequest) -> CalculatorComputeResponse:
        result = calculator_engine.calculate(req.calc_type, req.payload or {})
        return CalculatorComputeResponse(**result)

    ALLOWED_EXTENSIONS = {".pdf", ".docx", ".doc", ".txt", ".png", ".jpg", ".jpeg", ".xlsx", ".xls", ".csv"}
    MAX_FILE_SIZE = 20 * 1024 * 1024  # 20 MB
    UPLOAD_DIR = Path(__file__).resolve().parent.parent / "storage" / "uploads"

    @app.post("/api/v1/upload", response_model=UploadFileResponse)
    async def upload_file(file: UploadFile = File(...)) -> UploadFileResponse:
        filename = file.filename or "unknown"
        display_name = Path(filename.replace("\\", "/")).name
        if not display_name or display_name in {".", ".."}:
            raise HTTPException(status_code=400, detail="无效的文件名")
        if re.search(r"[\x00-\x1f]", display_name):
            raise HTTPException(status_code=400, detail="文件名包含非法控制字符")
        ext = Path(display_name).suffix.lower()
        if ext not in ALLOWED_EXTENSIONS:
            raise HTTPException(status_code=400, detail=f"不支持的文件格式: {ext}")
        content = await file.read()
        if len(content) > MAX_FILE_SIZE:
            raise HTTPException(status_code=400, detail="文件大小超过 20MB 限制")
        file_id = uuid.uuid4().hex
        storage_key = f"{file_id}{ext}"
        UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
        dest = UPLOAD_DIR / storage_key
        dest.write_bytes(content)
        return UploadFileResponse(file_id=file_id, name=display_name, size=len(content))

    return app


app = create_app()


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "Agents.api_server:app",
        host="0.0.0.0",
        port=8000,
        reload=True,
        reload_dirs=[str(Path(__file__).resolve().parents[1])],
    )
