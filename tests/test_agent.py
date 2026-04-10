import io
import os
import tempfile
import unittest
from pathlib import Path

from Agents.agent import Agent


class FakeLLM:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def __call__(self, **kwargs):
        self.calls.append(kwargs)
        return self.responses[len(self.calls) - 1]


class AgentTests(unittest.TestCase):

    def test_run_turn_resolves_controller_template_from_project_root_when_cwd_is_example(self):
        fake_llm = FakeLLM(["ok"])
        agent = Agent(main_prompt="base_system", llm_callable=fake_llm)

        project_root = Path(__file__).resolve().parents[1]
        old_cwd = Path.cwd()
        try:
            os.chdir(project_root / "Example")
            result = agent.run_turn(
                "测试输入",
                template_path="Prompt_Template/ControllerAgent.md",
            )
        finally:
            os.chdir(old_cwd)

        self.assertIn("测试输入", result.injected_prompt)

    def test_run_turn_injects_user_input_and_growing_context(self):
        fake_llm = FakeLLM(["agent_reply_1", "agent_reply_2"])
        agent = Agent(main_prompt="base_system", llm_callable=fake_llm)

        with tempfile.TemporaryDirectory() as tmp_dir:
            template_path = Path(tmp_dir) / "controller.md"
            template_path.write_text(
                "user={user_input}\nctx={conversation_context}\natt={attachments_meta}",
                encoding="utf-8",
            )

            first = agent.run_turn("你好", template_path=str(template_path))
            self.assertEqual(first.agent_reply, "agent_reply_1")
            self.assertIn("user=你好", first.injected_prompt)
            self.assertIn("User: 你好", first.conversation_context)
            self.assertEqual(first.conversation_context.strip(), "User: 你好")

            second = agent.run_turn("我被辞退了", template_path=str(template_path))
            self.assertEqual(second.agent_reply, "agent_reply_2")
            self.assertIn("User: 你好", second.conversation_context)
            self.assertIn("Agent: agent_reply_1", second.conversation_context)
            self.assertIn("User: 我被辞退了", second.conversation_context)

            second_prompt = fake_llm.calls[1]["user_prompt"]
            self.assertIn("ctx=User: 你好", second_prompt)
            self.assertIn("Agent: agent_reply_1", second_prompt)
            self.assertEqual(fake_llm.calls[1]["system_prompt"], "base_system")
            self.assertNotIn("base_url", fake_llm.calls[1])
            self.assertNotIn("api_key", fake_llm.calls[1])


    def test_chat_console_handles_llm_error_without_traceback(self):
        class ErrorLLM:
            def __call__(self, **kwargs):
                raise RuntimeError("401 invalid token")

        agent = Agent(main_prompt="base_system", llm_callable=ErrorLLM())

        with tempfile.TemporaryDirectory() as tmp_dir:
            template_path = Path(tmp_dir) / "controller.md"
            template_path.write_text(
                "user={user_input}\nctx={conversation_context}\natt={attachments_meta}",
                encoding="utf-8",
            )

            output_buffer = io.StringIO()

            def fake_output(message):
                output_buffer.write(str(message) + "\n")

            agent.chat_console(
                initial_user_input="第一轮输入",
                template_path=str(template_path),
                input_func=lambda _='': 'exit',
                output_func=fake_output,
            )

            out = output_buffer.getvalue()
            self.assertIn("AgentError:", out)
            self.assertNotIn("Traceback", out)

    def test_chat_console_prints_user_and_agent_messages(self):
        fake_llm = FakeLLM(
            [
                '{"askmore":"yes","ask":"请继续补充"}',
                '{"askmore":"no","user_input":"第一轮输入\\n第二轮输入","analysis":{"scene_id":"demo"}}',
            ]
        )
        agent = Agent(main_prompt="base_system", llm_callable=fake_llm)

        with tempfile.TemporaryDirectory() as tmp_dir:
            template_path = Path(tmp_dir) / "controller.md"
            template_path.write_text(
                "user={user_input}\nctx={conversation_context}\natt={attachments_meta}",
                encoding="utf-8",
            )

            inputs = iter(["第二轮输入", "exit"])
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
            self.assertIn("User: 第一轮输入", out)
            self.assertIn('Agent: {"askmore":"yes","ask":"请继续补充"}', out)
            self.assertIn("User: 第二轮输入", out)
            self.assertIn('"askmore":"no"', out)


if __name__ == "__main__":
    unittest.main()
