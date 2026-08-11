from __future__ import annotations

from collections.abc import AsyncIterator, Sequence

from Agents.application.decisions import (
    ContinueCurrentStageDecision,
    ControllerDecision,
    RequestAnalysisDecision,
    RequestDocumentDecision,
    RequestFactConfirmationDecision,
)
from Agents.application.handlers.base import CommandHandler, ExecutionBatch
from Agents.domain.case_state import CaseAggregate
from Agents.domain.commands import (
    CalculateRulePayload,
    CancelOperationPayload,
    CaseCommand,
    ConfirmFactPayload,
    RegisterEvidencePayload,
    RequestAnalysisPayload,
    RequestDocumentPayload,
    SubmitUserMessagePayload,
)


class CaseOrchestrator:
    """Single application entrypoint that normalizes commands before delegation."""

    def __init__(
        self,
        *,
        controller_handler: CommandHandler,
        stage_handlers: Sequence[CommandHandler],
    ) -> None:
        self._controller_handler = controller_handler
        self._stage_handlers = tuple(stage_handlers)

    def supports(self, command_type: str) -> bool:
        return command_type in {
            "submit_user_message",
            "register_evidence",
            "confirm_fact",
            "calculate_rule",
            "request_analysis",
            "request_document",
            "cancel_operation",
        }

    async def execute(
        self,
        command: CaseCommand,
        aggregate: CaseAggregate,
    ) -> AsyncIterator[ExecutionBatch]:
        if isinstance(command.payload, SubmitUserMessagePayload):
            handler = self._controller_handler
        else:
            self.decision_for_command(command)
            handler = self._resolve_stage_handler(command.command_type)
        async for batch in handler.execute(command, aggregate):
            yield batch

    @staticmethod
    def decision_for_command(command: CaseCommand) -> ControllerDecision:
        payload = command.payload
        if isinstance(payload, ConfirmFactPayload):
            return RequestFactConfirmationDecision(
                fact_ids=[payload.fact_id],
                reason="用户提交了显式事实确认命令",
            )
        if isinstance(payload, RequestAnalysisPayload):
            return RequestAnalysisDecision(reason="用户显式请求法律分析")
        if isinstance(payload, RequestDocumentPayload):
            return RequestDocumentDecision(
                document_type=payload.document_type,
                reason="用户显式请求生成文书",
            )
        if isinstance(
            payload,
            (CalculateRulePayload, RegisterEvidencePayload, CancelOperationPayload),
        ):
            return ContinueCurrentStageDecision(
                reason=f"继续执行显式能力：{payload.command_type}"
            )
        raise ValueError(
            f"submit_user_message requires a Controller provider decision: {command.command_id}"
        )

    def _resolve_stage_handler(self, command_type: str) -> CommandHandler:
        matches = [
            handler for handler in self._stage_handlers if handler.supports(command_type)
        ]
        if len(matches) != 1:
            raise RuntimeError(
                f"expected exactly one stage handler for {command_type}, got {len(matches)}"
            )
        return matches[0]
