import unittest
from uuid import UUID

from httpx import ASGITransport, AsyncClient

from Agents.api.async_app import create_async_case_app
from Agents.api.auth import BearerTokenAuthenticator
from Agents.application.decisions import AskClarificationDecision
from Agents.application.scenario_models import ScenarioResult
from Agents.services.tool_hub import AuthorityToolResult, ToolHub


class TerminationController:
    async def decide(self, *, case_state, user_input):
        return AskClarificationDecision(
            question="请补充解除日期、月工资及书面解除通知。",
            required_fact_ids=[
                "termination.date",
                "employment.monthly_wage",
                "termination.written_notice",
            ],
        )


class ApiScenarioProvider:
    async def analyze(self, *, role_id, scene_id, case_state):
        return ScenarioResult(
            scene_id=scene_id,
            confidence=0.5,
            missing_fact_questions=["请补充劳动合同。"],
            summary="需要继续核对劳动合同。",
        )


class ApiAuthorityAdapter:
    async def search(self, request):
        return AuthorityToolResult(
            tool_name=request.tool_name,
            normalized_query=" ".join(request.query.split()),
            attempts=1,
            documents=(),
        )


class UnusedLegalProvider:
    async def analyze(self, *, context):
        raise AssertionError("legal provider is not used in these API tests")

    async def draft_document(self, *, context, document_type):
        raise AssertionError("legal provider is not used in these API tests")


class AsyncCaseApiTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.app = create_async_case_app(
            decision_provider=TerminationController(),
            scenario_provider=ApiScenarioProvider(),
            tool_hub=ToolHub(ApiAuthorityAdapter()),
            legal_provider=UnusedLegalProvider(),
            authenticator=BearerTokenAuthenticator(
                {
                    "worker-one-token": "worker-1",
                    "other-worker-token": "another-worker",
                }
            ),
        )
        self.client = AsyncClient(
            transport=ASGITransport(app=self.app),
            base_url="http://testserver",
        )
        self.headers = {"Authorization": "Bearer worker-one-token"}

    async def asyncTearDown(self):
        await self.app.state.runtime_registry.shutdown(timeout=2)
        await self.client.aclose()

    async def test_case_command_and_committed_event_history(self):
        create = await self.client.post(
            "/api/v1/cases",
            headers=self.headers,
            json={"role_id": "worker"},
        )
        self.assertEqual(create.status_code, 201)
        case_id = create.json()["case_id"]

        submit = await self.client.post(
            f"/api/v1/cases/{case_id}/commands",
            headers=self.headers,
            json={
                "idempotency_key": "browser-message-1",
                "expected_case_version": 0,
                "payload": {
                    "command_type": "submit_user_message",
                    "text": "公司以绩效不合格为由口头辞退我，没有给书面通知。",
                },
            },
        )
        self.assertEqual(submit.status_code, 202)
        runtime = await self.app.state.runtime_registry.get_or_create(UUID(case_id))
        await runtime.wait_idle()

        case = await self.client.get(
            f"/api/v1/cases/{case_id}",
            headers=self.headers,
        )
        history = await self.client.get(
            f"/api/v1/cases/{case_id}/events/history",
            headers=self.headers,
        )

        self.assertEqual(case.status_code, 200)
        self.assertEqual(case.json()["version"], 2)
        event_types = [item["event_type"] for item in history.json()["events"]]
        self.assertEqual(event_types[0], "command.accepted")
        self.assertEqual(event_types[-1], "operation.completed")
        self.assertIn("clarification.requested", event_types)
        resume = await self.client.get(
            f"/api/v1/cases/{case_id}/events/history",
            headers=self.headers,
            params={"after_sequence": history.json()["events"][-2]["sequence"]},
        )
        self.assertEqual(
            [item["event_type"] for item in resume.json()["events"]],
            ["operation.completed"],
        )

    async def test_case_owner_is_enforced(self):
        create = await self.client.post(
            "/api/v1/cases",
            headers=self.headers,
            json={"role_id": "worker"},
        )
        case_id = create.json()["case_id"]

        response = await self.client.get(
            f"/api/v1/cases/{case_id}",
            headers={"Authorization": "Bearer other-worker-token"},
        )

        self.assertEqual(response.status_code, 403)

    async def test_internal_agent_routing_is_not_a_user_command(self):
        create = await self.client.post(
            "/api/v1/cases",
            headers=self.headers,
            json={"role_id": "worker"},
        )
        case_id = create.json()["case_id"]

        response = await self.client.post(
            f"/api/v1/cases/{case_id}/commands",
            headers=self.headers,
            json={
                "idempotency_key": "invalid-internal-routing-command",
                "expected_case_version": 0,
                "payload": {
                    "command_type": "switch_agent",
                    "agent_name": "ScenarioAgent",
                },
            },
        )

        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()["code"], "invalid_command")

    async def test_evidence_upload_is_parsed_and_case_list_is_owner_scoped(self):
        create = await self.client.post(
            "/api/v1/cases",
            headers=self.headers,
            json={"role_id": "worker"},
        )
        case_id = create.json()["case_id"]
        upload = await self.client.post(
            f"/api/v1/cases/{case_id}/evidence",
            headers=self.headers,
            params={
                "idempotency_key": "contract-upload-1",
                "expected_case_version": 0,
            },
            files={
                "file": (
                    "../工资证明.txt",
                    "用人单位：示例公司\n月工资：12000元\n解除日期：2026-07-20".encode(),
                    "text/plain",
                )
            },
        )
        self.assertEqual(upload.status_code, 202)
        runtime = await self.app.state.runtime_registry.get_or_create(UUID(case_id))
        await runtime.wait_idle()
        duplicate = await self.client.post(
            f"/api/v1/cases/{case_id}/evidence",
            headers=self.headers,
            params={
                "idempotency_key": "contract-upload-1",
                "expected_case_version": 0,
            },
            files={
                "file": (
                    "工资证明.txt",
                    "月工资：12000元".encode(),
                    "text/plain",
                )
            },
        )

        current = await self.client.get(f"/api/v1/cases/{case_id}", headers=self.headers)
        cases = await self.client.get("/api/v1/cases", headers=self.headers)

        self.assertEqual(current.json()["evidence"][0]["status"], "parsed")
        self.assertTrue(duplicate.json()["idempotent_replay"])
        self.assertEqual(duplicate.json()["evidence_id"], upload.json()["evidence_id"])
        self.assertNotIn("..", current.json()["evidence"][0]["display_name"])
        fact_ids = {item["fact_id"] for item in current.json()["candidate_facts"]}
        self.assertIn("employment.monthly_wage", fact_ids)
        self.assertEqual([item["case_id"] for item in cases.json()["cases"]], [case_id])


if __name__ == "__main__":
    unittest.main()
