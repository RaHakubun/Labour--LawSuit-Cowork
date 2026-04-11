import unittest
from unittest.mock import patch

import Agents.llm_call as llm_call


class _FakeMessage:
    def __init__(self, content):
        self.content = content


class _FakeChoice:
    def __init__(self, content):
        self.message = _FakeMessage(content)


class _FakeCompletion:
    def __init__(self, content):
        self.choices = [_FakeChoice(content)]


class _FakeCompletions:
    def __init__(self):
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return _FakeCompletion('ok')


class _FakeChat:
    def __init__(self, completions):
        self.completions = completions


class _FakeClient:
    def __init__(self, completions):
        self.chat = _FakeChat(completions)


class LLMCallTests(unittest.TestCase):
    def test_get_client_raises_import_error_when_openai_not_installed(self):
        real_import = __import__

        def fake_import(name, globals=None, locals=None, fromlist=(), level=0):
            if name == "openai":
                raise ImportError("No module named 'openai'")
            return real_import(name, globals, locals, fromlist, level)

        with patch("builtins.__import__", side_effect=fake_import):
            with self.assertRaises(ImportError) as ctx:
                llm_call.get_client()

        self.assertIn('openai', str(ctx.exception).lower())

    def test_chat_completion_uses_single_user_message_and_merges_system_text(self):
        fake_completions = _FakeCompletions()
        fake_client = _FakeClient(fake_completions)

        with patch('Agents.llm_call.get_client', return_value=fake_client):
            result = llm_call.chat_completion(
                user_prompt='用户输入',
                system_prompt='系统提示',
                model='demo-model',
            )

        self.assertEqual(result, 'ok')
        self.assertEqual(len(fake_completions.calls), 1)

        messages = fake_completions.calls[0]['messages']
        self.assertEqual(len(messages), 1)
        self.assertEqual(messages[0]['role'], 'user')
        self.assertIn('系统提示', messages[0]['content'])
        self.assertIn('用户输入', messages[0]['content'])


if __name__ == '__main__':
    unittest.main()
