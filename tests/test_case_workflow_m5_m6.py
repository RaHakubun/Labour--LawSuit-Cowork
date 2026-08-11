import tempfile
import unittest
from pathlib import Path

from Agents.application.command_service import CaseCommandService
from Agents.application.decisions import RouteScenarioDecision
from Agents.application.handlers.evidence import EvidenceCommandHandler
from Agents.application.handlers.facts import ConfirmFactCommandHandler
from Agents.application.handlers.intake import ControllerCommandHandler
from Agents.application.handlers.legal import LegalCommandHandler
from Agents.application.handlers.rules import RuleCalculationCommandHandler
from Agents.application.handlers.scenario import ScenarioStageHandler
from Agents.application.legal_models import (
    DocumentDraftResult,
    LegalAnalysisResult,
    LegalIssueResult,
)
from Agents.application.scenario_models import AuthorityRetrievalRequest, ScenarioResult
from Agents.domain.case_state import FactStatus
from Agents.domain.commands import (
    CalculateRulePayload,
    ConfirmFactPayload,
    RegisterEvidencePayload,
    RequestAnalysisPayload,
    RequestDocumentPayload,
    SubmitUserMessagePayload,
)
from Agents.infrastructure.evidence_storage import LocalEvidenceStorage
from Agents.infrastructure.memory_uow import InMemoryCaseUnitOfWork
from Agents.runtime.registry import CaseRuntimeRegistry
from Agents.services.evidence_parser import EvidenceParser
from Agents.services.tool_hub import AuthorityDocument, AuthorityToolResult, ToolHub


class CompleteController:
    async def decide(self, *, case_state, user_input):
        return RouteScenarioDecision(
            scene_id="termination_layoff",
            reason="解除争议事实已足以进入证据核验",
            current_goal="核验违法解除与赔偿请求",
        )


class CompleteScenario:
    async def analyze(self, *, role_id, scene_id, case_state):
        return ScenarioResult(
            scene_id=scene_id,
            confidence=0.9,
            retrieval_plan=[
                AuthorityRetrievalRequest(
                    tool_name="检索法律法规-语义",
                    query="违法解除劳动合同的构成与赔偿责任",
                    purpose="核验请求权基础",
                )
            ],
            summary="进入违法解除证据核验。",
        )


class CompleteAuthorityAdapter:
    async def search(self, request):
        return AuthorityToolResult(
            tool_name=request.tool_name,
            normalized_query=request.query,
            attempts=1,
            documents=(
                AuthorityDocument(
                    source_id="npc-labour-contract-law-48-87",
                    title="中华人民共和国劳动合同法相关条文",
                    source_url="https://flk.npc.gov.cn/",
                    excerpt="测试夹具仅验证引用链，不作为线上动态法源。",
                    content_hash="a" * 64,
                ),
            ),
        )


class TraceableLegalProvider:
    async def analyze(self, *, context):
        fact_ids = [item["fact_id"] for item in context["facts"]["confirmed"]]
        evidence_ids = [item["evidence_id"] for item in context["evidence"]]
        authority_ids = [item["authority_id"] for item in context["authorities"]]
        return LegalAnalysisResult(
            summary="现有材料支持形成方向性违法解除分析。",
            report_markdown="## 简要回复\n\n现有材料支持主张违法解除赔偿，但仍应核验原件。",
            issues=[
                LegalIssueResult(
                    title="用人单位解除是否合法",
                    conclusion="现有材料显示解除程序存在明显风险。",
                    fact_ids=fact_ids,
                    evidence_ids=evidence_ids,
                    authority_ids=authority_ids,
                )
            ],
        )

    async def draft_document(self, *, context, document_type):
        issue = context["existing_issues"][0]
        return DocumentDraftResult(
            title="劳动仲裁申请书",
            content=(
                "# 劳动仲裁申请书\n\n申请人：[待填写]\n\n"
                "## 仲裁请求\n请求裁决被申请人支付违法解除赔偿金。\n\n"
                "## 事实与理由\n申请人提交劳动合同及工资材料证明劳动关系。\n\n"
                "## 证据目录\n1. 劳动合同及工资材料。\n\n申请人：[待填写]"
            ),
            fact_ids=issue["fact_ids"],
            evidence_ids=issue["evidence_ids"],
            authority_ids=issue["authority_ids"],
            rule_result_ids=[item["result_id"] for item in context["rule_results"]],
        )


class CaseWorkflowM5M6Tests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.storage = LocalEvidenceStorage(Path(self.temp_dir.name))
        self.uow = InMemoryCaseUnitOfWork()
        scenario = ScenarioStageHandler(
            scenario_provider=CompleteScenario(),
            tool_hub=ToolHub(CompleteAuthorityAdapter()),
        )
        self.registry = CaseRuntimeRegistry(
            unit_of_work=self.uow,
            handlers=[
                ControllerCommandHandler(CompleteController(), scenario),
                EvidenceCommandHandler(
                    EvidenceParser(
                        path_resolver=self.storage.path_for,
                        text_writer=self.storage.save_extracted_text,
                    )
                ),
                ConfirmFactCommandHandler(),
                RuleCalculationCommandHandler(),
                LegalCommandHandler(
                    TraceableLegalProvider(),
                    text_reader=self.storage.read_extracted_text,
                ),
            ],
        )
        self.service = CaseCommandService(
            unit_of_work=self.uow,
            runtime_registry=self.registry,
        )

    async def asyncTearDown(self):
        await self.registry.shutdown(timeout=2)
        self.temp_dir.cleanup()

    async def _submit(self, case, key, payload):
        current = await self.uow.get_case(case.case_id)
        await self.service.submit(
            case_id=case.case_id,
            actor_id="worker-1",
            idempotency_key=key,
            expected_case_version=current.version,
            payload=payload,
        )
        await (await self.registry.get_or_create(case.case_id)).wait_idle()

    async def test_evidence_fact_rule_analysis_and_document_revision_chain(self):
        case = await self.service.create_case(owner_id="worker-1", role_id="worker")
        await self._submit(
            case,
            "route-case",
            SubmitUserMessagePayload(text="公司口头解除劳动合同，我要申请劳动仲裁。"),
        )
        stored = await self.storage.save(
            display_name="../劳动合同与工资.txt",
            media_type="text/plain",
            content=(
                "用人单位：上海示例科技有限公司\n"
                "劳动者：张某\n"
                "入职日期：2022-03-01\n"
                "月工资：10000元\n"
                "解除日期：2026-07-20\n"
            ).encode(),
        )
        self.assertNotIn("..", stored.display_name)
        await self._submit(
            case,
            "upload-contract",
            RegisterEvidencePayload(
                evidence_id=stored.evidence_id,
                display_name=stored.display_name,
                storage_key=stored.storage_key,
                media_type=stored.media_type,
                sha256=stored.sha256,
                size=stored.size,
            ),
        )
        parsed = await self.uow.get_case(case.case_id)
        self.assertEqual(parsed.state.evidence.items[stored.evidence_id].status.value, "parsed")
        self.assertIn("employment.monthly_wage", parsed.state.facts.items)
        self.assertEqual(
            parsed.state.facts.items["employment.monthly_wage"].status,
            FactStatus.PENDING_VERIFICATION,
        )
        await self._submit(
            case,
            "confirm-wage",
            ConfirmFactPayload(fact_id="employment.monthly_wage", value=10000),
        )
        await self._submit(
            case,
            "calculate-wage-base",
            CalculateRulePayload(
                calc_type="wage_base",
                inputs={"monthly_wage": 10000},
                fact_ids=["employment.monthly_wage"],
            ),
        )
        await self._submit(case, "generate-analysis", RequestAnalysisPayload())
        await self._submit(
            case,
            "generate-application",
            RequestDocumentPayload(document_type="labour_arbitration_application"),
        )

        completed = await self.uow.get_case(case.case_id)
        self.assertEqual(len(completed.state.analysis.rule_results), 1)
        self.assertEqual(len(completed.state.analysis.issues), 1)
        artifacts = completed.state.outputs.artifacts
        self.assertEqual({item.artifact_type for item in artifacts.values()}, {"legal_analysis_report", "labour_arbitration_application"})
        application = next(item for item in artifacts.values() if item.artifact_type == "labour_arbitration_application")
        self.assertIn("仲裁请求", application.revisions[0].content)
        self.assertEqual(len(application.revisions[0].rule_result_ids), 1)

        await self._submit(
            case,
            "reconfirm-wage-after-output",
            ConfirmFactPayload(fact_id="employment.monthly_wage", value=10000),
        )
        stale = await self.uow.get_case(case.case_id)
        self.assertTrue(all(item.stale for item in stale.state.outputs.artifacts.values()))

    async def test_parser_failure_is_visible_and_does_not_create_empty_facts(self):
        case = await self.service.create_case(owner_id="worker-1", role_id="worker")
        stored = await self.storage.save(
            display_name="损坏材料.txt",
            media_type="text/plain",
            content=b"\xff\xfe\x00",
        )
        await self._submit(
            case,
            "broken-evidence",
            RegisterEvidencePayload(
                evidence_id=stored.evidence_id,
                display_name=stored.display_name,
                storage_key=stored.storage_key,
                media_type=stored.media_type,
                sha256=stored.sha256,
                size=stored.size,
            ),
        )
        current = await self.uow.get_case(case.case_id)
        events = await self.uow.list_events(case.case_id)
        self.assertEqual(current.state.evidence.items[stored.evidence_id].status.value, "failed")
        self.assertEqual(current.state.facts.items, {})
        self.assertIn("tool.call_failed", [event.event_type for event in events])
        self.assertEqual(events[-1].event_type, "operation.failed")

    async def test_extension_and_media_type_must_match(self):
        with self.assertRaisesRegex(ValueError, "does not match media type"):
            await self.storage.save(
                display_name="伪装材料.pdf",
                media_type="text/plain",
                content=b"not a pdf",
            )


if __name__ == "__main__":
    unittest.main()
