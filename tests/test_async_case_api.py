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


class AsyncCaseApiTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.app = create_async_case_app(
            decision_provider=TerminationController(),
            scenario_provider=ApiScenarioProvider(),
            tool_hub=ToolHub(ApiAuthorityAdapter()),
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
                    "command_type": "confirm_handoff",
                    "handoff_id": "2f5e17c4-16c4-488c-a337-fb043cfe6e18",
                    "approve": True,
                },
            },
        )

        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()["detail"]["code"], "invalid_command")


if __name__ == "__main__":
    unittest.main()
