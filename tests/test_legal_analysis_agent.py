import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

from Agents.legal_analysis_agent import LegalAnalysisAgent


LEGAL_DATA = (
    '{"schema_version":"1.0","issues":[{"issue_id":"I1","title":"问题1","conclusion":"结论1",'
    '"confidence":"C3","citation_ids":["C1"]}],"citations":[{"citation_id":"C1","kind":"law",'
    '"law_name":"中华人民共和国劳动合同法","article":"第四十条","title":"中华人民共和国劳动合同法第四十条",'
    '"quote":"条文摘录","source":{"tool_name":"检索法律法规-语义","query":"违法解除条款"}}]}'
)


class FakeLLM:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def __call__(self, **kwargs):
        self.calls.append(kwargs)
        return self.responses[len(self.calls) - 1]


class StreamingFakeLLM:
    def __init__(self, response):
        self.response = response
        self.calls = []

    def __call__(self, **kwargs):
        self.calls.append(kwargs)
        on_token = kwargs.get("on_token")
        if on_token is not None:
            on_token("chunk-x")
            on_token("chunk-y")
        return self.response


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
                f'{{"askmore":"no","analysis":"最终分析文档","data":{LEGAL_DATA}}}',
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
                f'{{"askmore":"no","analysis":"最终分析","data":{LEGAL_DATA}}}',
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

    def test_run_until_done_supports_query_when_askmore_yes_without_tools(self):
        fake_llm = FakeLLM(
            [
                '{"askmore":"yes","query":"请补充合同解除时间。"}',
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

        self.assertEqual(result["final_output"]["askmore"], "yes")
        self.assertEqual(result["final_output"]["query"], "请补充合同解除时间。")
        self.assertEqual(fake_mcp.calls, [])

    def test_run_until_done_supports_end_output(self):
        fake_llm = FakeLLM(
            [
                f'{{"askmore":"end","query":"本轮会话结束。","analysis":"# 最终报告\\\\n建议申请仲裁。","data":{LEGAL_DATA}}}',
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

        self.assertEqual(result["final_output"]["askmore"], "end")
        self.assertIn("最终报告", result["final_output"]["analysis"])
        self.assertEqual(fake_mcp.calls, [])

    def test_run_until_done_parses_mcp_query_string(self):
        fake_llm = FakeLLM(
            [
                '{"askmore":"yes","tool":"mcp_query(\\"检索法律法规-语义\\",\\"offer未入职法律依据\\")"}',
                f'{{"askmore":"no","analysis":"最终分析","data":{LEGAL_DATA}}}',
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
                f'{{"askmore":"no","analysis":"最终分析","data":{LEGAL_DATA}}}',
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

    def test_run_turn_streaming_uses_injected_llm_callable(self):
        fake_llm = StreamingFakeLLM(f'{{"askmore":"no","analysis":"最终分析","data":{LEGAL_DATA}}}')
        agent = LegalAnalysisAgent(llm_callable=fake_llm, mcp_query_callable=FakeMCP())

        with tempfile.TemporaryDirectory() as tmp_dir:
            template_path = Path(tmp_dir) / "LegalAnalysisAgent.md"
            template_path.write_text(
                "{Scenario_Agent_Input}\n{Tool_Call_History}",
                encoding="utf-8",
            )
            streamed = []
            callback = streamed.append
            with patch(
                "Agents.legal_analysis_agent.llm_call.chat_completion_with_callback",
                side_effect=AssertionError("global stream helper should not be used"),
            ):
                result = agent.run_turn(
                    scenario_agent_input="scene input",
                    tool_call_history="",
                    template_path=str(template_path),
                    on_token=callback,
                )

        self.assertEqual(result.askmore, "no")
        self.assertEqual(streamed, ["chunk-x", "chunk-y"])
        self.assertIs(fake_llm.calls[0]["on_token"], callback)

    def test_run_until_done_requires_data_when_no(self):
        fake_llm = FakeLLM(
            [
                '{"askmore":"no","analysis":"最终分析文档"}',
            ]
        )
        agent = LegalAnalysisAgent(llm_callable=fake_llm, mcp_query_callable=FakeMCP())

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

    def test_run_until_done_rejects_citation_source_tool_name_not_allowed(self):
        bad_data = (
            '{"schema_version":"1.0","issues":[{"issue_id":"I1","title":"问题1","conclusion":"结论1",'
            '"confidence":"C3","citation_ids":["C1"]}],"citations":[{"citation_id":"C1","kind":"law",'
            '"law_name":"中华人民共和国劳动合同法","article":"第四十条","title":"中华人民共和国劳动合同法第四十条",'
            '"quote":"条文摘录","source":{"tool_name":"非法工具","query":"违法解除条款"}}]}'
        )
        fake_llm = FakeLLM(
            [
                f'{{"askmore":"no","analysis":"最终分析文档","data":{bad_data}}}',
            ]
        )
        agent = LegalAnalysisAgent(llm_callable=fake_llm, mcp_query_callable=FakeMCP())

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


if __name__ == "__main__":
    unittest.main()
