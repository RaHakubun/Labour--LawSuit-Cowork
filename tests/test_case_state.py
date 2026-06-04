import unittest

from Agents.case_state import CaseState, CaseWorkspace, EvidenceItem, FactItem, OutputArtifact


class CaseStateTests(unittest.TestCase):
    def test_empty_case_state_initializes_layered_structure(self):
        state = CaseState.new()

        self.assertEqual(state.interaction["stage"], "controller")
        self.assertEqual(state.facts["items"], {})
        self.assertEqual(state.evidence["items"], {})
        self.assertEqual(state.analysis["issues"], [])
        self.assertEqual(state.outputs["artifacts"], [])

    def test_fact_item_preserves_status_source_and_confidence(self):
        fact = FactItem(
            fact_id="employment.start_date",
            value="2024-01-01",
            status="user_claimed",
            source={"agent": "ScenarioAgent"},
            confidence="C2",
            updated_at="2026-06-04T00:00:00Z",
        )

        payload = fact.to_dict()

        self.assertEqual(payload["status"], "user_claimed")
        self.assertEqual(payload["source"]["agent"], "ScenarioAgent")
        self.assertEqual(payload["confidence"], "C2")

    def test_evidence_item_links_to_facts(self):
        evidence = EvidenceItem(
            evidence_id="ev1",
            type="contract",
            source={"filename": "contract.txt"},
            extracted_text="劳动合同文本",
            linked_fact_ids=["employment.contract_signed"],
            probative_value="high",
            authenticity_risk="low",
        )

        payload = evidence.to_dict()

        self.assertEqual(payload["linked_fact_ids"], ["employment.contract_signed"])
        self.assertEqual(payload["authenticity_risk"], "low")

    def test_output_artifact_records_snapshot_version(self):
        artifact = OutputArtifact(
            artifact_id="report1",
            type="legal_report",
            title="法律分析报告",
            content="# 报告",
            generated_by="LegalAnalysisAgent",
            generated_at="2026-06-04T00:00:00Z",
            case_version=3,
        )

        self.assertEqual(artifact.to_dict()["case_version"], 3)

    def test_workspace_roundtrip_contains_case_version(self):
        workspace = CaseWorkspace.new(session_id="s1", role_id="worker")
        restored = CaseWorkspace.from_dict(workspace.to_dict())

        self.assertEqual(restored.session_id, "s1")
        self.assertEqual(restored.role_id, "worker")
        self.assertEqual(restored.version, 0)
        self.assertEqual(restored.case_state.interaction["stage"], "controller")


if __name__ == "__main__":
    unittest.main()
