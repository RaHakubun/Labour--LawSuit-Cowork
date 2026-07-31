import asyncio
import unittest

from Agents.application.command_service import CaseCommandService
from Agents.application.decisions import (
    AskClarificationDecision,
    RouteScenarioDecision,
)
from Agents.application.handlers.intake import IntakeCommandHandler
from Agents.domain.case_state import CaseStage
from Agents.domain.commands import ConfirmHandoffPayload, SubmitUserMessagePayload
from Agents.infrastructure.memory_uow import InMemoryCaseUnitOfWork
from Agents.runtime.registry import CaseRuntimeRegistry


class TerminationDecisionProvider:
    async def decide(self, *, case_state, user_input):
        if "工资" not in user_input or "解除时间" not in user_input:
            return AskClarificationDecision(
                question="请补充解除时间、月工资及是否收到书面解除通知。",
                required_fact_ids=[
                    "termination.date",
                    "employment.monthly_wage",
                    "termination.written_notice",
                ],
            )
        return RouteScenarioDecision(
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
            question="请补充书面解除通知。",
            required_fact_ids=["termination.written_notice"],
        )


class CaseRuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.uow = InMemoryCaseUnitOfWork()
        handler = IntakeCommandHandler(TerminationDecisionProvider())
        self.registry = CaseRuntimeRegistry(unit_of_work=self.uow, handlers=[handler])
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

    async def test_confirmed_handoff_advances_case_to_scenario_stage(self):
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
        routed = await self.uow.get_case(case.case_id)
        pending = routed.state.interaction.pending_handoff
        self.assertIsNotNone(pending)

        await self.service.submit(
            case_id=case.case_id,
            actor_id="worker-1",
            idempotency_key="approve-scenario-handoff",
            expected_case_version=routed.version,
            payload=ConfirmHandoffPayload(
                handoff_id=pending.handoff_id,
                approve=True,
            ),
        )
        await runtime.wait_idle()

        updated = await self.uow.get_case(case.case_id)
        self.assertEqual(updated.state.interaction.stage, CaseStage.EVIDENCE_PROCESSING)
        self.assertEqual(updated.state.interaction.active_agent, "ScenarioAgent")
        self.assertIsNone(updated.state.interaction.pending_handoff)
        self.assertIn(
            "handoff.confirmed",
            [event.event_type for event in await self.uow.list_events(case.case_id)],
        )

    async def test_different_cases_progress_concurrently(self):
        await self.registry.shutdown(timeout=2)
        provider = BlockingDecisionProvider()
        self.registry = CaseRuntimeRegistry(
            unit_of_work=self.uow,
            handlers=[IntakeCommandHandler(provider)],
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


if __name__ == "__main__":
    unittest.main()
