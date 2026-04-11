import unittest

from Agents.presentation_adapter import (
    adapt_controller_payload,
    adapt_legal_payload,
    adapt_payload_by_agent,
    adapt_scenario_payload,
    adapt_tool_history_text,
    build_handoff_block,
)


class PresentationAdapterTests(unittest.TestCase):
    def test_build_handoff_block(self):
        block = build_handoff_block("ControllerAgent", "ScenarioAgent", scene_id="work_injury")
        self.assertEqual(block.kind, "handoff")
        self.assertEqual(block.metadata["from_agent"], "ControllerAgent")
        self.assertEqual(block.metadata["to_agent"], "ScenarioAgent")
        self.assertEqual(block.metadata["scene_id"], "work_injury")

    def test_adapt_controller_askmore_yes(self):
        payload = {"askmore": "yes", "ask": "请补充劳动合同签署时间和解除通知形式。"}
        blocks = adapt_controller_payload(payload)
        self.assertEqual(len(blocks), 1)
        self.assertEqual(blocks[0].kind, "agent_message")
        self.assertIn("请补充", blocks[0].text)

    def test_adapt_controller_askmore_no(self):
        payload = {
            "askmore": "no",
            "user_input": "用户原文",
            "analysis": {
                "scene_id": "termination_layoff",
                "confidence_level": "C3",
                "escalation_flags": "L2",
                "current_status": "已被口头辞退，未收书面通知",
                "user_appeal": "主张违法解除赔偿",
                "faced_problems": "解除理由和程序合法性不足",
                "route_plan": "移交对应场景完成细化",
            },
        }
        blocks = adapt_controller_payload(payload)
        self.assertGreaterEqual(len(blocks), 5)
        self.assertEqual(blocks[0].metadata["scene_id"], "termination_layoff")

    def test_adapt_scenario_payload_with_tools(self):
        payload = {
            "askmore": "no",
            "analysis": {
                "scene_id": "work_injury",
                "current_status": "已发生工伤事故，待认定",
                "user_appeal": "确认工伤并主张停工留薪待遇",
                "faced_problems": "工伤认定材料不足",
                "route_plan": "先补证后进入法律分析",
            },
            "tool": {
                "toolname1": 'mcp_query("检索法律法规-语义","工伤认定适用法规")',
            },
        }
        blocks = adapt_scenario_payload(payload)
        self.assertTrue(any(block.kind == "tool_plan" for block in blocks))

    def test_adapt_legal_payload_final(self):
        payload = {"askmore": "no", "analysis": "# 结论\n建议先补证后仲裁。"}
        blocks = adapt_legal_payload(payload)
        self.assertEqual(len(blocks), 1)
        self.assertEqual(blocks[0].kind, "legal_report")
        self.assertIn("建议先补证", blocks[0].text)

    def test_adapt_tool_history_text(self):
        tool_history = (
            '[Scenario Bootstrap] Tool Call(s)\n'
            '1. mcp_query("检索司法案例-语义", "试用期被辞退案例")\n'
            "result:\n命中"
        )
        blocks = adapt_tool_history_text(tool_history)
        self.assertGreaterEqual(len(blocks), 2)
        self.assertEqual(blocks[0].kind, "tool_result_card")
        self.assertEqual(blocks[-1].kind, "tool_history")
        self.assertTrue(blocks[-1].items)

    def test_dispatch_by_agent(self):
        payload = {"askmore": "yes", "ask": "继续补充证据。"}
        blocks = adapt_payload_by_agent("ControllerAgent", payload)
        self.assertEqual(blocks[0].title, "ControllerAgent 追问")


if __name__ == "__main__":
    unittest.main()
