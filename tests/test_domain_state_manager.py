import unittest
from uuid import uuid4

from Agents.domain.case_state import (
    ArtifactRevision,
    CaseAggregate,
    FactItem,
    FactSource,
    FactStatus,
    IssueCard,
    OutputArtifact,
    RuleResult,
)
from Agents.domain.patches import (
    AddArtifactRevision,
    AddRuleResult,
    CasePatch,
    SetInteraction,
    UpsertFact,
    UpsertIssue,
)
from Agents.domain.state_manager import DomainStateManager


class DomainStateManagerTests(unittest.TestCase):
    def setUp(self):
        self.case = CaseAggregate.create(owner_id="worker-1", role_id="worker")
        self.manager = DomainStateManager()

    def test_rejected_patch_is_atomic(self):
        original = self.case.model_dump(mode="json")
        patch = CasePatch(
            producer="ControllerAgent",
            base_version=0,
            operations=[
                SetInteraction(last_user_input="公司口头辞退我"),
                UpsertFact(
                    fact=FactItem(
                        fact_id="termination.legality",
                        value="违法解除",
                        status=FactStatus.CONFIRMED,
                        source=FactSource(kind="agent", ref_id="controller-turn-1"),
                    )
                ),
            ],
        )

        result = self.manager.apply_patch(self.case, patch)

        self.assertFalse(result.accepted)
        self.assertEqual(self.case.model_dump(mode="json"), original)
        self.assertEqual(self.case.version, 0)

    def test_conflicting_wage_creates_conflict_without_overwrite(self):
        first = FactItem(
            fact_id="employment.monthly_wage",
            value=10000,
            status=FactStatus.PENDING_VERIFICATION,
            source=FactSource(kind="agent", ref_id="scenario-1"),
        )
        second = FactItem(
            fact_id="employment.monthly_wage",
            value=12000,
            status=FactStatus.CLAIMED,
            source=FactSource(kind="user", ref_id="message-2"),
        )
        self.assertTrue(
            self.manager.apply_patch(
                self.case,
                CasePatch(
                    producer="ScenarioAgent",
                    base_version=0,
                    operations=[UpsertFact(fact=first)],
                ),
            ).accepted
        )

        result = self.manager.apply_patch(
            self.case,
            CasePatch(
                producer="ScenarioAgent",
                base_version=1,
                operations=[UpsertFact(fact=second)],
            ),
        )

        self.assertTrue(result.accepted)
        self.assertEqual(
            self.case.state.facts.items["employment.monthly_wage"].value,
            10000,
        )
        self.assertEqual(
            self.case.state.facts.items["employment.monthly_wage"].status,
            FactStatus.DISPUTED,
        )
        self.assertEqual(len(self.case.state.facts.conflicts), 1)

    def test_artifact_requires_existing_references(self):
        artifact = OutputArtifact(
            artifact_type="labour_arbitration_application",
            title="劳动仲裁申请书",
            revisions=[
                ArtifactRevision(
                    revision=1,
                    content="申请人请求确认违法解除并支付赔偿金。",
                    case_version=0,
                    fact_ids=["termination.date"],
                )
            ],
        )

        result = self.manager.apply_patch(
            self.case,
            CasePatch(
                producer="LegalAnalysisAgent",
                base_version=0,
                operations=[AddArtifactRevision(artifact=artifact)],
            ),
        )

        self.assertFalse(result.accepted)
        self.assertIn("fact reference not found", result.errors[0])

    def test_issue_requires_existing_rule_result_references(self):
        issue = IssueCard(
            title="赔偿金额",
            rule_result_ids=[uuid4()],
        )

        result = self.manager.apply_patch(
            self.case,
            CasePatch(
                producer="LegalAnalysisAgent",
                base_version=0,
                operations=[UpsertIssue(issue=issue)],
            ),
        )

        self.assertFalse(result.accepted)
        self.assertIn("rule result reference not found", result.errors[0])

    def test_fact_change_marks_dependent_rule_result_stale_and_blocks_reuse(self):
        fact = FactItem(
            fact_id="employment.monthly_wage",
            value=10_000,
            status=FactStatus.CONFIRMED,
            source=FactSource(kind="user", ref_id="confirmation-1"),
        )
        self.case.state.facts.items[fact.fact_id] = fact
        rule_result = RuleResult(
            rule="wage_base",
            input_case_version=0,
            result={"result": {"monthly_wage": 10_000}},
            fact_ids=[fact.fact_id],
        )
        added = self.manager.apply_patch(
            self.case,
            CasePatch(
                producer="RuleCalculator",
                base_version=0,
                operations=[AddRuleResult(rule_result=rule_result)],
            ),
        )
        self.assertTrue(added.accepted)

        changed = self.manager.apply_patch(
            self.case,
            CasePatch(
                producer="User",
                base_version=1,
                operations=[
                    UpsertFact(
                        fact=FactItem(
                            fact_id=fact.fact_id,
                            value=12_000,
                            status=FactStatus.CONFIRMED,
                            source=FactSource(kind="user", ref_id="confirmation-2"),
                        )
                    )
                ],
            ),
        )
        self.assertTrue(changed.accepted)
        self.assertTrue(
            self.case.state.analysis.rule_results[rule_result.result_id].stale
        )

        reused = self.manager.apply_patch(
            self.case,
            CasePatch(
                producer="LegalAnalysisAgent",
                base_version=2,
                operations=[
                    UpsertIssue(
                        issue=IssueCard(
                            title="赔偿金额",
                            fact_ids=[fact.fact_id],
                            rule_result_ids=[rule_result.result_id],
                        )
                    )
                ],
            ),
        )
        self.assertFalse(reused.accepted)
        self.assertIn("stale rule result cannot support analysis", reused.errors[0])

    def test_user_confirmation_can_correct_agent_candidate_without_false_conflict(self):
        candidate = FactItem(
            fact_id="employment.monthly_wage",
            value=10000,
            status=FactStatus.PENDING_VERIFICATION,
            source=FactSource(kind="agent", ref_id="scenario-1"),
        )
        self.assertTrue(
            self.manager.apply_patch(
                self.case,
                CasePatch(
                    producer="ScenarioAgent",
                    base_version=0,
                    operations=[UpsertFact(fact=candidate)],
                ),
            ).accepted
        )
        corrected = FactItem(
            fact_id="employment.monthly_wage",
            value=12000,
            status=FactStatus.CONFIRMED,
            source=FactSource(kind="user", ref_id="confirm-command"),
        )
        result = self.manager.apply_patch(
            self.case,
            CasePatch(
                producer="User",
                base_version=1,
                operations=[UpsertFact(fact=corrected)],
            ),
        )
        self.assertTrue(result.accepted)
        self.assertEqual(self.case.state.facts.items[candidate.fact_id].value, 12000)
        self.assertEqual(self.case.state.facts.items[candidate.fact_id].status, FactStatus.CONFIRMED)
        self.assertEqual(self.case.state.facts.conflicts, {})


if __name__ == "__main__":
    unittest.main()
