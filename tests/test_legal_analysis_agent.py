import tempfile
import unittest
from pathlib import Path

from Agents.legal_analysis_agent import LegalAnalysisAgent


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


class LegalAnalysisAgentTests(unittest.TestCase):
    def test_run_until_done_yes_then_no_with_tool_array(self):
        fake_llm = FakeLLM(
            [
                '{"askmore":"yes","tool":[{"tool_name":"检索司法案例-语义","input":"帮我找到一个实习期被解雇的案例"}]}',
                '{"askmore":"no","analysis":"最终分析文档"}',
            ]
        )
        fake_mcp = FakeMCP()
        agent = LegalAnalysisAgent(llm_callable=fake_llm, mcp_query_callable=fake_mcp)

        with tempfile.TemporaryDirectory() as tmp_dir:
            template_path = Path(tmp_dir) / "LegalAnalysisAgent.md"
            template_path.write_text(
                "上游输入:\n{Scenario_Agent_Input}\n\n工具历史:\n{Tool_Call_History}",
                encoding="utf-8",
            )
            result = agent.run_until_done(
                scenario_agent_input="scene input",
                template_path=str(template_path),
            )

        self.assertEqual(result["final_output"]["askmore"], "no")
        self.assertEqual(len(fake_mcp.calls), 1)
        self.assertEqual(fake_mcp.calls[0][0], "检索司法案例-语义")
        self.assertIn("[Round 1] Tool Call(s)", result["tool_call_history"])
        second_prompt = fake_llm.calls[1]["user_prompt"]
        self.assertIn("scene input", second_prompt)
        self.assertIn("mcp_query(\"检索司法案例-语义\"", second_prompt)

    def test_run_until_done_supports_tool_map(self):
        fake_llm = FakeLLM(
            [
                '{"askmore":"yes","tool":{"检索司法案例-关键词":"offer未入职毁约案例","检索法律法规-语义":"offer未入职被解约涉及哪些法律"}}',
                '{"askmore":"no","analysis":"最终分析"}',
            ]
        )
        fake_mcp = FakeMCP()
        agent = LegalAnalysisAgent(llm_callable=fake_llm, mcp_query_callable=fake_mcp)

        with tempfile.TemporaryDirectory() as tmp_dir:
            template_path = Path(tmp_dir) / "LegalAnalysisAgent.md"
            template_path.write_text(
                "{Scenario_Agent_Input}\n{Tool_Call_History}",
                encoding="utf-8",
            )
            result = agent.run_until_done(
                scenario_agent_input="scene input",
                template_path=str(template_path),
            )

        self.assertEqual(result["final_output"]["askmore"], "no")
        self.assertEqual(len(fake_mcp.calls), 2)
        self.assertEqual(fake_mcp.calls[0][0], "检索司法案例-关键词")
        self.assertEqual(fake_mcp.calls[1][0], "检索法律法规-语义")

    def test_run_until_done_rejects_unsupported_tool_name(self):
        fake_llm = FakeLLM(
            [
                '{"askmore":"yes","tool":[{"tool_name":"非法工具","input":"x"}]}',
            ]
        )
        fake_mcp = FakeMCP()
        agent = LegalAnalysisAgent(llm_callable=fake_llm, mcp_query_callable=fake_mcp)

        with tempfile.TemporaryDirectory() as tmp_dir:
            template_path = Path(tmp_dir) / "LegalAnalysisAgent.md"
            template_path.write_text(
                "{Scenario_Agent_Input}\n{Tool_Call_History}",
                encoding="utf-8",
            )
            with self.assertRaises(ValueError):
                agent.run_until_done(
                    scenario_agent_input="scene input",
                    template_path=str(template_path),
                )

        self.assertEqual(fake_mcp.calls, [])

    def test_run_until_done_requires_tool_when_askmore_yes(self):
        fake_llm = FakeLLM(
            [
                '{"askmore":"yes"}',
            ]
        )
        fake_mcp = FakeMCP()
        agent = LegalAnalysisAgent(llm_callable=fake_llm, mcp_query_callable=fake_mcp)

        with tempfile.TemporaryDirectory() as tmp_dir:
            template_path = Path(tmp_dir) / "LegalAnalysisAgent.md"
            template_path.write_text(
                "{Scenario_Agent_Input}\n{Tool_Call_History}",
                encoding="utf-8",
            )
            with self.assertRaises(ValueError):
                agent.run_until_done(
                    scenario_agent_input="scene input",
                    template_path=str(template_path),
                )

    def test_run_until_done_parses_mcp_query_string(self):
        fake_llm = FakeLLM(
            [
                '{"askmore":"yes","tool":"mcp_query(\\"检索法律法规-语义\\",\\"offer未入职法律依据\\")"}',
                '{"askmore":"no","analysis":"最终分析"}',
            ]
        )
        fake_mcp = FakeMCP()
        agent = LegalAnalysisAgent(llm_callable=fake_llm, mcp_query_callable=fake_mcp)

        with tempfile.TemporaryDirectory() as tmp_dir:
            template_path = Path(tmp_dir) / "LegalAnalysisAgent.md"
            template_path.write_text(
                "{Scenario_Agent_Input}\n{Tool_Call_History}",
                encoding="utf-8",
            )
            result = agent.run_until_done(
                scenario_agent_input="scene input",
                template_path=str(template_path),
            )

        self.assertEqual(result["final_output"]["askmore"], "no")
        self.assertEqual(fake_mcp.calls[0][0], "检索法律法规-语义")

    def test_run_until_done_supports_toolname_placeholder_map_with_mcp_query_value(self):
        fake_llm = FakeLLM(
            [
                '{"askmore":"yes","tool":{"toolname1":"mcp_query(\\"检索司法案例-关键词\\",\\"offer未入职毁约案例\\")","toolname2":"mcp_query(\\"检索法律法规-语义\\",\\"offer未入职涉及法条\\")"}}',
                '{"askmore":"no","analysis":"最终分析"}',
            ]
        )
        fake_mcp = FakeMCP()
        agent = LegalAnalysisAgent(llm_callable=fake_llm, mcp_query_callable=fake_mcp)

        with tempfile.TemporaryDirectory() as tmp_dir:
            template_path = Path(tmp_dir) / "LegalAnalysisAgent.md"
            template_path.write_text(
                "{Scenario_Agent_Input}\n{Tool_Call_History}",
                encoding="utf-8",
            )
            result = agent.run_until_done(
                scenario_agent_input="scene input",
                template_path=str(template_path),
            )

        self.assertEqual(result["final_output"]["askmore"], "no")
        self.assertEqual(len(fake_mcp.calls), 2)
        self.assertEqual(fake_mcp.calls[0][0], "检索司法案例-关键词")
        self.assertEqual(fake_mcp.calls[1][0], "检索法律法规-语义")


if __name__ == "__main__":
    unittest.main()
