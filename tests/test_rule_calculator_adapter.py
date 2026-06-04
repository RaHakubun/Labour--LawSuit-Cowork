import unittest

from Agents.case_state import CaseWorkspace
from Agents.rule_calculator import RuleCalculatorAdapter
from Agents.state_manager import StateManager


class RuleCalculatorAdapterTests(unittest.TestCase):
    def test_successful_calculation_writes_calculation_patch(self):
        workspace = CaseWorkspace.new(session_id="s1", role_id="worker")
        adapter = RuleCalculatorAdapter()
        patch = adapter.build_patch(
            workspace,
            calc_type="overtime",
            payload={
                "monthly_base_salary": 21750,
                "overtime_base_salary": 21750,
                "weekday_overtime_hours": 8,
                "restday_overtime_hours": 0,
                "holiday_overtime_hours": 0,
            },
        )

        result = StateManager().apply_patch(workspace, patch)

        self.assertTrue(result.accepted)
        self.assertEqual(workspace.case_state.analysis["calculations"][0]["calc_type"], "overtime")
        self.assertEqual(workspace.case_state.analysis["calculations"][0]["ok"], True)

    def test_failed_calculation_writes_missing_information(self):
        workspace = CaseWorkspace.new(session_id="s1", role_id="worker")
        adapter = RuleCalculatorAdapter()
        patch = adapter.build_patch(workspace, calc_type="severance", payload={})

        result = StateManager().apply_patch(workspace, patch)

        self.assertTrue(result.accepted)
        self.assertIn("severance", workspace.case_state.analysis["missing_information"][0]["field"])


if __name__ == "__main__":
    unittest.main()
