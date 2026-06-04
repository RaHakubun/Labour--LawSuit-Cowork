import tempfile
import unittest
from pathlib import Path

from Agents.agent import Agent
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


class SessionServiceTests(unittest.TestCase):
    def test_controller_askmore_yes(self):
        controller_llm = FakeLLM(
            ['{"askmore":"yes","ask":"请补充劳动合同签署时间、解除通知形式和工资基数。"}']
        )

        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp = Path(tmp_dir)
            controller_template = tmp / "ControllerAgent.md"
            legal_template = tmp / "LegalAnalysisAgent.md"
            scenario_root = tmp / "ScenarioAgents"
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
                legal_factory=lambda: LegalAnalysisAgent(main_prompt="", llm_callable=FakeLLM(['{"askmore":"no","analysis":"x","data":{"schema_version":"1.0","issues":[{"issue_id":"I1","title":"问题1","conclusion":"结论1","confidence":"C3","citation_ids":["C1"]}],"citations":[{"citation_id":"C1","kind":"law","law_name":"中华人民共和国劳动合同法","article":"第四十条","title":"中华人民共和国劳动合同法第四十条","quote":"条文摘录","source":{"tool_name":"检索法律法规-语义","query":"违法解除条款"}}]}}'])),
                controller_template_path=str(controller_template),
                legal_template_path=str(legal_template),
                scenario_template_root=scenario_root,
            )

            session = service.create_session("worker")
            turn = service.submit_turn(session.session_id, "我在拿到offer前被解雇了")

        self.assertEqual(turn.askmore, "yes")
        self.assertEqual(turn.active_agent, "ControllerAgent")
        self.assertEqual(len(turn.handoffs), 0)
        self.assertGreaterEqual(len(turn.messages), 2)

    def test_case_state_is_initialized_and_updated_by_controller_patch(self):
        controller_llm = FakeLLM(
            [
                (
                    '{"askmore":"no","user_input":"公司口头辞退我",'
                    '"analysis":{"scene_id":"termination_layoff","confidence_level":"C3",'
                    '"escalation_flags":"L1","current_status":"已被口头辞退",'
                    '"user_appeal":"主张违法解除赔偿","faced_problems":"缺少解除通知和工资基数",'
                    '"route_plan":"进入解除场景"}}'
                )
            ]
        )

        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp = Path(tmp_dir)
            controller_template = tmp / "ControllerAgent.md"
            legal_template = tmp / "LegalAnalysisAgent.md"
            scenario_root = tmp / "ScenarioAgents"
            scenario_root.mkdir(parents=True, exist_ok=True)
            (scenario_root / "termination_layoff.md").write_text(
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
                legal_factory=lambda: LegalAnalysisAgent(main_prompt="", llm_callable=FakeLLM(['{"askmore":"no","analysis":"x","data":{"schema_version":"1.0","issues":[{"issue_id":"I1","title":"问题1","conclusion":"结论1","confidence":"C3","citation_ids":["C1"]}],"citations":[{"citation_id":"C1","kind":"law","law_name":"中华人民共和国劳动合同法","article":"第四十条","title":"中华人民共和国劳动合同法第四十条","quote":"条文摘录","source":{"tool_name":"检索法律法规-语义","query":"违法解除条款"}}]}}'])),
                controller_template_path=str(controller_template),
                legal_template_path=str(legal_template),
                scenario_template_root=scenario_root,
            )

            session = service.create_session("worker")
            service.submit_turn(session.session_id, "公司口头辞退我")
            case_payload = service.get_case_state(session.session_id)

        self.assertEqual(case_payload["case_state"]["interaction"]["user_role"], "worker")
        self.assertEqual(case_payload["case_state"]["interaction"]["current_goal"], "主张违法解除赔偿")
        self.assertEqual(case_payload["case_state"]["interaction"]["stage"], "controller")
        self.assertGreaterEqual(case_payload["case_version"], 1)
        self.assertTrue(
            any(event["event_type"] == "patch_applied" for event in case_payload["events"])
        )

    def test_full_chain_controller_to_scenario_to_legal(self):
        controller_llm = FakeLLM(
            [
                (
                    '{"askmore":"no","user_input":"用户原文","analysis":{'
                    '"scene_id":"recruitment_probation",'
                    '"confidence_level":"C3",'
                    '"escalation_flags":"L1",'
                    '"current_status":"已被口头拒绝入职",'
                    '"user_appeal":"主张违约责任",'
                    '"faced_problems":"offer约束力与证据链不足",'
                    '"route_plan":"进入场景细化"}}'
                )
            ]
        )
        scenario_llm = FakeLLM(
            [
                (
                    '{"askmore":"no","user_input":"用户原文","analysis":{'
                    '"scene_id":"recruitment_probation",'
                    '"current_status":"已收到拒绝入职口头通知",'
                    '"user_appeal":"确认是否可索赔",'
                    '"faced_problems":"录用承诺与毁约证据不足",'
                    '"route_plan":"补证并检索法源"},'
                    '"tool":{"检索司法案例-语义":"offer毁约案例"}}'
                )
            ]
        )
        legal_llm = FakeLLM(
            [
                '{"askmore":"no","analysis":"# 法律分析\\n可考虑违约与缔约过失路径。","data":{"schema_version":"1.0","issues":[{"issue_id":"I1","title":"问题1","conclusion":"结论1","confidence":"C3","citation_ids":["C1"]}],"citations":[{"citation_id":"C1","kind":"law","law_name":"中华人民共和国劳动合同法","article":"第四十条","title":"中华人民共和国劳动合同法第四十条","quote":"条文摘录","source":{"tool_name":"检索法律法规-语义","query":"违法解除条款"}}]}}',
            ]
        )
        fake_mcp = FakeMCP()

        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp = Path(tmp_dir)
            controller_template = tmp / "ControllerAgent.md"
            legal_template = tmp / "LegalAnalysisAgent.md"
            scenario_root = tmp / "ScenarioAgents"
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
            )

            session = service.create_session("lawyer")
            turn = service.submit_turn(session.session_id, "我在拿到offer前被解雇了")
            self.assertTrue(turn.requires_handoff_confirmation)
            self.assertEqual(turn.pending_transition["to_agent"], "ScenarioAgent")
            scenario_turn = service.confirm_handoff(session.session_id, approve=True)
            self.assertTrue(scenario_turn.requires_handoff_confirmation)
            self.assertEqual(scenario_turn.pending_transition["to_agent"], "LegalAnalysisAgent")
            turn = service.confirm_handoff(session.session_id, approve=True)
            summary = service.get_session_summary(session.session_id)

        self.assertEqual(turn.askmore, "no")
        self.assertEqual(turn.active_agent, "LegalAnalysisAgent")
        self.assertEqual(len(turn.handoffs), 1)
        self.assertEqual(turn.handoffs[0].from_agent, "ScenarioAgent")
        self.assertEqual(summary["stage"], "legal")
        self.assertEqual(len(fake_mcp.calls), 1)

    def test_session_persistence_reload_and_continue(self):
        first_round_controller_llm = FakeLLM(
            ['{"askmore":"yes","ask":"请补充 offer 形式、沟通记录和拒绝入职时间。"}']
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

            service1 = MultiAgentSessionService(
                controller_factory=lambda: Agent(
                    main_prompt="", llm_callable=first_round_controller_llm
                ),
                scenario_factory=lambda: ScenarioAgent(main_prompt="", llm_callable=FakeLLM(['{"askmore":"yes","ask":"x"}'])),
                legal_factory=lambda: LegalAnalysisAgent(main_prompt="", llm_callable=FakeLLM(['{"askmore":"no","analysis":"x","data":{"schema_version":"1.0","issues":[{"issue_id":"I1","title":"问题1","conclusion":"结论1","confidence":"C3","citation_ids":["C1"]}],"citations":[{"citation_id":"C1","kind":"law","law_name":"中华人民共和国劳动合同法","article":"第四十条","title":"中华人民共和国劳动合同法第四十条","quote":"条文摘录","source":{"tool_name":"检索法律法规-语义","query":"违法解除条款"}}]}}'])),
                controller_template_path=str(controller_template),
                legal_template_path=str(legal_template),
                scenario_template_root=scenario_root,
                storage_root=storage_root,
            )
            session = service1.create_session("worker")
            first_turn = service1.submit_turn(session.session_id, "我被口头取消录用了")
            self.assertEqual(first_turn.askmore, "yes")

            second_round_controller_llm = FakeLLM(
                [
                    (
                        '{"askmore":"no","user_input":"我被口头取消录用了，补充了证据",'
                        '"analysis":{'
                        '"scene_id":"recruitment_probation",'
                        '"confidence_level":"C2",'
                        '"escalation_flags":"L1",'
                        '"current_status":"已被拒绝入职",'
                        '"user_appeal":"确认赔偿方案",'
                        '"faced_problems":"证据链完整性",'
                        '"route_plan":"进入场景分析"}}'
                    )
                ]
            )
            scenario_llm = FakeLLM(
                [
                    (
                        '{"askmore":"no","analysis":{'
                        '"scene_id":"recruitment_probation",'
                        '"current_status":"已补充沟通证据",'
                        '"user_appeal":"确认索赔路径",'
                        '"faced_problems":"口头解除证据效力",'
                        '"route_plan":"调取类案与法条"},'
                        '"tool":{"检索司法案例-语义":"实习期拒绝录用"} }'
                    )
                ]
            )
            legal_llm = FakeLLM(
                ['{"askmore":"no","analysis":"# 结论\\n可主张缔约过失责任与损失赔偿。","data":{"schema_version":"1.0","issues":[{"issue_id":"I1","title":"问题1","conclusion":"结论1","confidence":"C3","citation_ids":["C1"]}],"citations":[{"citation_id":"C1","kind":"law","law_name":"中华人民共和国劳动合同法","article":"第四十条","title":"中华人民共和国劳动合同法第四十条","quote":"条文摘录","source":{"tool_name":"检索法律法规-语义","query":"违法解除条款"}}]}}']
            )

            service2 = MultiAgentSessionService(
                controller_factory=lambda: Agent(
                    main_prompt="", llm_callable=second_round_controller_llm
                ),
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

            summary_before = service2.get_session_summary(session.session_id)
            self.assertEqual(summary_before["stage"], "controller")
            self.assertEqual(summary_before["turn_id"], 1)

            second_turn = service2.submit_turn(session.session_id, "我有录音和邮件截图")
            self.assertTrue(second_turn.requires_handoff_confirmation)
            scenario_turn = service2.confirm_handoff(session.session_id, approve=True)
            self.assertTrue(scenario_turn.requires_handoff_confirmation)
            second_turn = service2.confirm_handoff(session.session_id, approve=True)
            summary_after = service2.get_session_summary(session.session_id)

        self.assertEqual(second_turn.askmore, "no")
        self.assertEqual(second_turn.active_agent, "LegalAnalysisAgent")
        self.assertEqual(summary_after["stage"], "legal")
        self.assertGreaterEqual(summary_after["message_count"], 6)
        self.assertEqual(len(fake_mcp.calls), 1)

    def test_legal_stage_allows_followup_after_askmore_no(self):
        controller_llm = FakeLLM(
            ['{"askmore":"no","user_input":"用户原文","analysis":{"scene_id":"recruitment_probation","confidence_level":"C3","escalation_flags":"L1","current_status":"已被拒绝入职","user_appeal":"主张赔偿","faced_problems":"证据不足","route_plan":"场景细化"}}']
        )
        scenario_llm = FakeLLM(
            ['{"askmore":"no","analysis":{"scene_id":"recruitment_probation","current_status":"状态","user_appeal":"诉求","faced_problems":"问题","route_plan":"计划"},"tool":{"toolname1":"mcp_query(\\"检索司法案例-语义\\",\\"实习期毁约案例\\")"}}']
        )
        legal_llm = FakeLLM(
            [
                '{"askmore":"no","analysis":"# 第一版报告\\n可主张缔约过失。","data":{"schema_version":"1.0","issues":[{"issue_id":"I1","title":"问题1","conclusion":"结论1","confidence":"C3","citation_ids":["C1"]}],"citations":[{"citation_id":"C1","kind":"law","law_name":"中华人民共和国劳动合同法","article":"第四十条","title":"中华人民共和国劳动合同法第四十条","quote":"条文摘录","source":{"tool_name":"检索法律法规-语义","query":"违法解除条款"}}]}}',
                '{"askmore":"yes","query":"我继续补充解释：你可以主张合理信赖损失。"}',
            ]
        )
        fake_mcp = FakeMCP()

        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp = Path(tmp_dir)
            controller_template = tmp / "ControllerAgent.md"
            legal_template = tmp / "LegalAnalysisAgent.md"
            scenario_root = tmp / "ScenarioAgents"
            scenario_root.mkdir(parents=True, exist_ok=True)
            (scenario_root / "recruitment_probation.md").write_text(
                "user={user_input}\nctx={conversation_context}\natt={attachments_meta}",
                encoding="utf-8",
            )
            controller_template.write_text(
                "user={user_input}\nctx={conversation_context}\natt={attachments_meta}",
                encoding="utf-8",
            )
            legal_template.write_text("history={Tool_Call_History}", encoding="utf-8")

            service = MultiAgentSessionService(
                controller_factory=lambda: Agent(main_prompt="", llm_callable=controller_llm),
                scenario_factory=lambda: ScenarioAgent(main_prompt="", llm_callable=scenario_llm, mcp_query_callable=fake_mcp),
                legal_factory=lambda: LegalAnalysisAgent(main_prompt="", llm_callable=legal_llm, mcp_query_callable=fake_mcp),
                controller_template_path=str(controller_template),
                legal_template_path=str(legal_template),
                scenario_template_root=scenario_root,
                scenario_tool_retry_times=0,
            )

            session = service.create_session("worker")
            service.submit_turn(session.session_id, "我在拿到offer前被解雇了")
            service.confirm_handoff(session.session_id, approve=True)
            first_legal = service.confirm_handoff(session.session_id, approve=True)
            second_legal = service.submit_turn(session.session_id, "那我还能继续问吗？")
            summary = service.get_session_summary(session.session_id)

        self.assertEqual(first_legal.askmore, "no")
        self.assertEqual(second_legal.askmore, "yes")
        self.assertEqual(summary["stage"], "legal")

    def test_legal_stage_end_closes_session(self):
        controller_llm = FakeLLM(
            ['{"askmore":"no","user_input":"用户原文","analysis":{"scene_id":"recruitment_probation","confidence_level":"C3","escalation_flags":"L1","current_status":"已被拒绝入职","user_appeal":"主张赔偿","faced_problems":"证据不足","route_plan":"场景细化"}}']
        )
        scenario_llm = FakeLLM(
            ['{"askmore":"no","analysis":{"scene_id":"recruitment_probation","current_status":"状态","user_appeal":"诉求","faced_problems":"问题","route_plan":"计划"}}']
        )
        legal_llm = FakeLLM(
            [
                '{"askmore":"end","query":"本轮结束","analysis":"# 最终报告\\n建议仲裁。","data":{"schema_version":"1.0","issues":[{"issue_id":"I1","title":"问题1","conclusion":"结论1","confidence":"C3","citation_ids":["C1"]}],"citations":[{"citation_id":"C1","kind":"law","law_name":"中华人民共和国劳动合同法","article":"第四十条","title":"中华人民共和国劳动合同法第四十条","quote":"条文摘录","source":{"tool_name":"检索法律法规-语义","query":"违法解除条款"}}]}}',
                '{"askmore":"yes","query":"请补充证据细节","analysis":"继续追问。"}',
            ]
        )

        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp = Path(tmp_dir)
            controller_template = tmp / "ControllerAgent.md"
            legal_template = tmp / "LegalAnalysisAgent.md"
            scenario_root = tmp / "ScenarioAgents"
            scenario_root.mkdir(parents=True, exist_ok=True)
            (scenario_root / "recruitment_probation.md").write_text(
                "user={user_input}\nctx={conversation_context}\natt={attachments_meta}",
                encoding="utf-8",
            )
            controller_template.write_text(
                "user={user_input}\nctx={conversation_context}\natt={attachments_meta}",
                encoding="utf-8",
            )
            legal_template.write_text("history={Tool_Call_History}", encoding="utf-8")

            service = MultiAgentSessionService(
                controller_factory=lambda: Agent(main_prompt="", llm_callable=controller_llm),
                scenario_factory=lambda: ScenarioAgent(main_prompt="", llm_callable=scenario_llm),
                legal_factory=lambda: LegalAnalysisAgent(main_prompt="", llm_callable=legal_llm),
                controller_template_path=str(controller_template),
                legal_template_path=str(legal_template),
                scenario_template_root=scenario_root,
            )

            session = service.create_session("worker")
            service.submit_turn(session.session_id, "我在拿到offer前被解雇了")
            service.confirm_handoff(session.session_id, approve=True)
            legal_turn = service.confirm_handoff(session.session_id, approve=True)
            resumed_turn = service.submit_turn(session.session_id, "还能继续问吗")
            summary = service.get_session_summary(session.session_id)

        self.assertEqual(legal_turn.askmore, "no")
        self.assertEqual(resumed_turn.active_agent, "LegalAnalysisAgent")
        self.assertEqual(summary["stage"], "legal")

    def test_employer_direct_module_service(self):
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
                legal_factory=lambda: LegalAnalysisAgent(main_prompt="", llm_callable=FakeLLM(['{"askmore":"no","analysis":"x","data":{"schema_version":"1.0","issues":[{"issue_id":"I1","title":"问题1","conclusion":"结论1","confidence":"C3","citation_ids":["C1"]}],"citations":[{"citation_id":"C1","kind":"law","law_name":"中华人民共和国劳动合同法","article":"第四十条","title":"中华人民共和国劳动合同法第四十条","quote":"条文摘录","source":{"tool_name":"检索法律法规-语义","query":"违法解除条款"}}]}}'])),
                controller_template_path=str(controller_template),
                legal_template_path=str(legal_template),
                scenario_template_root=scenario_root,
                storage_root=storage_root,
            )
            session = service.create_session("employer")
            turn = service.submit_turn(
                session.session_id,
                "我想做解除场景的合规扫描",
                attachments_meta={"module_key": "compliance_scanner"},
            )
            summary = service.get_session_summary(session.session_id)

        self.assertEqual(turn.active_agent, "EmployerModuleAgent")
        self.assertEqual(turn.askmore, "no")
        self.assertEqual(summary["stage"], "done")
        self.assertGreaterEqual(len(turn.messages), 2)

    def test_reject_module_key_not_allowed_for_role(self):
        controller_llm = FakeLLM(
            ['{"askmore":"yes","ask":"请补充劳动合同签署时间、解除通知形式和工资基数。"}']
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
                legal_factory=lambda: LegalAnalysisAgent(main_prompt="", llm_callable=FakeLLM(['{"askmore":"no","analysis":"x","data":{"schema_version":"1.0","issues":[{"issue_id":"I1","title":"问题1","conclusion":"结论1","confidence":"C3","citation_ids":["C1"]}],"citations":[{"citation_id":"C1","kind":"law","law_name":"中华人民共和国劳动合同法","article":"第四十条","title":"中华人民共和国劳动合同法第四十条","quote":"条文摘录","source":{"tool_name":"检索法律法规-语义","query":"违法解除条款"}}]}}'])),
                controller_template_path=str(controller_template),
                legal_template_path=str(legal_template),
                scenario_template_root=scenario_root,
                storage_root=storage_root,
            )
            session = service.create_session("worker")
            with self.assertRaises(ValueError):
                service.submit_turn(
                    session.session_id,
                    "我想看企业合规模板",
                    attachments_meta={"module_key": "compliance_scanner"},
                )

    def test_worker_module_key_injects_scene_hints_to_controller(self):
        controller_llm = FakeLLM(
            ['{"askmore":"yes","ask":"请补充劳动合同签署时间、解除通知形式和工资基数。"}']
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
                legal_factory=lambda: LegalAnalysisAgent(main_prompt="", llm_callable=FakeLLM(['{"askmore":"no","analysis":"x","data":{"schema_version":"1.0","issues":[{"issue_id":"I1","title":"问题1","conclusion":"结论1","confidence":"C3","citation_ids":["C1"]}],"citations":[{"citation_id":"C1","kind":"law","law_name":"中华人民共和国劳动合同法","article":"第四十条","title":"中华人民共和国劳动合同法第四十条","quote":"条文摘录","source":{"tool_name":"检索法律法规-语义","query":"违法解除条款"}}]}}'])),
                controller_template_path=str(controller_template),
                legal_template_path=str(legal_template),
                scenario_template_root=scenario_root,
                storage_root=storage_root,
            )
            session = service.create_session("worker")
            service.submit_turn(
                session.session_id,
                "公司口头辞退我，没有给通知",
                attachments_meta={"module_key": "compensation_calculator"},
            )
            rendered_prompt = controller_llm.calls[0]["user_prompt"]

        self.assertIn("compensation_calculator", rendered_prompt)
        self.assertIn("module_scene_hints", rendered_prompt)

    def test_reject_handoff_keeps_stage_and_waits_next_round(self):
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
                ),
                (
                    '{"askmore":"no","user_input":"用户补充原文","analysis":{'
                    '"scene_id":"recruitment_probation",'
                    '"confidence_level":"C3",'
                    '"escalation_flags":"L1",'
                    '"current_status":"补充后仍需场景细化",'
                    '"user_appeal":"主张赔偿",'
                    '"faced_problems":"仍需确认细节",'
                    '"route_plan":"场景细化"}}'
                ),
            ]
        )

        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp = Path(tmp_dir)
            controller_template = tmp / "ControllerAgent.md"
            legal_template = tmp / "LegalAnalysisAgent.md"
            scenario_root = tmp / "ScenarioAgents"
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
                legal_factory=lambda: LegalAnalysisAgent(main_prompt="", llm_callable=FakeLLM(['{"askmore":"no","analysis":"x","data":{"schema_version":"1.0","issues":[{"issue_id":"I1","title":"问题1","conclusion":"结论1","confidence":"C3","citation_ids":["C1"]}],"citations":[{"citation_id":"C1","kind":"law","law_name":"中华人民共和国劳动合同法","article":"第四十条","title":"中华人民共和国劳动合同法第四十条","quote":"条文摘录","source":{"tool_name":"检索法律法规-语义","query":"违法解除条款"}}]}}'])),
                controller_template_path=str(controller_template),
                legal_template_path=str(legal_template),
                scenario_template_root=scenario_root,
            )

            session = service.create_session("worker")
            first_turn = service.submit_turn(session.session_id, "初始问题")
            self.assertTrue(first_turn.requires_handoff_confirmation)

            rejected = service.confirm_handoff(session.session_id, approve=False)
            self.assertEqual(rejected.active_agent, "ControllerAgent")
            self.assertEqual(service.get_session_summary(session.session_id)["stage"], "controller")

            next_turn = service.submit_turn(session.session_id, "补充信息")
            self.assertTrue(next_turn.requires_handoff_confirmation)
            self.assertIn(
                "用户拒绝了本次Agent的终止，也许是还有需要澄清的地方，这次的输入如下：",
                controller_llm.calls[1]["user_prompt"],
            )
            self.assertIn("补充信息", controller_llm.calls[1]["user_prompt"])

    def test_forced_route_after_three_asks_renders_handoff_consistently(self):
        controller_llm = FakeLLM(
            [
                (
                    '{"askmore":"yes","ask":"还想继续追问",'
                    '"user_input":"补充原文",'
                    '"analysis":{'
                    '"scene_id":"recruitment_probation",'
                    '"confidence_level":"C2",'
                    '"escalation_flags":"L1",'
                    '"current_status":"信息基本足够",'
                    '"user_appeal":"确认索赔路径",'
                    '"faced_problems":"仍有少量细节待核实",'
                    '"route_plan":"进入场景分析"}}'
                )
            ]
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
                legal_factory=lambda: LegalAnalysisAgent(main_prompt="", llm_callable=FakeLLM(['{"askmore":"no","analysis":"x","data":{"schema_version":"1.0","issues":[{"issue_id":"I1","title":"问题1","conclusion":"结论1","confidence":"C3","citation_ids":["C1"]}],"citations":[{"citation_id":"C1","kind":"law","law_name":"中华人民共和国劳动合同法","article":"第四十条","title":"中华人民共和国劳动合同法第四十条","quote":"条文摘录","source":{"tool_name":"检索法律法规-语义","query":"违法解除条款"}}]}}'])),
                controller_template_path=str(controller_template),
                legal_template_path=str(legal_template),
                scenario_template_root=scenario_root,
                storage_root=storage_root,
            )
            session = service.create_session("worker")
            state = service.get_session(session.session_id)
            state.controller_ask_count = 3
            turn = service.submit_turn(session.session_id, "我补充完了")

        self.assertEqual(turn.askmore, "no")
        self.assertTrue(turn.requires_handoff_confirmation)
        self.assertEqual(turn.pending_transition["to_agent"], "ScenarioAgent")
        agent_messages = [m for m in turn.messages if m.speaker_type == "agent"]
        self.assertEqual(agent_messages[0].display_blocks[0].title, "ControllerAgent 路由结果")


if __name__ == "__main__":
    unittest.main()
