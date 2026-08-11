from __future__ import annotations

import unittest
from uuid import UUID, uuid4

from httpx import ASGITransport, AsyncClient

from Agents.api.async_app import create_async_case_app
from Agents.api.auth import BearerTokenAuthenticator
from Agents.application.decisions import AskClarificationDecision
from Agents.application.scenario_models import ScenarioResult
from Agents.services.tool_hub import AuthorityToolResult, ToolHub


class ControllerProvider:
    async def decide(self, *, case_state, user_input):
        return AskClarificationDecision(
            decision_type="ask_clarification",
            question="请补充解除日期。",
            required_fact_ids=["termination.date"],
        )


class ScenarioProvider:
    async def analyze(self, *, role_id, scene_id, case_state):
        return ScenarioResult(scene_id=scene_id, confidence=1, summary="已核对。")


class AuthorityAdapter:
    async def search(self, request):
        return AuthorityToolResult(
            tool_name=request.tool_name,
            normalized_query=request.query,
            attempts=1,
            documents=(),
        )


class UnusedLegalProvider:
    async def analyze(self, *, context):
        raise AssertionError("legal provider is not used")

    async def draft_document(self, *, context, document_type):
        raise AssertionError("legal provider is not used")


class ApiWorkbenchContractTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.app = create_async_case_app(
            decision_provider=ControllerProvider(),
            scenario_provider=ScenarioProvider(),
            tool_hub=ToolHub(AuthorityAdapter()),
            legal_provider=UnusedLegalProvider(),
            authenticator=BearerTokenAuthenticator({"owner-token": "owner-1"}),
        )
        self.client = AsyncClient(
            transport=ASGITransport(app=self.app),
            base_url="http://testserver",
        )
        self.headers = {"Authorization": "Bearer owner-token"}

    async def asyncTearDown(self):
        await self.app.state.runtime_registry.shutdown(timeout=2)
        await self.client.aclose()

    async def test_case_roles_are_closed_and_visible_in_projection(self):
        for role_id in ("worker", "lawyer", "employer"):
            response = await self.client.post(
                "/api/v1/cases",
                headers=self.headers,
                json={"role_id": role_id},
            )
            self.assertEqual(response.status_code, 201)
            self.assertEqual(response.json()["role_id"], role_id)

        invalid = await self.client.post(
            "/api/v1/cases",
            headers=self.headers,
            json={"role_id": "judge"},
        )
        self.assertEqual(invalid.status_code, 422)
        self.assertEqual(invalid.json()["code"], "request_validation_failed")
        self.assertTrue(invalid.json()["fields"])

    async def test_api_errors_have_one_stable_public_shape(self):
        response = await self.client.get(
            f"/api/v1/cases/{uuid4()}",
            headers=self.headers,
        )

        self.assertEqual(response.status_code, 404)
        self.assertEqual(
            set(response.json()),
            {"code", "message", "retryable", "case_id", "command_id", "fields"},
        )
        self.assertEqual(response.json()["code"], "case_not_found")
        self.assertFalse(response.json()["retryable"])

        unauthenticated = await self.client.get("/api/v1/cases")
        self.assertEqual(unauthenticated.status_code, 401)
        self.assertEqual(
            unauthenticated.json()["code"],
            "authentication_required",
        )

        created = await self.client.post(
            "/api/v1/cases",
            headers=self.headers,
            json={"role_id": "worker"},
        )
        invalid_cursor = await self.client.get(
            f"/api/v1/cases/{created.json()['case_id']}/events",
            headers={**self.headers, "Last-Event-ID": "not-a-sequence"},
        )
        self.assertEqual(invalid_cursor.status_code, 400)
        self.assertEqual(invalid_cursor.json()["code"], "invalid_event_cursor")

    async def test_event_history_is_bounded_and_cursor_paginated(self):
        created = await self.client.post(
            "/api/v1/cases",
            headers=self.headers,
            json={"role_id": "worker"},
        )
        case_id = created.json()["case_id"]
        accepted = await self.client.post(
            f"/api/v1/cases/{case_id}/commands",
            headers=self.headers,
            json={
                "idempotency_key": "pagination-message-1",
                "expected_case_version": 0,
                "payload": {
                    "command_type": "submit_user_message",
                    "text": "公司口头通知解除劳动合同。",
                },
            },
        )
        runtime = await self.app.state.runtime_registry.get_or_create(UUID(case_id))
        await runtime.wait_idle()
        self.assertEqual(accepted.status_code, 202)

        first = await self.client.get(
            f"/api/v1/cases/{case_id}/events/history",
            headers=self.headers,
            params={"limit": 2},
        )
        first_body = first.json()
        self.assertEqual(len(first_body["events"]), 2)
        self.assertEqual(
            first_body["next_after_sequence"],
            first_body["events"][-1]["sequence"],
        )

        second = await self.client.get(
            f"/api/v1/cases/{case_id}/events/history",
            headers=self.headers,
            params={
                "after_sequence": first_body["next_after_sequence"],
                "limit": 100,
            },
        )
        second_body = second.json()
        self.assertTrue(second_body["events"])
        self.assertIsNone(second_body["next_after_sequence"])
        self.assertGreater(
            second_body["events"][0]["sequence"],
            first_body["events"][-1]["sequence"],
        )


if __name__ == "__main__":
    unittest.main()
