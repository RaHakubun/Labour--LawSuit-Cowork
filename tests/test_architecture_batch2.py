from __future__ import annotations

import os
import unittest
from uuid import uuid4

from Agents.application.handlers.base import ExecutionBatch
from Agents.application.handlers.facts import ConfirmFactCommandHandler
from Agents.domain.case_state import CaseAggregate, FactItem, FactSource, FactStatus
from Agents.domain.commands import CaseCommand, ConfirmFactPayload
from Agents.domain.patches import CasePatch, SetInteraction
from Agents.infrastructure.database import create_engine, create_session_factory
from Agents.infrastructure.memory_uow import InMemoryCaseUnitOfWork
from Agents.infrastructure.postgres_uow import PostgresCaseUnitOfWork
from Agents.runtime.case_runtime import CaseRuntime
from Agents.runtime.registry import CaseRuntimeRegistry


def confirmation_command(aggregate: CaseAggregate, *, key: str) -> CaseCommand:
    return CaseCommand(
        case_id=aggregate.case_id,
        actor_id=aggregate.owner_id,
        idempotency_key=key,
        expected_case_version=aggregate.version,
        payload=ConfirmFactPayload(
            fact_id="termination.date",
            value="2026-07-20",
        ),
    )


def aggregate_with_candidate(*, owner_id: str = "worker-1") -> CaseAggregate:
    aggregate = CaseAggregate.create(owner_id=owner_id, role_id="worker")
    aggregate.state.facts.items["termination.date"] = FactItem(
        fact_id="termination.date",
        value="2026-07-20",
        status=FactStatus.CLAIMED,
        source=FactSource(kind="user", ref_id="message-1"),
    )
    return aggregate


class RuntimeReliabilityBatchTests(unittest.IsolatedAsyncioTestCase):
    async def test_runtime_records_started_and_exactly_one_terminal_event(self):
        aggregate = aggregate_with_candidate()
        uow = InMemoryCaseUnitOfWork()
        await uow.create_case(aggregate)
        registry = CaseRuntimeRegistry(
            unit_of_work=uow,
            handlers=[ConfirmFactCommandHandler()],
        )
        self.addAsyncCleanup(registry.shutdown, 2)

        runtime = await registry.get_or_create(aggregate.case_id)
        command = confirmation_command(aggregate, key="confirm-once")
        await runtime.submit(command)
        await runtime.wait_idle()
        events = await uow.list_events(aggregate.case_id)
        types = [item.event_type for item in events if item.command_id == command.command_id]

        self.assertEqual(types[0:2], ["command.accepted", "operation.started"])
        self.assertEqual(types[-1], "operation.completed")
        self.assertEqual(
            len(
                [
                    item
                    for item in types
                    if item
                    in {
                        "operation.completed",
                        "operation.failed",
                        "operation.cancelled",
                    }
                ]
            ),
            1,
        )

    async def test_queue_full_rejects_before_command_is_persisted(self):
        aggregate = aggregate_with_candidate()
        uow = InMemoryCaseUnitOfWork()
        await uow.create_case(aggregate)
        runtime = CaseRuntime(
            case_id=aggregate.case_id,
            unit_of_work=uow,
            handlers=[ConfirmFactCommandHandler()],
            queue_capacity=1,
        )
        first = confirmation_command(aggregate, key="queued-first")
        second = confirmation_command(aggregate, key="rejected-second")

        await runtime.submit(first)
        with self.assertRaisesRegex(RuntimeError, "case_busy"):
            await runtime.submit(second)

        events = await uow.list_events(aggregate.case_id)
        second_events = [item for item in events if item.command_id == second.command_id]
        self.assertEqual([item.event_type for item in second_events], ["command.rejected"])
        self.assertEqual(second_events[0].payload["code"], "case_busy")

    async def test_registry_recovers_accepted_command_after_process_restart(self):
        aggregate = aggregate_with_candidate()
        uow = InMemoryCaseUnitOfWork()
        await uow.create_case(aggregate)
        command = confirmation_command(aggregate, key="accepted-before-restart")
        await uow.accept_command(command)
        registry = CaseRuntimeRegistry(
            unit_of_work=uow,
            handlers=[ConfirmFactCommandHandler()],
        )
        self.addAsyncCleanup(registry.shutdown, 2)

        recovered = await registry.recover()
        runtime = await registry.get_or_create(aggregate.case_id)
        await runtime.wait_idle()
        events = await uow.list_events(aggregate.case_id)

        self.assertEqual(recovered, 1)
        self.assertEqual(events[-1].event_type, "operation.completed")

    async def test_registry_fails_interrupted_running_command_without_reexecution(self):
        aggregate = aggregate_with_candidate()
        uow = InMemoryCaseUnitOfWork()
        await uow.create_case(aggregate)
        command = confirmation_command(aggregate, key="running-before-restart")
        await uow.accept_command(command)
        await uow.start_command(command)
        registry = CaseRuntimeRegistry(
            unit_of_work=uow,
            handlers=[ConfirmFactCommandHandler()],
        )
        self.addAsyncCleanup(registry.shutdown, 2)

        recovered = await registry.recover()
        events = await uow.list_events(aggregate.case_id)

        self.assertEqual(recovered, 0)
        self.assertEqual(events[-1].event_type, "operation.failed")
        self.assertEqual(events[-1].payload["code"], "interrupted_operation")
        self.assertEqual((await uow.get_case(aggregate.case_id)).version, 0)

    async def test_registry_evicts_only_idle_case_runtimes(self):
        aggregate = aggregate_with_candidate()
        uow = InMemoryCaseUnitOfWork()
        await uow.create_case(aggregate)
        registry = CaseRuntimeRegistry(
            unit_of_work=uow,
            handlers=[ConfirmFactCommandHandler()],
        )
        self.addAsyncCleanup(registry.shutdown, 2)
        first = await registry.get_or_create(aggregate.case_id)

        evicted = await registry.evict_idle(idle_seconds=0)
        second = await registry.get_or_create(aggregate.case_id)

        self.assertEqual(evicted, 1)
        self.assertIsNot(first, second)


@unittest.skipUnless(
    os.getenv("TEST_DATABASE_URL"),
    "TEST_DATABASE_URL is required for real PostgreSQL integration",
)
class PostgresAtomicityBatchTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_engine(os.environ["TEST_DATABASE_URL"])
        self.uow = PostgresCaseUnitOfWork(create_session_factory(self.engine))

    async def asyncTearDown(self):
        await self.engine.dispose()

    async def test_invalid_patch_rolls_back_state_snapshot_and_batch_events(self):
        aggregate = aggregate_with_candidate(owner_id=f"worker-{uuid4()}")
        await self.uow.create_case(aggregate)
        command = confirmation_command(aggregate, key=f"postgres-{uuid4()}")
        await self.uow.accept_command(command)
        await self.uow.start_command(command)
        invalid_batch = ExecutionBatch(
            patch=CasePatch(
                producer="ControllerAgent",
                base_version=999,
                operations=[SetInteraction(current_goal="不得提交")],
            )
        )

        with self.assertRaisesRegex(ValueError, "base_version"):
            await self.uow.commit_batch(command, invalid_batch)

        refreshed = await self.uow.get_case(aggregate.case_id)
        events = await self.uow.list_events(aggregate.case_id)
        self.assertEqual(refreshed.version, 0)
        self.assertEqual(
            [item.event_type for item in events],
            [
                "command.accepted",
                "operation.started",
                "patch.submitted",
                "patch.rejected",
            ],
        )


if __name__ == "__main__":
    unittest.main()
