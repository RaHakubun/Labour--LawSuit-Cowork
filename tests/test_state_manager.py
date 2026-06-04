import unittest

from Agents.case_state import CaseWorkspace
from Agents.state_manager import CasePatch, PatchOperation, StateManager


class StateManagerTests(unittest.TestCase):
    def setUp(self):
        self.workspace = CaseWorkspace.new(session_id="s1", role_id="worker")
        self.manager = StateManager()

    def test_valid_patch_merges_and_increments_version(self):
        patch = CasePatch(
            patch_id="p1",
            agent_name="ControllerAgent",
            base_version=0,
            operations=[
                PatchOperation(
                    op="set",
                    path="interaction.current_goal",
                    value="确认违法解除赔偿",
                )
            ],
        )

        result = self.manager.apply_patch(self.workspace, patch)

        self.assertTrue(result.accepted)
        self.assertEqual(self.workspace.version, 1)
        self.assertEqual(self.workspace.case_state.interaction["current_goal"], "确认违法解除赔偿")

    def test_unauthorized_agent_cannot_write_confirmed_fact(self):
        patch = CasePatch(
            patch_id="p1",
            agent_name="ScenarioAgent",
            base_version=0,
            operations=[
                PatchOperation(
                    op="upsert_fact",
                    path="facts.items.employment.start_date",
                    value={"value": "2024-01-01", "status": "confirmed"},
                )
            ],
        )

        result = self.manager.apply_patch(self.workspace, patch)

        self.assertFalse(result.accepted)
        self.assertIn("confirmed facts", result.errors[0])
        self.assertEqual(self.workspace.version, 0)

    def test_scenario_agent_cannot_write_legal_conclusion_to_fact_layer(self):
        patch = CasePatch(
            patch_id="p1",
            agent_name="ScenarioAgent",
            base_version=0,
            operations=[
                PatchOperation(
                    op="upsert_fact",
                    path="facts.items.legal.conclusion",
                    value={"value": "公司违法解除", "status": "user_claimed"},
                )
            ],
        )

        result = self.manager.apply_patch(self.workspace, patch)

        self.assertFalse(result.accepted)
        self.assertIn("legal conclusions", result.errors[0])

    def test_conflicting_fact_is_marked_disputed_without_overwrite(self):
        first = CasePatch(
            patch_id="p1",
            agent_name="EvidenceParser",
            base_version=0,
            operations=[
                PatchOperation(
                    op="upsert_fact",
                    path="facts.items.employment.monthly_wage",
                    value={"value": 10000, "status": "pending_verification"},
                )
            ],
        )
        second = CasePatch(
            patch_id="p2",
            agent_name="ScenarioAgent",
            base_version=1,
            operations=[
                PatchOperation(
                    op="upsert_fact",
                    path="facts.items.employment.monthly_wage",
                    value={"value": 12000, "status": "user_claimed"},
                )
            ],
        )

        self.assertTrue(self.manager.apply_patch(self.workspace, first).accepted)
        result = self.manager.apply_patch(self.workspace, second)

        self.assertTrue(result.accepted)
        item = self.workspace.case_state.facts["items"]["employment.monthly_wage"]
        self.assertEqual(item["value"], 10000)
        self.assertEqual(item["status"], "disputed")
        self.assertEqual(len(self.workspace.case_state.facts["disputed_facts"]), 1)
        self.assertIn("monthly_wage", self.workspace.case_state.analysis["missing_information"][0]["field"])

    def test_stale_patch_is_rejected(self):
        patch = CasePatch(
            patch_id="p1",
            agent_name="ControllerAgent",
            base_version=2,
            operations=[
                PatchOperation(op="set", path="interaction.current_goal", value="x")
            ],
        )

        result = self.manager.apply_patch(self.workspace, patch)

        self.assertFalse(result.accepted)
        self.assertIn("base_version", result.errors[0])


if __name__ == "__main__":
    unittest.main()
