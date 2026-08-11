from __future__ import annotations

import os
from pathlib import Path
import stat
import tempfile
import unittest

from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

from Agents.api.async_app import create_async_case_app
from Agents.api.auth import BearerTokenAuthenticator
from Agents.application.decisions import AskClarificationDecision
from Agents.application.scenario_models import ScenarioResult
from Agents.domain.case_state import CaseAggregate
from Agents.domain.errors import IntegrationNotConfiguredError
from Agents.infrastructure.configurable_integrations import (
    DatabaseBackedControllerDecisionProvider,
)
from Agents.infrastructure.database import create_engine, create_session_factory
from Agents.infrastructure.integration_credentials import (
    CredentialCipher,
    IntegrationCredentialInput,
    PostgresIntegrationCredentialStore,
    load_or_create_credential_cipher,
)
from Agents.services.tool_hub import AuthorityToolResult, ToolHub


class _ControllerProvider:
    async def decide(self, *, case_state, user_input):
        return AskClarificationDecision(
            decision_type="ask_clarification",
            question="请补充解除日期。",
            required_fact_ids=["termination.date"],
        )


class _ScenarioProvider:
    async def analyze(self, *, role_id, scene_id, case_state):
        return ScenarioResult(
            scene_id=scene_id,
            confidence=1,
            missing_fact_questions=["请补充解除日期。"],
            summary="待补信息。",
        )


class _AuthorityAdapter:
    async def search(self, request):
        return AuthorityToolResult(
            tool_name=request.tool_name,
            normalized_query=request.query,
            attempts=1,
            documents=(),
        )


class _LegalProvider:
    async def analyze(self, *, context):
        raise AssertionError("legal provider is not used")


class CredentialMasterKeyTests(unittest.TestCase):
    def test_master_key_is_created_with_owner_only_permissions(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "integration-master-key"
            cipher = load_or_create_credential_cipher(path)
            encrypted, nonce = cipher.encrypt("llm", "secret")

            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
            self.assertEqual(cipher.decrypt("llm", encrypted, nonce), "secret")

    def test_invalid_master_key_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "integration-master-key"
            path.write_text("not-valid-base64!", encoding="utf-8")

            with self.assertRaisesRegex(RuntimeError, "master key file is invalid"):
                load_or_create_credential_cipher(path)

    async def draft_document(self, *, context, document_type):
        raise AssertionError("legal provider is not used")


@unittest.skipUnless(
    os.getenv("TEST_DATABASE_URL"),
    "TEST_DATABASE_URL is required for encrypted credential integration tests",
)
class PostgresIntegrationCredentialStoreTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.engine = create_engine(os.environ["TEST_DATABASE_URL"])
        self.session_factory = create_session_factory(self.engine)
        self.store = PostgresIntegrationCredentialStore(
            self.session_factory,
            CredentialCipher(bytes(range(32))),
        )
        await self.store.delete("llm")

    async def asyncTearDown(self) -> None:
        await self.store.delete("llm")
        await self.engine.dispose()

    async def test_secret_is_encrypted_at_rest_and_never_returned_in_status(self) -> None:
        original_secret = "controlled-staging-secret"
        await self.store.upsert(
            "llm",
            IntegrationCredentialInput(
                endpoint="https://llm.example.test/v1",
                model="legal-model-v1",
                secret=original_secret,
            ),
        )

        resolved = await self.store.require("llm")
        statuses = await self.store.list_statuses()
        async with self.session_factory() as session:
            row = (
                await session.execute(
                    text(
                        "SELECT encrypted_secret, nonce "
                        "FROM integration_credentials WHERE provider = 'llm'"
                    )
                )
            ).one()

        self.assertEqual(resolved.secret, original_secret)
        self.assertEqual(resolved.endpoint, "https://llm.example.test/v1")
        self.assertNotIn(original_secret.encode(), bytes(row.encrypted_secret))
        self.assertNotIn(original_secret.encode(), bytes(row.nonce))
        self.assertEqual(len(statuses), 3)
        llm_status = next(item for item in statuses if item.provider == "llm")
        self.assertTrue(llm_status.configured)
        self.assertEqual(llm_status.endpoint, "https://llm.example.test/v1")
        self.assertEqual(llm_status.model, "legal-model-v1")
        self.assertNotIn("secret", llm_status.model_dump())

    async def test_upsert_replaces_secret_atomically_and_delete_removes_it(self) -> None:
        await self.store.upsert(
            "llm",
            IntegrationCredentialInput(
                endpoint="https://first.example.test/v1",
                model="first-model",
                secret="first-secret",
            ),
        )
        await self.store.upsert(
            "llm",
            IntegrationCredentialInput(
                endpoint="https://second.example.test/v1",
                model="second-model",
                secret="second-secret",
            ),
        )

        resolved = await self.store.require("llm")
        deleted = await self.store.delete("llm")

        self.assertEqual(resolved.secret, "second-secret")
        self.assertEqual(resolved.endpoint, "https://second.example.test/v1")
        self.assertTrue(deleted)
        with self.assertRaisesRegex(RuntimeError, "llm integration is not configured"):
            await self.store.require("llm")


@unittest.skipUnless(
    os.getenv("TEST_DATABASE_URL"),
    "TEST_DATABASE_URL is required for integration settings API tests",
)
class IntegrationSettingsApiTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.engine = create_engine(os.environ["TEST_DATABASE_URL"])
        self.store = PostgresIntegrationCredentialStore(
            create_session_factory(self.engine),
            CredentialCipher(bytes(reversed(range(32)))),
        )
        for provider in ("llm", "ocr", "mcp"):
            await self.store.delete(provider)
        self.app = create_async_case_app(
            decision_provider=_ControllerProvider(),
            scenario_provider=_ScenarioProvider(),
            tool_hub=ToolHub(_AuthorityAdapter()),
            legal_provider=_LegalProvider(),
            authenticator=BearerTokenAuthenticator(
                {
                    "settings-token": "owner-1",
                    "ordinary-token": "owner-2",
                }
            ),
            integration_store=self.store,
            integration_admin_actor_ids=frozenset({"owner-1"}),
        )
        self.client = AsyncClient(
            transport=ASGITransport(app=self.app),
            base_url="http://testserver",
        )
        self.headers = {"Authorization": "Bearer settings-token"}

    async def asyncTearDown(self) -> None:
        await self.app.state.runtime_registry.shutdown(timeout=2)
        await self.client.aclose()
        for provider in ("llm", "ocr", "mcp"):
            await self.store.delete(provider)
        await self.engine.dispose()

    async def test_authenticated_settings_crud_never_returns_secret(self) -> None:
        unauthenticated = await self.client.get("/api/v1/integrations")
        self.assertEqual(unauthenticated.status_code, 401)

        initial = await self.client.get(
            "/api/v1/integrations",
            headers=self.headers,
        )
        self.assertEqual(initial.status_code, 200)
        self.assertEqual(
            {item["provider"] for item in initial.json()["integrations"]},
            {"llm", "ocr", "mcp"},
        )
        self.assertFalse(any(item["configured"] for item in initial.json()["integrations"]))

        saved = await self.client.put(
            "/api/v1/integrations/llm",
            headers=self.headers,
            json={
                "endpoint": "https://llm.example.test/v1",
                "model": "legal-model-v2",
                "secret": "write-only-secret",
            },
        )
        self.assertEqual(saved.status_code, 200)
        self.assertTrue(saved.json()["configured"])
        self.assertNotIn("secret", saved.json())
        listed_text = (await self.client.get("/api/v1/integrations", headers=self.headers)).text
        self.assertNotIn("write-only-secret", listed_text)
        self.assertNotIn("secret", listed_text)

        deleted = await self.client.delete(
            "/api/v1/integrations/llm",
            headers=self.headers,
        )
        self.assertEqual(deleted.status_code, 204)
        with self.assertRaises(IntegrationNotConfiguredError):
            await self.store.require("llm")

    async def test_validation_error_never_echoes_rejected_secret(self) -> None:
        rejected_secret = "x" * 10001
        response = await self.client.put(
            "/api/v1/integrations/llm",
            headers=self.headers,
            json={
                "endpoint": "https://llm.example.test/v1",
                "model": "legal-model",
                "secret": rejected_secret,
            },
        )

        self.assertEqual(response.status_code, 422)
        self.assertNotIn(rejected_secret, response.text)
        self.assertNotIn("input", response.text)

    async def test_metadata_can_change_without_returning_or_replacing_secret(self) -> None:
        await self.store.upsert(
            "llm",
            IntegrationCredentialInput(
                endpoint="https://first.example.test/v1",
                model="first-model",
                secret="preserved-secret",
            ),
        )

        response = await self.client.put(
            "/api/v1/integrations/llm",
            headers=self.headers,
            json={
                "endpoint": "https://second.example.test/v1",
                "model": "second-model",
                "secret": None,
            },
        )

        self.assertEqual(response.status_code, 200)
        resolved = await self.store.require("llm")
        self.assertEqual(resolved.endpoint, "https://second.example.test/v1")
        self.assertEqual(resolved.model, "second-model")
        self.assertEqual(resolved.secret, "preserved-secret")

    async def test_provider_specific_validation_is_explicit(self) -> None:
        response = await self.client.put(
            "/api/v1/integrations/mcp",
            headers=self.headers,
            json={
                "endpoint": "https://must-not-be-accepted.example.test",
                "model": None,
                "secret": "mcp-token",
            },
        )

        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()["code"], "invalid_integration_configuration")

    async def test_blank_configuration_is_rejected_for_every_provider(self) -> None:
        payloads = {
            "llm": {
                "endpoint": "https://llm.example.test/v1",
                "model": "   ",
                "secret": "   ",
            },
            "ocr": {
                "endpoint": "https://ocr.example.test/v1",
                "model": "   ",
                "secret": "   ",
            },
            "mcp": {"endpoint": None, "model": None, "secret": "   "},
        }
        for provider, payload in payloads.items():
            with self.subTest(provider=provider):
                response = await self.client.put(
                    f"/api/v1/integrations/{provider}",
                    headers=self.headers,
                    json=payload,
                )
                self.assertEqual(response.status_code, 422)
                self.assertEqual(
                    response.json()["code"],
                    "invalid_integration_configuration",
                )
        statuses = await self.store.list_statuses()
        self.assertFalse(any(item.configured for item in statuses))

    async def test_non_admin_actor_cannot_change_installation_credentials(self) -> None:
        headers = {"Authorization": "Bearer ordinary-token"}
        response = await self.client.put(
            "/api/v1/integrations/mcp",
            headers=headers,
            json={"endpoint": None, "model": None, "secret": "attacker-token"},
        )
        deleted = await self.client.delete(
            "/api/v1/integrations/mcp",
            headers=headers,
        )

        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["code"], "integration_admin_required")
        self.assertEqual(deleted.status_code, 403)
        with self.assertRaises(IntegrationNotConfiguredError):
            await self.store.require("mcp")

    async def test_database_backed_provider_fails_without_business_fallback(self) -> None:
        provider = DatabaseBackedControllerDecisionProvider(self.store)
        aggregate = CaseAggregate.create(owner_id="owner-1", role_id="worker")

        with self.assertRaises(IntegrationNotConfiguredError) as raised:
            await provider.decide(
                case_state=aggregate.state,
                user_input="公司口头辞退我",
            )

        self.assertEqual(raised.exception.code, "integration_not_configured")
        self.assertFalse(raised.exception.retryable)


if __name__ == "__main__":
    unittest.main()
