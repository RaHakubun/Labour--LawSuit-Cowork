import unittest

from Agents.conversation_contract import (
    RenderBlockPayload,
    build_handoff_payload,
    build_message_payload,
    list_agent_names,
    list_role_ids,
    validate_agent_name,
)


class ConversationContractTests(unittest.TestCase):
    def test_validate_agent_name(self):
        self.assertEqual(validate_agent_name("ControllerAgent"), "ControllerAgent")
        with self.assertRaises(ValueError):
            validate_agent_name("UnknownAgent")

    def test_build_message_payload(self):
        block = RenderBlockPayload(kind="section", title="标题", text="内容")
        payload = build_message_payload(
            session_id="s1",
            turn_id=1,
            speaker_type="agent",
            speaker_agent="ScenarioAgent",
            role_id="worker",
            display_blocks=[block],
            created_at_utc="2026-04-10T00:00:00Z",
        )
        d = payload.to_dict()
        self.assertEqual(d["session_id"], "s1")
        self.assertEqual(d["speaker_agent"], "ScenarioAgent")
        self.assertEqual(d["display_blocks"][0]["title"], "标题")

    def test_build_handoff_payload(self):
        handoff = build_handoff_payload(
            session_id="s1",
            turn_id=2,
            from_agent="ControllerAgent",
            to_agent="ScenarioAgent",
            reason="主控路由已完成",
            scene_id="termination_layoff",
            created_at_utc="2026-04-10T00:00:00Z",
        )
        d = handoff.to_dict()
        self.assertEqual(d["from_agent"], "ControllerAgent")
        self.assertEqual(d["to_agent"], "ScenarioAgent")
        self.assertEqual(d["scene_id"], "termination_layoff")

    def test_build_handoff_payload_reject_same_agent(self):
        with self.assertRaises(ValueError):
            build_handoff_payload(
                session_id="s1",
                turn_id=2,
                from_agent="ControllerAgent",
                to_agent="ControllerAgent",
                reason="invalid",
            )

    def test_list_constants(self):
        self.assertIn("ControllerAgent", list_agent_names())
        self.assertIn("worker", list_role_ids())


if __name__ == "__main__":
    unittest.main()

