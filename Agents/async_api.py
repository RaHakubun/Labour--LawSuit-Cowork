from __future__ import annotations

import os
from pathlib import Path

from Agents.api.async_app import create_async_case_app
from Agents.api.auth import BearerTokenAuthenticator
from Agents.infrastructure.database import create_engine, create_session_factory
from Agents.infrastructure.configurable_integrations import (
    DatabaseBackedAuthoritySearchAdapter,
    DatabaseBackedControllerDecisionProvider,
    DatabaseBackedLegalResultProvider,
    DatabaseBackedScenarioResultProvider,
    DatabaseBackedVisionOcrAdapter,
)
from Agents.infrastructure.evidence_storage import LocalEvidenceStorage
from Agents.infrastructure.integration_credentials import (
    PostgresIntegrationCredentialStore,
    load_or_create_credential_cipher,
)
from Agents.infrastructure.postgres_uow import PostgresCaseUnitOfWork
from Agents.scene_catalog import validate_template_catalog
from Agents.services.tool_hub import ToolHub


def build_app():
    raw_origins = os.getenv("ALLOWED_ORIGINS", "")
    origins = [item.strip() for item in raw_origins.split(",") if item.strip()]
    engine = create_engine()
    session_factory = create_session_factory(engine)
    integration_store = PostgresIntegrationCredentialStore(
        session_factory,
        load_or_create_credential_cipher(),
    )
    provider = DatabaseBackedControllerDecisionProvider(integration_store)
    scenario_provider = DatabaseBackedScenarioResultProvider(integration_store)
    legal_provider = DatabaseBackedLegalResultProvider(integration_store)
    validate_template_catalog(Path(__file__).resolve().parents[1])
    tool_hub = ToolHub(DatabaseBackedAuthoritySearchAdapter(integration_store))
    vision_ocr = DatabaseBackedVisionOcrAdapter(integration_store)
    authenticator = BearerTokenAuthenticator.from_environment()
    raw_admin_actors = os.getenv("INTEGRATION_ADMIN_ACTOR_IDS", "")
    integration_admin_actor_ids = frozenset(
        item.strip() for item in raw_admin_actors.split(",") if item.strip()
    ) or None
    unit_of_work = PostgresCaseUnitOfWork(session_factory)
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
        vision_ocr=vision_ocr,
        integration_store=integration_store,
        integration_admin_actor_ids=integration_admin_actor_ids,
    )


app = build_app()
