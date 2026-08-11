from __future__ import annotations

import unittest
from uuid import uuid4

from pydantic import TypeAdapter

from Agents.application.decisions import ControllerDecision
from Agents.application.orchestrator import CaseOrchestrator
from Agents.domain.case_state import (
    ArtifactRevision,
    CaseAggregate,
    FactItem,
    FactSource,
    FactStatus,
    OutputArtifact,
)
from Agents.domain.patches import CasePatch, UpsertFact
from Agents.domain.commands import (
    CalculateRulePayload,
    CaseCommand,
    ConfirmFactPayload,
    RequestAnalysisPayload,
    RequestDocumentPayload,
)
from Agents.domain.snapshot_migrations import load_case_aggregate
from Agents.domain.state_manager import DomainStateManager


class ArchitectureBatchOneTests(unittest.TestCase):
    def test_schema_v1_snapshot_migrates_once_to_v2_and_unknown_versions_fail(self):
        legacy = CaseAggregate.create(owner_id="worker-1", role_id="worker").model_dump(
            mode="json"
        )
        self._replace_schema_versions(legacy, "1.0")
        migrated = load_case_aggregate(legacy)

        self.assertEqual(migrated.schema_version, "2.0")
        self.assertEqual(migrated.state.schema_version, "2.0")
        self.assertEqual(migrated.state.interaction.schema_version, "2.0")
        self.assertEqual(migrated.state.evidence.schema_version, "2.0")
        self.assertEqual(migrated.state.analysis.schema_version, "2.0")

        unknown = {**legacy, "schema_version": "9.9"}
        with self.assertRaisesRegex(ValueError, "unsupported case snapshot schema_version"):
            load_case_aggregate(unknown)

    def _replace_schema_versions(self, value, version):
        if isinstance(value, dict):
            if "schema_version" in value:
                value["schema_version"] = version
            for item in value.values():
                self._replace_schema_versions(item, version)
        elif isinstance(value, list):
            for item in value:
                self._replace_schema_versions(item, version)

    def test_controller_contract_accepts_all_six_orchestration_decisions(self):
        adapter = TypeAdapter(ControllerDecision)
        payloads = [
            {
                "decision_type": "ask_clarification",
                "question": "解除发生在哪一天？",
                "required_fact_ids": ["termination.date"],
            },
            {
                "decision_type": "route_scenario",
                "scene_id": "termination_layoff",
                "reason": "用户陈述涉及解除劳动关系",
                "current_goal": "核对解除合法性",
            },
            {
                "decision_type": "request_fact_confirmation",
                "fact_ids": ["termination.date"],
                "reason": "分析前必须确认解除日期",
            },
            {
                "decision_type": "request_analysis",
                "reason": "事实与法源已满足分析条件",
            },
            {
                "decision_type": "request_document",
                "document_type": "labour_arbitration_application",
                "reason": "争议焦点已经形成",
            },
            {
                "decision_type": "continue_current_stage",
                "reason": "继续完成既定规则计算",
            },
        ]

        decisions = [adapter.validate_python(payload) for payload in payloads]

        self.assertEqual(
            [item.decision_type for item in decisions],
            [item["decision_type"] for item in payloads],
        )

    def test_fact_change_only_marks_dependent_artifacts_stale(self):
        aggregate = CaseAggregate.create(owner_id="worker-1", role_id="worker")
        wage_fact = FactItem(
            fact_id="employment.monthly_wage",
            value=10_000,
            status=FactStatus.CLAIMED,
            source=FactSource(kind="user", ref_id="message-1"),
        )
        date_fact = FactItem(
            fact_id="termination.date",
            value="2026-07-20",
            status=FactStatus.CLAIMED,
            source=FactSource(kind="user", ref_id="message-1"),
        )
        aggregate.state.facts.items = {
            wage_fact.fact_id: wage_fact,
            date_fact.fact_id: date_fact,
        }
        wage_artifact = OutputArtifact(
            artifact_type="legal_analysis_report",
            title="工资分析",
            revisions=[
                ArtifactRevision(
                    revision=1,
                    content="工资请求分析",
                    case_version=aggregate.version,
                    fact_ids=[wage_fact.fact_id],
                )
            ],
        )
        date_artifact = OutputArtifact(
            artifact_type="labour_arbitration_application",
            title="仲裁申请书",
            revisions=[
                ArtifactRevision(
                    revision=1,
                    content="解除争议申请",
                    case_version=aggregate.version,
                    fact_ids=[date_fact.fact_id],
                )
            ],
        )
        aggregate.state.outputs.artifacts = {
            wage_artifact.artifact_id: wage_artifact,
            date_artifact.artifact_id: date_artifact,
        }
        patch = CasePatch(
            producer="User",
            base_version=aggregate.version,
            operations=[
                UpsertFact(
                    fact=FactItem(
                        fact_id=wage_fact.fact_id,
                        value=12_000,
                        status=FactStatus.CONFIRMED,
                        source=FactSource(kind="user", ref_id=str(uuid4())),
                    )
                )
            ],
        )

        result = DomainStateManager().apply_patch(aggregate, patch)

        self.assertTrue(result.accepted)
        self.assertTrue(aggregate.state.outputs.artifacts[wage_artifact.artifact_id].stale)
        self.assertFalse(aggregate.state.outputs.artifacts[date_artifact.artifact_id].stale)

    def test_explicit_user_commands_are_normalized_to_controller_decisions(self):
        case_id = uuid4()
        commands = [
            CaseCommand(
                case_id=case_id,
                actor_id="worker-1",
                idempotency_key="confirm",
                expected_case_version=0,
                payload=ConfirmFactPayload(fact_id="termination.date", value="2026-07-20"),
            ),
            CaseCommand(
                case_id=case_id,
                actor_id="worker-1",
                idempotency_key="rule",
                expected_case_version=0,
                payload=CalculateRulePayload(
                    calc_type="wage_base",
                    inputs={"monthly_wage": 10_000},
                    fact_ids=["employment.monthly_wage"],
                ),
            ),
            CaseCommand(
                case_id=case_id,
                actor_id="worker-1",
                idempotency_key="analysis",
                expected_case_version=0,
                payload=RequestAnalysisPayload(),
            ),
            CaseCommand(
                case_id=case_id,
                actor_id="worker-1",
                idempotency_key="document",
                expected_case_version=0,
                payload=RequestDocumentPayload(
                    document_type="labour_arbitration_application"
                ),
            ),
        ]

        decisions = [CaseOrchestrator.decision_for_command(item) for item in commands]

        self.assertEqual(
            [item.decision_type for item in decisions],
            [
                "request_fact_confirmation",
                "continue_current_stage",
                "request_analysis",
                "request_document",
            ],
        )


if __name__ == "__main__":
    unittest.main()
