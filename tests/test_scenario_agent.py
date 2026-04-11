import io
import os
import tempfile
import unittest
from pathlib import Path

from Agents.scenario_agent import ScenarioAgent


class FakeLLM:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def __call__(self, **kwargs):
        self.calls.append(kwargs)
        return self.responses[len(self.calls) - 1]


class ScenarioAgentTests(unittest.TestCase):

    def test_run_turn_resolves_template_from_project_root_when_cwd_is_example(self):
        fake_llm = FakeLLM(['{"askmore":"yes","ask":"请补充信息"}'])
        agent = ScenarioAgent(main_prompt="", llm_callable=fake_llm)

        project_root = Path(__file__).resolve().parents[1]
        old_cwd = Path.cwd()
        try:
            os.chdir(project_root / "Example")
            result = agent.run_turn(
                "我在拿到offer前被解雇了",
                template_path="Prompt_Template/ScenarioAgents/recruitment_probation.md",
            )
        finally:
            os.chdir(old_cwd)

        self.assertEqual(result.askmore, "yes")
        self.assertIn("我在拿到offer前被解雇了", result.injected_prompt)

    def test_run_turn_injects_user_input_and_growing_context(self):
        fake_llm = FakeLLM(
            [
                '{"askmore":"yes","ask":"请补充信息"}',
                '{"askmore":"no","user_input":"U1\\nU2","analysis":{"scene_id":"recruitment_probation"}}',
            ]
        )

        with tempfile.TemporaryDirectory() as tmp_dir:
            template_dir = Path(tmp_dir) / "Prompt_Template" / "ScenarioAgents"
            template_dir.mkdir(parents=True, exist_ok=True)
            template_path = template_dir / "demo.md"
            template_path.write_text(
                "user={user_input}\nctx={conversation_context}\natt={attachments_meta}",
                encoding="utf-8",
            )
            agent = ScenarioAgent(
                main_prompt="scenario_system",
                llm_callable=fake_llm,
                template_root=template_dir.resolve(),
            )

            first = agent.run_turn("第一轮", template_path=str(template_path))
            self.assertEqual(first.askmore, "yes")
            self.assertIn("user=第一轮", first.injected_prompt)
            self.assertEqual(first.conversation_context.strip(), "User: 第一轮")

            second = agent.run_turn("第二轮", template_path=str(template_path))
            self.assertEqual(second.askmore, "no")
            self.assertIn("User: 第一轮", second.conversation_context)
            self.assertIn("Agent: {\"askmore\":\"yes\",\"ask\":\"请补充信息\"}", second.conversation_context)

            second_prompt = fake_llm.calls[1]["user_prompt"]
            self.assertIn("ctx=User: 第一轮", second_prompt)
            self.assertIn("Agent: {\"askmore\":\"yes\",\"ask\":\"请补充信息\"}", second_prompt)
            self.assertEqual(fake_llm.calls[1]["system_prompt"], "scenario_system")

    def test_chat_console_ends_when_askmore_no(self):
        fake_llm = FakeLLM(
            [
                '{"askmore":"no","user_input":"第一轮输入","analysis":{"scene_id":"recruitment_probation"}}'
            ]
        )

        with tempfile.TemporaryDirectory() as tmp_dir:
            template_dir = Path(tmp_dir) / "Prompt_Template" / "ScenarioAgents"
            template_dir.mkdir(parents=True, exist_ok=True)
            template_path = template_dir / "demo.md"
            template_path.write_text(
                "user={user_input}\nctx={conversation_context}\natt={attachments_meta}",
                encoding="utf-8",
            )
            agent = ScenarioAgent(
                main_prompt="",
                llm_callable=fake_llm,
                template_root=template_dir.resolve(),
            )
            output_buffer = io.StringIO()

            def fake_output(message):
                output_buffer.write(str(message) + "\n")

            agent.chat_console(
                initial_user_input="第一轮输入",
                template_path=str(template_path),
                input_func=lambda _="": "不会被读取",
                output_func=fake_output,
            )

            out = output_buffer.getvalue()
            self.assertIn("askmore=no, session ended.", out)
            self.assertIn('"askmore": "no"', out)

    def test_chat_console_continues_on_askmore_yes_then_no(self):
        fake_llm = FakeLLM(
            [
                '{"askmore":"yes","ask":"请补充合同签署时间和证据"}',
                '{"askmore":"no","user_input":"第一轮输入\\n第二轮输入","analysis":{"scene_id":"recruitment_probation"}}',
            ]
        )

        with tempfile.TemporaryDirectory() as tmp_dir:
            template_dir = Path(tmp_dir) / "Prompt_Template" / "ScenarioAgents"
            template_dir.mkdir(parents=True, exist_ok=True)
            template_path = template_dir / "demo.md"
            template_path.write_text(
                "user={user_input}\nctx={conversation_context}\natt={attachments_meta}",
                encoding="utf-8",
            )
            agent = ScenarioAgent(
                main_prompt="",
                llm_callable=fake_llm,
                template_root=template_dir.resolve(),
            )
            inputs = iter(["第二轮输入"])
            output_buffer = io.StringIO()

            def fake_input(_prompt=""):
                return next(inputs)

            def fake_output(message):
                output_buffer.write(str(message) + "\n")

            agent.chat_console(
                initial_user_input="第一轮输入",
                template_path=str(template_path),
                input_func=fake_input,
                output_func=fake_output,
            )

            out = output_buffer.getvalue()
            self.assertIn("Agent Ask: 请补充合同签署时间和证据", out)
            self.assertIn("User: 第二轮输入", out)
            self.assertIn("askmore=no, session ended.", out)

    def test_normalize_tool_calls_supports_tool_map(self):
        agent = ScenarioAgent(main_prompt="", llm_callable=FakeLLM(['{"askmore":"no"}']))
        payload = {
            "askmore": "no",
            "tool": {
                "检索司法案例-关键词": "offer未入职毁约案例",
                "检索法律法规-语义": "offer未入职涉及法条",
            },
        }
        calls = agent.normalize_tool_calls(payload)
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[0].tool_name, "检索司法案例-关键词")
        self.assertEqual(calls[1].tool_name, "检索法律法规-语义")

    def test_normalize_tool_calls_supports_toolname_placeholder_map_with_mcp_query(self):
        agent = ScenarioAgent(main_prompt="", llm_callable=FakeLLM(['{"askmore":"no"}']))
        payload = {
            "askmore": "no",
            "tool": {
                "toolname1": 'mcp_query("检索司法案例-关键词","offer未入职毁约案例")',
                "toolname2": 'mcp_query("检索法律法规-语义","offer未入职涉及法条")',
            },
        }
        calls = agent.normalize_tool_calls(payload)
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[0].tool_name, "检索司法案例-关键词")
        self.assertEqual(calls[1].tool_name, "检索法律法规-语义")

    def test_execute_tools_from_payload_retries_then_continue_on_error(self):
        class FlakyMCP:
            def __init__(self):
                self.calls = []
                self.failures = {
                    ("检索司法案例-语义", "先失败后成功"): 2,
                    ("检索法律法规-语义", "一直失败"): 99,
                }

            def __call__(self, service_name, query):
                self.calls.append((service_name, query))
                key = (service_name, query)
                left = self.failures.get(key, 0)
                if left > 0:
                    self.failures[key] = left - 1
                    raise RuntimeError("mcp temporary error")
                return f"MCP 服务名: {service_name}\n查询内容: {query}\n\n结果是:\n命中结果"

        flaky_mcp = FlakyMCP()
        agent = ScenarioAgent(
            main_prompt="",
            llm_callable=FakeLLM(['{"askmore":"no"}']),
            mcp_query_callable=flaky_mcp,
        )
        payload = {
            "askmore": "no",
            "tool": {
                "检索司法案例-语义": "先失败后成功",
                "检索法律法规-语义": "一直失败",
            },
        }

        execution = agent.execute_tools_from_payload(
            payload,
            retry_times=2,
            continue_on_error=True,
        )
        self.assertEqual(len(execution.tool_calls), 2)
        self.assertEqual(len(execution.tool_results), 2)
        self.assertIn("mcp_query(\"检索司法案例-语义\"", execution.tool_call_history)
        self.assertIn("MCP 调用失败（已重试 3 次）", execution.tool_call_history)
        self.assertEqual(len(flaky_mcp.calls), 6)


if __name__ == "__main__":
    unittest.main()
