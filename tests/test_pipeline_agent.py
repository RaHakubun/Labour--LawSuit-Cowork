import io
import tempfile
import unittest
from pathlib import Path

from Agents.agent import Agent
from Agents.legal_analysis_agent import LegalAnalysisAgent
from Agents.pipeline_agent import ControllerScenarioLegalPipeline
from Agents.scenario_agent import ScenarioAgent


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


class PipelineAgentTests(unittest.TestCase):
    def test_pipeline_connects_controller_scenario_legal(self):
        controller_llm = FakeLLM(
            [
                '{"askmore":"no","user_input":"用户原文","analysis":{"scene_id":"recruitment_probation"}}'
            ]
        )
        scenario_llm = FakeLLM(
            [
                '{"askmore":"no","user_input":"场景输入","analysis":{"scene_id":"recruitment_probation"},"tool":{"检索司法案例-语义":"试用期解除案例","检索法律法规-语义":"试用期解除法条"}}'
            ]
        )
        legal_llm = FakeLLM(
            [
                '{"askmore":"no","analysis":"最终法律分析"}',
            ]
        )
        scenario_mcp = FakeMCP()
        legal_mcp = FakeMCP()

        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            controller_template = tmp_path / "controller.md"
            legal_template = tmp_path / "legal.md"
            scenario_root = tmp_path / "Prompt_Template" / "ScenarioAgents"
            scenario_root.mkdir(parents=True, exist_ok=True)
            scenario_template = scenario_root / "recruitment_probation.md"

            controller_template.write_text(
                "user={user_input}\nctx={conversation_context}\natt={attachments_meta}",
                encoding="utf-8",
            )
            legal_template.write_text(
                "scene={Scenario_Agent_Input}\nhistory={Tool_Call_History}",
                encoding="utf-8",
            )
            scenario_template.write_text(
                "user={user_input}\nctx={conversation_context}\natt={attachments_meta}",
                encoding="utf-8",
            )

            from Agents import pipeline_agent as pipeline_module
            from Agents import scenario_agent as scenario_module

            old_pipeline_root = pipeline_module.SCENARIO_TEMPLATE_ROOT
            old_scenario_root = scenario_module.SCENARIO_TEMPLATE_ROOT
            pipeline_module.SCENARIO_TEMPLATE_ROOT = scenario_root.resolve()
            scenario_module.SCENARIO_TEMPLATE_ROOT = scenario_root.resolve()
            try:
                pipeline = ControllerScenarioLegalPipeline(
                    controller_agent=Agent(main_prompt="", llm_callable=controller_llm),
                    scenario_agent=ScenarioAgent(
                        main_prompt="",
                        llm_callable=scenario_llm,
                        mcp_query_callable=scenario_mcp,
                    ),
                    legal_analysis_agent=LegalAnalysisAgent(
                        main_prompt="",
                        llm_callable=legal_llm,
                        mcp_query_callable=legal_mcp,
                    ),
                    controller_template_path=str(controller_template),
                    legal_template_path=str(legal_template),
                )

                output_buffer = io.StringIO()

                def fake_output(message):
                    output_buffer.write(str(message) + "\n")

                result = pipeline.run_console(
                    initial_user_input="我在拿到offer前被解雇了",
                    input_func=lambda _="": "不会被读取",
                    output_func=fake_output,
                )
            finally:
                pipeline_module.SCENARIO_TEMPLATE_ROOT = old_pipeline_root
                scenario_module.SCENARIO_TEMPLATE_ROOT = old_scenario_root

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.controller_output["analysis"]["scene_id"], "recruitment_probation")
        self.assertEqual(result.scenario_output["analysis"]["scene_id"], "recruitment_probation")
        self.assertIn("[Scenario Bootstrap] Tool Call(s)", result.scenario_tool_call_history)
        self.assertEqual(len(scenario_mcp.calls), 2)
        self.assertEqual(result.legal_result["final_output"]["askmore"], "no")

        first_legal_prompt = legal_llm.calls[0]["user_prompt"]
        self.assertIn("试用期解除案例", first_legal_prompt)
        self.assertIn("[Scenario Bootstrap] Tool Call(s)", first_legal_prompt)

        out = output_buffer.getvalue()
        self.assertIn("[Handoff] ControllerAgent -> ScenarioAgent", out)
        self.assertIn("[Handoff] ScenarioAgent -> LegalAnalysisAgent", out)

    def test_pipeline_repairs_invalid_scene_id_then_continue(self):
        controller_llm = FakeLLM(
            [
                '{"askmore":"no","user_input":"U1","analysis":{"scene_id":"bad_scene"}}',
                '{"askmore":"no","user_input":"U1","analysis":{"scene_id":"recruitment_probation"}}',
            ]
        )
        scenario_llm = FakeLLM(
            [
                '{"askmore":"no","user_input":"S1","analysis":{"scene_id":"recruitment_probation"}}'
            ]
        )
        legal_llm = FakeLLM(
            [
                '{"askmore":"no","analysis":"最终法律分析"}',
            ]
        )

        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            controller_template = tmp_path / "controller.md"
            legal_template = tmp_path / "legal.md"
            scenario_root = tmp_path / "Prompt_Template" / "ScenarioAgents"
            scenario_root.mkdir(parents=True, exist_ok=True)
            scenario_template = scenario_root / "recruitment_probation.md"

            controller_template.write_text(
                "user={user_input}\nctx={conversation_context}\natt={attachments_meta}",
                encoding="utf-8",
            )
            legal_template.write_text(
                "scene={Scenario_Agent_Input}\nhistory={Tool_Call_History}",
                encoding="utf-8",
            )
            scenario_template.write_text(
                "user={user_input}\nctx={conversation_context}\natt={attachments_meta}",
                encoding="utf-8",
            )

            from Agents import pipeline_agent as pipeline_module
            from Agents import scenario_agent as scenario_module

            old_pipeline_root = pipeline_module.SCENARIO_TEMPLATE_ROOT
            old_scenario_root = scenario_module.SCENARIO_TEMPLATE_ROOT
            pipeline_module.SCENARIO_TEMPLATE_ROOT = scenario_root.resolve()
            scenario_module.SCENARIO_TEMPLATE_ROOT = scenario_root.resolve()
            try:
                pipeline = ControllerScenarioLegalPipeline(
                    controller_agent=Agent(main_prompt="", llm_callable=controller_llm),
                    scenario_agent=ScenarioAgent(main_prompt="", llm_callable=scenario_llm),
                    legal_analysis_agent=LegalAnalysisAgent(main_prompt="", llm_callable=legal_llm),
                    controller_template_path=str(controller_template),
                    legal_template_path=str(legal_template),
                )
                result = pipeline.run_console(
                    initial_user_input="用户输入",
                    input_func=lambda _="": "不会被读取",
                    output_func=lambda _msg: None,
                )
            finally:
                pipeline_module.SCENARIO_TEMPLATE_ROOT = old_pipeline_root
                scenario_module.SCENARIO_TEMPLATE_ROOT = old_scenario_root

        self.assertIsNotNone(result)
        self.assertEqual(len(controller_llm.calls), 2)
        self.assertIn("协议修复", controller_llm.calls[1]["user_prompt"])


if __name__ == "__main__":
    unittest.main()

