import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from Agents.agent import Agent
from Agents.api_server import create_app
from Agents.legal_analysis_agent import LegalAnalysisAgent
from Agents.scenario_agent import ScenarioAgent
from Agents.session_service import MultiAgentSessionService


class FakeLLM:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def __call__(self, **kwargs):
        self.calls.append(kwargs)
        return self.responses[len(self.calls) - 1]


class FakeMCP:
    def __init__(self):
        self.calls = []

    def __call__(self, service_name, query):
        self.calls.append((service_name, query))
        return f"MCP 服务名: {service_name}\n查询内容: {query}\n\n结果是:\n命中结果"


class ApiServerTests(unittest.TestCase):
    def test_session_lifecycle_with_controller_askmore(self):
        controller_llm = FakeLLM(
            ['{"askmore":"yes","ask":"请补充合同签署日期、解除时间和工资。"}']
        )

        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp = Path(tmp_dir)
            controller_template = tmp / "ControllerAgent.md"
            legal_template = tmp / "LegalAnalysisAgent.md"
            scenario_root = tmp / "ScenarioAgents"
            storage_root = tmp / "session_storage"
            scenario_root.mkdir(parents=True, exist_ok=True)
            (scenario_root / "recruitment_probation.md").write_text(
                "user={user_input}\nctx={conversation_context}\natt={attachments_meta}",
                encoding="utf-8",
            )
            controller_template.write_text(
                "user={user_input}\nctx={conversation_context}\natt={attachments_meta}",
                encoding="utf-8",
            )
            legal_template.write_text(
                "scene={Scenario_Agent_Input}\nhistory={Tool_Call_History}",
                encoding="utf-8",
            )

            service = MultiAgentSessionService(
                controller_factory=lambda: Agent(main_prompt="", llm_callable=controller_llm),
                scenario_factory=lambda: ScenarioAgent(main_prompt="", llm_callable=FakeLLM(['{"askmore":"yes","ask":"x"}'])),
                legal_factory=lambda: LegalAnalysisAgent(main_prompt="", llm_callable=FakeLLM(['{"askmore":"no","analysis":"x"}'])),
                controller_template_path=str(controller_template),
                legal_template_path=str(legal_template),
                scenario_template_root=scenario_root,
                storage_root=storage_root,
            )
            app = create_app(service=service)
            client = TestClient(app)

            create_resp = client.post("/api/v1/sessions", json={"role_id": "worker"})
            self.assertEqual(create_resp.status_code, 200)
            session_id = create_resp.json()["session_id"]

            turn_resp = client.post(
                f"/api/v1/sessions/{session_id}/turns",
                json={"user_input": "公司口头辞退我，没有给通知"},
            )
            self.assertEqual(turn_resp.status_code, 200)
            payload = turn_resp.json()
            self.assertEqual(payload["askmore"], "yes")
            self.assertEqual(payload["active_agent"], "ControllerAgent")
            self.assertGreaterEqual(len(payload["messages"]), 2)

            summary_resp = client.get(f"/api/v1/sessions/{session_id}")
            self.assertEqual(summary_resp.status_code, 200)
            summary = summary_resp.json()
            self.assertEqual(summary["stage"], "controller")
            self.assertEqual(summary["turn_id"], 1)

            msg_resp = client.get(f"/api/v1/sessions/{session_id}/messages")
            self.assertEqual(msg_resp.status_code, 200)
            self.assertGreaterEqual(len(msg_resp.json()["messages"]), 2)

    def test_full_chain_via_http(self):
        controller_llm = FakeLLM(
            [
                (
                    '{"askmore":"no","user_input":"用户原文","analysis":{'
                    '"scene_id":"recruitment_probation",'
                    '"confidence_level":"C3",'
                    '"escalation_flags":"L1",'
                    '"current_status":"已口头拒绝入职",'
                    '"user_appeal":"主张赔偿",'
                    '"faced_problems":"证据链不足",'
                    '"route_plan":"场景细化"}}'
                )
            ]
        )
        scenario_llm = FakeLLM(
            [
                (
                    '{"askmore":"no","analysis":{'
                    '"scene_id":"recruitment_probation",'
                    '"current_status":"已补充录音截图",'
                    '"user_appeal":"确认索赔路径",'
                    '"faced_problems":"要件证据匹配",'
                    '"route_plan":"检索案例与法条"},'
                    '"tool":{"检索司法案例-语义":"实习期被拒绝入职案例"}}'
                )
            ]
        )
        legal_llm = FakeLLM(
            ['{"askmore":"no","analysis":"# 结论\\n可主张缔约过失及合理损失。"}']
        )
        fake_mcp = FakeMCP()

        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp = Path(tmp_dir)
            controller_template = tmp / "ControllerAgent.md"
            legal_template = tmp / "LegalAnalysisAgent.md"
            scenario_root = tmp / "ScenarioAgents"
            storage_root = tmp / "session_storage"
            scenario_root.mkdir(parents=True, exist_ok=True)
            (scenario_root / "recruitment_probation.md").write_text(
                "user={user_input}\nctx={conversation_context}\natt={attachments_meta}",
                encoding="utf-8",
            )
            controller_template.write_text(
                "user={user_input}\nctx={conversation_context}\natt={attachments_meta}",
                encoding="utf-8",
            )
            legal_template.write_text(
                "scene={Scenario_Agent_Input}\nhistory={Tool_Call_History}",
                encoding="utf-8",
            )

            service = MultiAgentSessionService(
                controller_factory=lambda: Agent(main_prompt="", llm_callable=controller_llm),
                scenario_factory=lambda: ScenarioAgent(
                    main_prompt="",
                    llm_callable=scenario_llm,
                    mcp_query_callable=fake_mcp,
                ),
                legal_factory=lambda: LegalAnalysisAgent(
                    main_prompt="",
                    llm_callable=legal_llm,
                    mcp_query_callable=fake_mcp,
                ),
                controller_template_path=str(controller_template),
                legal_template_path=str(legal_template),
                scenario_template_root=scenario_root,
                scenario_tool_retry_times=0,
                storage_root=storage_root,
            )
            app = create_app(service=service)
            client = TestClient(app)

            create_resp = client.post("/api/v1/sessions", json={"role_id": "lawyer"})
            self.assertEqual(create_resp.status_code, 200)
            session_id = create_resp.json()["session_id"]

            turn_resp = client.post(
                f"/api/v1/sessions/{session_id}/turns",
                json={"user_input": "我在拿到 offer 前被解雇了"},
            )
            self.assertEqual(turn_resp.status_code, 200)
            payload = turn_resp.json()
            self.assertEqual(payload["askmore"], "no")
            self.assertEqual(payload["active_agent"], "ControllerAgent")
            self.assertTrue(payload["requires_handoff_confirmation"])
            self.assertEqual(payload["pending_transition"]["to_agent"], "ScenarioAgent")

            confirm_to_scenario = client.post(
                f"/api/v1/sessions/{session_id}/handoff/confirm",
                json={"approve": True},
            )
            self.assertEqual(confirm_to_scenario.status_code, 200)
            payload2 = confirm_to_scenario.json()
            self.assertEqual(payload2["active_agent"], "ScenarioAgent")
            self.assertTrue(payload2["requires_handoff_confirmation"])
            self.assertEqual(payload2["pending_transition"]["to_agent"], "LegalAnalysisAgent")

            confirm_to_legal = client.post(
                f"/api/v1/sessions/{session_id}/handoff/confirm",
                json={"approve": True},
            )
            self.assertEqual(confirm_to_legal.status_code, 200)
            payload3 = confirm_to_legal.json()
            self.assertEqual(payload3["active_agent"], "LegalAnalysisAgent")
            self.assertFalse(payload3["requires_handoff_confirmation"])
            self.assertEqual(len(fake_mcp.calls), 1)

            summary_resp = client.get(f"/api/v1/sessions/{session_id}")
            self.assertEqual(summary_resp.status_code, 200)
            self.assertEqual(summary_resp.json()["stage"], "done")

    def test_404_when_session_not_found(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            app = create_app(
                service=MultiAgentSessionService(storage_root=Path(tmp_dir) / "session_storage")
            )
            client = TestClient(app)

            summary_resp = client.get("/api/v1/sessions/notfound")
            self.assertEqual(summary_resp.status_code, 404)

            messages_resp = client.get("/api/v1/sessions/notfound/messages")
            self.assertEqual(messages_resp.status_code, 404)

            turn_resp = client.post(
                "/api/v1/sessions/notfound/turns",
                json={"user_input": "test"},
            )
            self.assertEqual(turn_resp.status_code, 404)

    def test_employer_direct_module_via_http(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp = Path(tmp_dir)
            controller_template = tmp / "ControllerAgent.md"
            legal_template = tmp / "LegalAnalysisAgent.md"
            scenario_root = tmp / "ScenarioAgents"
            storage_root = tmp / "session_storage"
            scenario_root.mkdir(parents=True, exist_ok=True)
            (scenario_root / "recruitment_probation.md").write_text(
                "user={user_input}\nctx={conversation_context}\natt={attachments_meta}",
                encoding="utf-8",
            )
            controller_template.write_text(
                "user={user_input}\nctx={conversation_context}\natt={attachments_meta}",
                encoding="utf-8",
            )
            legal_template.write_text(
                "scene={Scenario_Agent_Input}\nhistory={Tool_Call_History}",
                encoding="utf-8",
            )

            service = MultiAgentSessionService(
                controller_factory=lambda: Agent(main_prompt="", llm_callable=FakeLLM(['{"askmore":"yes","ask":"x"}'])),
                scenario_factory=lambda: ScenarioAgent(main_prompt="", llm_callable=FakeLLM(['{"askmore":"yes","ask":"x"}'])),
                legal_factory=lambda: LegalAnalysisAgent(main_prompt="", llm_callable=FakeLLM(['{"askmore":"no","analysis":"x"}'])),
                controller_template_path=str(controller_template),
                legal_template_path=str(legal_template),
                scenario_template_root=scenario_root,
                storage_root=storage_root,
            )
            app = create_app(service=service)
            client = TestClient(app)

            create_resp = client.post("/api/v1/sessions", json={"role_id": "employer"})
            self.assertEqual(create_resp.status_code, 200)
            session_id = create_resp.json()["session_id"]

            turn_resp = client.post(
                f"/api/v1/sessions/{session_id}/turns",
                json={
                    "user_input": "帮我做一次绩效场景的合规扫描",
                    "attachments_meta": {"module_key": "compliance_scanner"},
                },
            )
            self.assertEqual(turn_resp.status_code, 200)
            payload = turn_resp.json()
            self.assertEqual(payload["active_agent"], "EmployerModuleAgent")
            self.assertEqual(payload["askmore"], "no")
            self.assertGreaterEqual(len(payload["messages"]), 2)

    def test_module_role_mismatch_returns_400(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp = Path(tmp_dir)
            controller_template = tmp / "ControllerAgent.md"
            legal_template = tmp / "LegalAnalysisAgent.md"
            scenario_root = tmp / "ScenarioAgents"
            storage_root = tmp / "session_storage"
            scenario_root.mkdir(parents=True, exist_ok=True)
            (scenario_root / "recruitment_probation.md").write_text(
                "user={user_input}\nctx={conversation_context}\natt={attachments_meta}",
                encoding="utf-8",
            )
            controller_template.write_text(
                "user={user_input}\nctx={conversation_context}\natt={attachments_meta}",
                encoding="utf-8",
            )
            legal_template.write_text(
                "scene={Scenario_Agent_Input}\nhistory={Tool_Call_History}",
                encoding="utf-8",
            )

            service = MultiAgentSessionService(
                controller_factory=lambda: Agent(main_prompt="", llm_callable=FakeLLM(['{"askmore":"yes","ask":"x"}'])),
                scenario_factory=lambda: ScenarioAgent(main_prompt="", llm_callable=FakeLLM(['{"askmore":"yes","ask":"x"}'])),
                legal_factory=lambda: LegalAnalysisAgent(main_prompt="", llm_callable=FakeLLM(['{"askmore":"no","analysis":"x"}'])),
                controller_template_path=str(controller_template),
                legal_template_path=str(legal_template),
                scenario_template_root=scenario_root,
                storage_root=storage_root,
            )
            app = create_app(service=service)
            client = TestClient(app)
            create_resp = client.post("/api/v1/sessions", json={"role_id": "worker"})
            session_id = create_resp.json()["session_id"]

            turn_resp = client.post(
                f"/api/v1/sessions/{session_id}/turns",
                json={
                    "user_input": "我要企业沟通话术",
                    "attachments_meta": {"module_key": "communication_guide"},
                },
            )
            self.assertEqual(turn_resp.status_code, 400)
            self.assertIn("not allowed", turn_resp.json()["detail"])

    def test_list_sessions_filters_by_role(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            service = MultiAgentSessionService(storage_root=Path(tmp_dir) / "session_storage")
            worker = service.create_session("worker")
            service.create_session("employer")
            app = create_app(service=service)
            client = TestClient(app)

            resp = client.get("/api/v1/sessions", params={"role_id": "worker"})
            self.assertEqual(resp.status_code, 200)
            payload = resp.json()
            self.assertEqual(len(payload["sessions"]), 1)
            self.assertEqual(payload["sessions"][0]["session_id"], worker.session_id)
            self.assertEqual(payload["sessions"][0]["role_id"], "worker")


if __name__ == "__main__":
    unittest.main()
