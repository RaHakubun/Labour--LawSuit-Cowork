from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from Agents.domain.case_state import (
    CaseAggregate,
    EvidenceItem,
    EvidenceStatus,
    FactItem,
    FactSource,
    FactStatus,
)
from Agents.domain.patches import CasePatch, LinkEvidenceToFact, RegisterEvidence, UpsertFact
from Agents.domain.state_manager import DomainStateManager
from Agents.infrastructure.evidence_storage import LocalEvidenceStorage
from Agents.infrastructure.vision_ocr_adapter import OpenAIVisionOcrAdapter
from Agents.infrastructure.mcp_adapter import PkulawAuthoritySearchAdapter
from Agents.services.evidence_parser import EvidenceParser
from Agents.services.context_builders import LegalAnalysisContextBuilder
from Agents.services.tool_hub import ToolExecutionError
from Agents.application.scenario_models import RuleCalculationRequest
from Agents.application.scenario_models import AuthorityRetrievalRequest
from Agents.services.scenario_rule_planner import ScenarioRulePlanner
from utils.pkulaw_mcp_client import McpHttpError


class EvidenceArchitectureBatchTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.storage = LocalEvidenceStorage(Path(self.temp_dir.name))

    async def test_text_extraction_is_stored_outside_case_snapshot(self):
        stored = await self.storage.save(
            display_name="工资证明.txt",
            media_type="text/plain",
            content="月工资：12000元\n入职日期：2022-03-01".encode(),
        )
        evidence = EvidenceItem(
            evidence_id=stored.evidence_id,
            display_name=stored.display_name,
            storage_key=stored.storage_key,
            media_type=stored.media_type,
            sha256=stored.sha256,
            size=stored.size,
            status=EvidenceStatus.REGISTERED,
        )
        parser = EvidenceParser(
            path_resolver=self.storage.path_for,
            text_writer=self.storage.save_extracted_text,
        )

        parsed = await parser.parse(evidence)

        self.assertNotIn("extracted_text", parsed.evidence.model_dump())
        self.assertEqual(parsed.evidence.extracted_text_ref, parsed.extraction.text_ref)
        self.assertEqual(
            await self.storage.read_extracted_text(parsed.extraction.text_ref),
            "月工资：12000元\n入职日期：2022-03-01",
        )
        self.assertEqual(
            {item.fact_id for item in parsed.candidate_facts},
            {"employment.monthly_wage", "employment.start_date"},
        )

        aggregate = CaseAggregate.create(owner_id="worker-1", role_id="worker")
        aggregate.state.evidence.items[parsed.evidence.evidence_id] = parsed.evidence
        aggregate.state.evidence.extractions[
            parsed.extraction.extraction_id
        ] = parsed.extraction
        context = await LegalAnalysisContextBuilder(
            text_reader=self.storage.read_extracted_text
        ).build(aggregate)
        self.assertEqual(
            context["evidence"][0]["extracted_text"],
            "月工资：12000元\n入职日期：2022-03-01",
        )

    async def test_image_evidence_requires_configured_visual_ocr(self):
        stored = await self.storage.save(
            display_name="解除通知.png",
            media_type="image/png",
            content=b"\x89PNG\r\n\x1a\nnot-a-real-image-but-valid-storage-input",
        )
        evidence = EvidenceItem(
            evidence_id=stored.evidence_id,
            display_name=stored.display_name,
            storage_key=stored.storage_key,
            media_type=stored.media_type,
            sha256=stored.sha256,
            size=stored.size,
        )
        parser = EvidenceParser(
            path_resolver=self.storage.path_for,
            text_writer=self.storage.save_extracted_text,
        )

        with self.assertRaisesRegex(ToolExecutionError, "visual OCR is required"):
            await parser.parse(evidence)

        jpeg = await self.storage.save(
            display_name="工资截图.jpeg",
            media_type="image/jpeg",
            content=b"jpeg-protocol-sample",
        )
        self.assertTrue(jpeg.storage_key.endswith(".jpg"))

    async def test_vision_ocr_requires_independent_configuration(self):
        with self.assertRaisesRegex(RuntimeError, "OCR_BASE_URL is required"):
            OpenAIVisionOcrAdapter(base_url="", api_key="", model="")

    async def test_fact_evidence_relationship_is_a_first_class_link(self):
        aggregate = CaseAggregate.create(owner_id="worker-1", role_id="worker")
        evidence = EvidenceItem(
            display_name="工资证明.txt",
            storage_key="0" * 32 + ".txt",
            media_type="text/plain",
            sha256="a" * 64,
            size=10,
        )
        fact = FactItem(
            fact_id="employment.monthly_wage",
            value=12_000,
            status=FactStatus.PENDING_VERIFICATION,
            source=FactSource(kind="evidence", ref_id=str(evidence.evidence_id)),
        )
        manager = DomainStateManager()
        registered = manager.apply_patch(
            aggregate,
            CasePatch(
                producer="EvidenceParser",
                base_version=aggregate.version,
                operations=[RegisterEvidence(evidence=evidence), UpsertFact(fact=fact)],
            ),
        )
        self.assertTrue(registered.accepted)

        linked = manager.apply_patch(
            aggregate,
            CasePatch(
                producer="EvidenceParser",
                base_version=aggregate.version,
                operations=[
                    LinkEvidenceToFact(
                        evidence_id=evidence.evidence_id,
                        fact_id=fact.fact_id,
                    )
                ],
            ),
        )

        self.assertTrue(linked.accepted)
        relationship = next(iter(aggregate.state.evidence.fact_links.values()))
        self.assertEqual(relationship.evidence_id, evidence.evidence_id)
        self.assertEqual(relationship.fact_id, fact.fact_id)

    async def test_scenario_rule_request_maps_only_confirmed_traceable_facts(self):
        aggregate = CaseAggregate.create(owner_id="worker-1", role_id="worker")
        aggregate.state.facts.items["employment.monthly_wage"] = FactItem(
            fact_id="employment.monthly_wage",
            value=12_000,
            status=FactStatus.CONFIRMED,
            source=FactSource(kind="user", ref_id="confirmation-1"),
        )
        request = RuleCalculationRequest(
            calc_type="wage_base",
            input_fact_map={"monthly_wage": "employment.monthly_wage"},
        )

        plan = ScenarioRulePlanner().plan(request, aggregate)

        self.assertEqual(plan.missing_fact_ids, ())
        self.assertEqual(plan.unconfirmed_fact_ids, ())
        self.assertEqual(plan.payload.inputs, {"monthly_wage": 12_000})
        self.assertEqual(plan.payload.fact_ids, ["employment.monthly_wage"])

    async def test_mcp_retries_only_typed_transient_http_failures(self):
        attempts = 0

        def provider(*_args):
            nonlocal attempts
            attempts += 1
            if attempts < 3:
                raise McpHttpError(503, "temporary provider outage")
            return {
                "structuredContent": {
                    "items": [
                        {
                            "id": "law-47",
                            "title": "劳动合同法第四十七条",
                            "content": "经济补偿按工作年限计算。",
                        }
                    ]
                }
            }

        adapter = PkulawAuthoritySearchAdapter(
            token="test-token",
            max_attempts=3,
            query_callable=provider,
        )
        result = await adapter.search(
            AuthorityRetrievalRequest(
                tool_name="检索法律法规-语义",
                query="违法解除 经济补偿",
                purpose="核对赔偿规则",
            )
        )

        self.assertEqual(attempts, 3)
        self.assertEqual(result.attempts, 3)
        self.assertEqual(result.documents[0].source_id, "law-47")


if __name__ == "__main__":
    unittest.main()
