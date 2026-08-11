import asyncio
import unittest

from Agents.application.command_service import CaseCommandService
from Agents.application.decisions import (
    AskClarificationDecision,
    RouteScenarioDecision,
)
from Agents.application.handlers.intake import ControllerCommandHandler
from Agents.application.handlers.scenario import ScenarioStageHandler
from Agents.application.scenario_models import (
    AuthorityRetrievalRequest,
    CandidateFact,
    EvidenceRequirement,
    ScenarioResult,
)
from Agents.domain.case_state import CaseStage
from Agents.domain.commands import CancelOperationPayload, SubmitUserMessagePayload
from Agents.infrastructure.memory_uow import InMemoryCaseUnitOfWork
from Agents.runtime.registry import CaseRuntimeRegistry
from Agents.services.tool_hub import (
    AuthorityDocument,
    AuthorityToolResult,
    ToolExecutionError,
    ToolHub,
)


class TerminationDecisionProvider:
    async def decide(self, *, case_state, user_input):
        if "工资" not in user_input or "解除时间" not in user_input:
            return AskClarificationDecision(
                decision_type="ask_clarification",
                question="请补充解除时间、月工资及是否收到书面解除通知。",
                required_fact_ids=[
                    "termination.date",
                    "employment.monthly_wage",
                    "termination.written_notice",
                ],
            )
        return RouteScenarioDecision(
            decision_type="route_scenario",
            scene_id="termination_layoff",
            reason="解除时间、工资与通知形式已具备，可进入解除场景核验证据。",
            current_goal="判断解除程序与赔偿请求",
        )


class BlockingDecisionProvider:
    def __init__(self):
        self.started = 0
        self.both_started = asyncio.Event()

    async def decide(self, *, case_state, user_input):
        self.started += 1
        if self.started == 2:
            self.both_started.set()
        await asyncio.wait_for(self.both_started.wait(), timeout=1)
        return AskClarificationDecision(
            decision_type="ask_clarification",
            question="请补充书面解除通知。",
            required_fact_ids=["termination.written_notice"],
        )


class CancellableDecisionProvider:
    def __init__(self):
        self.started = asyncio.Event()

    async def decide(self, *, case_state, user_input):
        self.started.set()
        await asyncio.Event().wait()


class TerminationScenarioProvider:
    async def analyze(self, *, role_id, scene_id, case_state):
        return ScenarioResult(
            scene_id=scene_id,
            confidence=0.84,
            candidate_facts=[
                CandidateFact(
                    fact_id="termination.written_notice",
                    value=False,
                    confidence=0.95,
                    derivation="用户明确表示未收到书面解除通知。",
                )
            ],
            missing_fact_questions=["请上传劳动合同及能够证明解除决定的沟通记录。"],
            evidence_requirements=[
                EvidenceRequirement(
                    evidence_type="劳动合同",
                    purpose="核对工作年限、工资约定及解除条款",
                )
            ],
            retrieval_plan=[
                AuthorityRetrievalRequest(
                    tool_name="检索法律法规-语义",
                    query="用人单位以绩效不合格为由口头解除劳动合同的法定条件和程序",
                    purpose="核对解除依据与书面通知程序",
                )
            ],
            summary="已进入解除裁员场景，需核对解除依据、程序和证据。",
        )


class ReviewedAuthorityAdapter:
    async def search(self, request):
        return AuthorityToolResult(
            tool_name=request.tool_name,
            normalized_query=" ".join(request.query.split()),
            attempts=1,
            documents=(
                AuthorityDocument(
                    source_id="labour-contract-law-article-40",
                    title="中华人民共和国劳动合同法第四十条",
                    source_url="https://flk.npc.gov.cn/",
                    excerpt="本协议样本用于验证权威资料引用结构，不作为线上动态法源。",
                    content_hash=(
                        "9d52a5ac0443cf77158572b1fdb7675a"
                        "fdcdba53c6b677586e8b84387548657d"
                    ),
                ),
            ),
        )


class UnavailableAuthorityAdapter:
    async def search(self, request):
        raise ToolExecutionError(
            "authority provider rejected the request",
            retryable=False,
        )


class CaseRuntimeTests(unittest.IsolatedAsyncioTestCase):
    @staticmethod
    def _controller_handler(
        decision_provider,
        authority_adapter=None,
    ):
        scenario_handler = ScenarioStageHandler(
            scenario_provider=TerminationScenarioProvider(),
            tool_hub=ToolHub(authority_adapter or ReviewedAuthorityAdapter()),
        )
        return ControllerCommandHandler(decision_provider, scenario_handler)

    async def asyncSetUp(self):
        self.uow = InMemoryCaseUnitOfWork()
        handlers = [self._controller_handler(TerminationDecisionProvider())]
        self.registry = CaseRuntimeRegistry(unit_of_work=self.uow, handlers=handlers)
        self.service = CaseCommandService(
            unit_of_work=self.uow,
            runtime_registry=self.registry,
        )

    async def asyncTearDown(self):
        await self.registry.shutdown(timeout=2)

    async def test_message_runs_through_queue_patch_and_committed_events(self):
        case = await self.service.create_case(owner_id="worker-1", role_id="worker")
        accepted = await self.service.submit(
            case_id=case.case_id,
            actor_id="worker-1",
            idempotency_key="message-1",
            expected_case_version=0,
            payload=SubmitUserMessagePayload(text="公司口头辞退我，没有书面通知"),
        )
        runtime = await self.registry.get_or_create(case.case_id)
        await runtime.wait_idle()

        updated = await self.uow.get_case(case.case_id)
        events = await self.uow.list_events(case.case_id)

        self.assertTrue(accepted.is_new)
        self.assertEqual(updated.version, 2)
        self.assertEqual(updated.state.interaction.last_user_input, "公司口头辞退我，没有书面通知")
        self.assertEqual(len(updated.state.interaction.pending_questions), 1)
        self.assertEqual(
            [event.sequence for event in events],
            list(range(1, len(events) + 1)),
        )
        self.assertEqual(events[-1].event_type, "operation.completed")
        self.assertIn("clarification.requested", [event.event_type for event in events])

    async def test_idempotency_does_not_execute_controller_twice(self):
        case = await self.service.create_case(owner_id="worker-1", role_id="worker")
        first = await self.service.submit(
            case_id=case.case_id,
            actor_id="worker-1",
            idempotency_key="same-browser-request",
            expected_case_version=0,
            payload=SubmitUserMessagePayload(text="公司口头辞退我"),
        )
        runtime = await self.registry.get_or_create(case.case_id)
        await runtime.wait_idle()
        duplicate = await self.service.submit(
            case_id=case.case_id,
            actor_id="worker-1",
            idempotency_key="same-browser-request",
            expected_case_version=0,
            payload=SubmitUserMessagePayload(text="公司口头辞退我"),
        )
        await runtime.wait_idle()

        self.assertFalse(duplicate.is_new)
        self.assertEqual(first.command.command_id, duplicate.command.command_id)
        completed = [
            event
            for event in await self.uow.list_events(case.case_id)
            if event.event_type == "operation.completed"
        ]
        self.assertEqual(len(completed), 1)

    async def test_controller_routes_and_executes_scenario_without_user_gate(self):
        case = await self.service.create_case(owner_id="worker-1", role_id="worker")
        await self.service.submit(
            case_id=case.case_id,
            actor_id="worker-1",
            idempotency_key="complete-intake-message",
            expected_case_version=0,
            payload=SubmitUserMessagePayload(
                text=(
                    "公司在2026年7月20日口头解除劳动合同，解除时间已经明确，"
                    "我的月工资是10000元，没有收到书面解除通知。"
                )
            ),
        )
        runtime = await self.registry.get_or_create(case.case_id)
        await runtime.wait_idle()

        updated = await self.uow.get_case(case.case_id)
        self.assertEqual(updated.state.interaction.stage, CaseStage.EVIDENCE_PROCESSING)
        self.assertEqual(updated.state.interaction.active_agent, "ControllerAgent")
        self.assertEqual(
            updated.state.interaction.active_scene_id,
            "termination_layoff",
        )
        self.assertIn("termination.written_notice", updated.state.facts.items)
        self.assertEqual(len(updated.state.analysis.authorities), 1)
        event_types = [
            event.event_type for event in await self.uow.list_events(case.case_id)
        ]
        self.assertIn("state.transitioned", event_types)
        self.assertIn("clarification.requested", event_types)
        self.assertIn(
            "tool.call_completed",
            event_types,
        )

    async def test_different_cases_progress_concurrently(self):
        await self.registry.shutdown(timeout=2)
        provider = BlockingDecisionProvider()
        self.registry = CaseRuntimeRegistry(
            unit_of_work=self.uow,
            handlers=[
                self._controller_handler(provider),
            ],
        )
        self.service = CaseCommandService(
            unit_of_work=self.uow,
            runtime_registry=self.registry,
        )
        first_case = await self.service.create_case(owner_id="worker-1", role_id="worker")
        second_case = await self.service.create_case(owner_id="worker-2", role_id="worker")

        await asyncio.gather(
            self.service.submit(
                case_id=first_case.case_id,
                actor_id="worker-1",
                idempotency_key="first-case-message",
                expected_case_version=0,
                payload=SubmitUserMessagePayload(text="第一名劳动者被口头辞退"),
            ),
            self.service.submit(
                case_id=second_case.case_id,
                actor_id="worker-2",
                idempotency_key="second-case-message",
                expected_case_version=0,
                payload=SubmitUserMessagePayload(text="第二名劳动者被口头辞退"),
            ),
        )
        await asyncio.gather(
            (await self.registry.get_or_create(first_case.case_id)).wait_idle(),
            (await self.registry.get_or_create(second_case.case_id)).wait_idle(),
        )

        self.assertEqual(provider.started, 2)
        self.assertEqual((await self.uow.get_case(first_case.case_id)).version, 2)
        self.assertEqual((await self.uow.get_case(second_case.case_id)).version, 2)

    async def test_required_authority_failure_stops_without_unfounded_sources(self):
        await self.registry.shutdown(timeout=2)
        self.registry = CaseRuntimeRegistry(
            unit_of_work=self.uow,
            handlers=[
                self._controller_handler(
                    TerminationDecisionProvider(),
                    UnavailableAuthorityAdapter(),
                ),
            ],
        )
        self.service = CaseCommandService(
            unit_of_work=self.uow,
            runtime_registry=self.registry,
        )
        case = await self.service.create_case(owner_id="worker-3", role_id="worker")
        await self.service.submit(
            case_id=case.case_id,
            actor_id="worker-3",
            idempotency_key="route-termination-case",
            expected_case_version=0,
            payload=SubmitUserMessagePayload(
                text=(
                    "公司在2026年7月20日以绩效不合格为由口头辞退我，"
                    "解除时间已经明确，工资是每月10000元，没有书面解除通知。"
                )
            ),
        )
        runtime = await self.registry.get_or_create(case.case_id)
        await runtime.wait_idle()

        updated = await self.uow.get_case(case.case_id)
        events = await self.uow.list_events(case.case_id)
        self.assertEqual(updated.state.analysis.authorities, {})
        self.assertIn("termination.written_notice", updated.state.facts.items)
        self.assertEqual(events[-2].event_type, "tool.call_failed")
        self.assertEqual(events[-1].event_type, "operation.failed")

    async def test_cancel_operation_stops_uncommitted_work_and_records_terminal_event(self):
        await self.registry.shutdown(timeout=2)
        provider = CancellableDecisionProvider()
        self.registry = CaseRuntimeRegistry(
            unit_of_work=self.uow,
            handlers=[self._controller_handler(provider)],
        )
        self.service = CaseCommandService(
            unit_of_work=self.uow,
            runtime_registry=self.registry,
        )
        case = await self.service.create_case(owner_id="worker-cancel", role_id="worker")
        target = await self.service.submit(
            case_id=case.case_id,
            actor_id="worker-cancel",
            idempotency_key="long-running-message",
            expected_case_version=0,
            payload=SubmitUserMessagePayload(text="公司口头辞退我"),
        )
        await asyncio.wait_for(provider.started.wait(), timeout=1)
        cancellation = await self.service.submit(
            case_id=case.case_id,
            actor_id="worker-cancel",
            idempotency_key="cancel-long-running-message",
            expected_case_version=0,
            payload=CancelOperationPayload(operation_id=target.command.command_id),
        )
        runtime = await self.registry.get_or_create(case.case_id)
        await runtime.wait_idle()

        events = await self.uow.list_events(case.case_id)
        target_events = [
            event.event_type
            for event in events
            if event.command_id == target.command.command_id
        ]
        cancellation_events = [
            event.event_type
            for event in events
            if event.command_id == cancellation.command.command_id
        ]
        self.assertEqual(target_events[-1], "operation.cancelled")
        self.assertEqual(cancellation_events[-1], "operation.completed")
        self.assertNotIn("clarification.requested", target_events)


if __name__ == "__main__":
    unittest.main()
