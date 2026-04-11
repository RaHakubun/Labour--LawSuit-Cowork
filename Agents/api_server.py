from __future__ import annotations

from pathlib import Path
import os
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from .session_service import MultiAgentSessionService


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


class SessionsListResponse(BaseModel):
    sessions: list[SessionSummaryResponse]


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

    @app.exception_handler(ValueError)
    def handle_value_error(_req: Request, exc: ValueError) -> JSONResponse:
        return JSONResponse(status_code=400, content={"detail": str(exc)})

    @app.get("/api/v1/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/api/v1/sessions", response_model=SessionsListResponse)
    def list_sessions(role_id: str | None = None) -> SessionsListResponse:
        summaries = service.list_sessions(role_id=role_id)
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

    @app.post("/api/v1/sessions/{session_id}/turns", response_model=SessionTurnResponseModel)
    def submit_turn(session_id: str, req: SubmitTurnRequest) -> SessionTurnResponseModel:
        if session_id not in service.sessions:
            raise HTTPException(status_code=404, detail=f"session not found: {session_id}")
        turn = service.submit_turn(
            session_id=session_id,
            user_input=req.user_input,
            attachments_meta=req.attachments_meta,
        )
        return SessionTurnResponseModel(**turn.to_dict())

    @app.post(
        "/api/v1/sessions/{session_id}/handoff/confirm",
        response_model=SessionTurnResponseModel,
    )
    def confirm_handoff(session_id: str, req: ConfirmHandoffRequest) -> SessionTurnResponseModel:
        if session_id not in service.sessions:
            raise HTTPException(status_code=404, detail=f"session not found: {session_id}")
        turn = service.confirm_handoff(session_id=session_id, approve=req.approve)
        return SessionTurnResponseModel(**turn.to_dict())

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
