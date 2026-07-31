from __future__ import annotations

import os

from Agents.api.async_app import create_async_case_app
from Agents.api.auth import BearerTokenAuthenticator
from Agents.infrastructure.database import create_engine, create_session_factory
from Agents.infrastructure.llm_adapter import AsyncOpenAIControllerDecisionProvider
from Agents.infrastructure.postgres_uow import PostgresCaseUnitOfWork


def build_app():
    raw_origins = os.getenv("ALLOWED_ORIGINS", "")
    origins = [item.strip() for item in raw_origins.split(",") if item.strip()]
    provider = AsyncOpenAIControllerDecisionProvider()
    authenticator = BearerTokenAuthenticator.from_environment()
    engine = create_engine()
    unit_of_work = PostgresCaseUnitOfWork(create_session_factory(engine))
    return create_async_case_app(
        decision_provider=provider,
        authenticator=authenticator,
        unit_of_work=unit_of_work,
        allowed_origins=origins or None,
        shutdown_callbacks=[engine.dispose],
    )


app = build_app()
