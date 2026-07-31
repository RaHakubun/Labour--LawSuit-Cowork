from __future__ import annotations

import os
from pathlib import Path

from Agents.api.async_app import create_async_case_app
from Agents.api.auth import BearerTokenAuthenticator
from Agents.infrastructure.database import create_engine, create_session_factory
from Agents.infrastructure.llm_adapter import AsyncOpenAIControllerDecisionProvider
from Agents.infrastructure.llm_adapter import AsyncOpenAIScenarioResultProvider
from Agents.infrastructure.llm_adapter import AsyncOpenAILegalResultProvider
from Agents.infrastructure.evidence_storage import LocalEvidenceStorage
from Agents.infrastructure.mcp_adapter import PkulawAuthoritySearchAdapter
from Agents.infrastructure.postgres_uow import PostgresCaseUnitOfWork
from Agents.scene_catalog import validate_template_catalog
from Agents.services.tool_hub import ToolHub


def build_app():
    raw_origins = os.getenv("ALLOWED_ORIGINS", "")
    origins = [item.strip() for item in raw_origins.split(",") if item.strip()]
    provider = AsyncOpenAIControllerDecisionProvider()
    scenario_provider = AsyncOpenAIScenarioResultProvider()
    legal_provider = AsyncOpenAILegalResultProvider()
    validate_template_catalog(Path(__file__).resolve().parents[1])
    tool_hub = ToolHub(PkulawAuthoritySearchAdapter())
    authenticator = BearerTokenAuthenticator.from_environment()
    engine = create_engine()
    unit_of_work = PostgresCaseUnitOfWork(create_session_factory(engine))
    evidence_root = Path(
        os.getenv(
            "EVIDENCE_STORAGE_ROOT",
            str(Path(__file__).resolve().parents[1] / "storage" / "evidence"),
        )
    )
    return create_async_case_app(
        decision_provider=provider,
        scenario_provider=scenario_provider,
        tool_hub=tool_hub,
        legal_provider=legal_provider,
        evidence_storage=LocalEvidenceStorage(evidence_root),
        authenticator=authenticator,
        unit_of_work=unit_of_work,
        allowed_origins=origins or None,
        shutdown_callbacks=[engine.dispose],
    )


app = build_app()
