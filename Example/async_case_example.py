"""Minimal in-process example for the asynchronous case runtime."""

import asyncio

from Agents.application.command_service import CaseCommandService
from Agents.application.decisions import AskClarificationDecision
from Agents.application.handlers.intake import ControllerCommandHandler
from Agents.application.handlers.scenario import ScenarioStageHandler
from Agents.application.scenario_models import ScenarioResult
from Agents.domain.commands import SubmitUserMessagePayload
from Agents.infrastructure.memory_uow import InMemoryCaseUnitOfWork
from Agents.runtime.registry import CaseRuntimeRegistry
from Agents.services.tool_hub import AuthorityToolResult, ToolHub


class ExampleController:
    async def decide(self, *, case_state, user_input):
        return AskClarificationDecision(
            question="请补充解除日期、月工资和是否收到书面解除通知。",
            required_fact_ids=[
                "termination.date",
                "employment.monthly_wage",
                "termination.written_notice",
            ],
        )


class ExampleScenario:
    async def analyze(self, *, role_id, scene_id, case_state):
        return ScenarioResult(
            scene_id=scene_id,
            confidence=0.5,
            missing_fact_questions=["请补充劳动合同。"],
            summary="等待补充材料。",
        )


class ExampleAuthorityAdapter:
    async def search(self, request):
        return AuthorityToolResult(
            tool_name=request.tool_name,
            normalized_query=request.query,
            attempts=1,
            documents=(),
        )


async def main() -> None:
    unit_of_work = InMemoryCaseUnitOfWork()
    scenario = ScenarioStageHandler(
        scenario_provider=ExampleScenario(),
        tool_hub=ToolHub(ExampleAuthorityAdapter()),
    )
    registry = CaseRuntimeRegistry(
        unit_of_work=unit_of_work,
        handlers=[ControllerCommandHandler(ExampleController(), scenario)],
    )
    service = CaseCommandService(
        unit_of_work=unit_of_work,
        runtime_registry=registry,
    )
    case = await service.create_case(owner_id="example-user", role_id="worker")
    await service.submit(
        case_id=case.case_id,
        actor_id="example-user",
        idempotency_key="example-message-1",
        expected_case_version=0,
        payload=SubmitUserMessagePayload(text="公司今天口头辞退我。"),
    )
    runtime = await registry.get_or_create(case.case_id)
    await runtime.wait_idle()
    for event in await unit_of_work.list_events(case.case_id, user_visible_only=True):
        print(event.sequence, event.event_type, event.payload)
    await registry.shutdown()


if __name__ == "__main__":
    asyncio.run(main())
