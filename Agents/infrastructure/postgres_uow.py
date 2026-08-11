from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from Agents.application.handlers.base import ExecutionBatch
from Agents.domain.case_state import CaseAggregate, utc_now
from Agents.domain.commands import CancelOperationPayload, CaseCommand
from Agents.domain.events import EventDraft, EventEnvelope
from Agents.domain.errors import PatchRejectedError, error_payload
from Agents.domain.state_manager import DomainStateManager
from Agents.domain.snapshot_migrations import load_case_aggregate

from .database import (
    ArtifactRow,
    CaseCommandRow,
    CaseEventRow,
    CaseRow,
    CaseSnapshotRow,
    EvidenceFileRow,
    MessageRow,
)
from .uow import AcceptCommandResult


class PostgresCaseUnitOfWork:
    """Postgres implementation with row-locked, atomic state/event commits."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        state_manager: DomainStateManager | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._state_manager = state_manager or DomainStateManager()

    async def create_case(self, aggregate: CaseAggregate) -> None:
        async with self._session_factory.begin() as session:
            if await session.get(CaseRow, aggregate.case_id) is not None:
                raise ValueError(f"case already exists: {aggregate.case_id}")
            session.add(
                CaseRow(
                    case_id=aggregate.case_id,
                    owner_id=aggregate.owner_id,
                    role_id=aggregate.role_id,
                    version=aggregate.version,
                    stage=aggregate.state.interaction.stage.value,
                    next_sequence=1,
                    state_json=aggregate.model_dump(mode="json"),
                    created_at=aggregate.created_at,
                    updated_at=aggregate.updated_at,
                )
            )
            session.add(
                CaseSnapshotRow(
                    case_id=aggregate.case_id,
                    version=aggregate.version,
                    schema_version=aggregate.schema_version,
                    state_json=aggregate.model_dump(mode="json"),
                    created_at=aggregate.created_at,
                )
            )

    async def get_case(self, case_id: UUID) -> CaseAggregate:
        async with self._session_factory() as session:
            row = await session.get(CaseRow, case_id)
            if row is None:
                raise KeyError(f"case not found: {case_id}")
            return load_case_aggregate(row.state_json)

    async def list_cases(self, owner_id: str) -> list[CaseAggregate]:
        async with self._session_factory() as session:
            rows = (
                await session.scalars(
                    select(CaseRow)
                    .where(CaseRow.owner_id == owner_id)
                    .order_by(CaseRow.updated_at.desc())
                )
            ).all()
            return [load_case_aggregate(row.state_json) for row in rows]

    async def accept_command(self, command: CaseCommand) -> AcceptCommandResult:
        async with self._session_factory.begin() as session:
            case_row = await self._locked_case(session, command.case_id)
            if case_row.owner_id != command.actor_id:
                raise PermissionError("actor does not own case")
            existing_row = await session.scalar(
                select(CaseCommandRow).where(
                    CaseCommandRow.case_id == command.case_id,
                    CaseCommandRow.idempotency_key == command.idempotency_key,
                )
            )
            if existing_row is not None:
                existing = CaseCommand.model_validate(existing_row.command_json)
                events = await self._events_for_command(session, existing.command_id)
                return AcceptCommandResult(existing, False, events)
            if (
                not isinstance(command.payload, CancelOperationPayload)
                and command.expected_case_version != case_row.version
            ):
                raise ValueError(
                    f"expected_case_version={command.expected_case_version} does not "
                    f"match case version={case_row.version}"
                )
            session.add(
                CaseCommandRow(
                    command_id=command.command_id,
                    case_id=command.case_id,
                    idempotency_key=command.idempotency_key,
                    actor_id=command.actor_id,
                    command_type=command.command_type,
                    command_json=command.model_dump(mode="json"),
                    status="accepted",
                    error_json=None,
                    created_at=command.created_at,
                    started_at=None,
                    completed_at=None,
                )
            )
            event = self._append_event(
                session,
                case_row,
                command,
                EventDraft(
                    event_type="command.accepted",
                    producer="CaseCommandService",
                    visibility="user",
                    payload={"command_type": command.command_type},
                ),
                case_version=case_row.version,
            )
            return AcceptCommandResult(command, True, [event])

    async def reject_command(
        self,
        command: CaseCommand,
        error: Exception,
    ) -> EventEnvelope:
        async with self._session_factory.begin() as session:
            case_row = await self._locked_case(session, command.case_id)
            return self._append_event(
                session,
                case_row,
                command,
                EventDraft(
                    event_type="command.rejected",
                    producer="CaseRuntime",
                    visibility="user",
                    payload=error_payload(error),
                ),
                case_version=case_row.version,
            )

    async def find_command(
        self,
        case_id: UUID,
        idempotency_key: str,
    ) -> AcceptCommandResult | None:
        async with self._session_factory() as session:
            row = await session.scalar(
                select(CaseCommandRow).where(
                    CaseCommandRow.case_id == case_id,
                    CaseCommandRow.idempotency_key == idempotency_key,
                )
            )
            if row is None:
                return None
            command = CaseCommand.model_validate(row.command_json)
            events = await self._events_for_command(session, command.command_id)
            return AcceptCommandResult(command, False, events)

    async def start_command(self, command: CaseCommand) -> EventEnvelope:
        async with self._session_factory.begin() as session:
            case_row = await self._locked_case(session, command.case_id)
            command_row = await session.get(CaseCommandRow, command.command_id)
            if command_row is None:
                raise KeyError(f"command not found: {command.command_id}")
            if command_row.status == "running":
                return await self._event_by_type(
                    session,
                    command.command_id,
                    "operation.started",
                )
            if command_row.status != "accepted":
                raise ValueError(
                    f"command cannot start from status={command_row.status}"
                )
            command_row.status = "running"
            command_row.started_at = utc_now()
            return self._append_event(
                session,
                case_row,
                command,
                EventDraft(
                    event_type="operation.started",
                    producer="CaseRuntime",
                    visibility="user",
                    payload={"status": "running"},
                ),
                case_version=case_row.version,
            )

    async def list_recoverable_commands(self) -> list[CaseCommand]:
        return await self._commands_with_status("accepted")

    async def list_interrupted_commands(self) -> list[CaseCommand]:
        return await self._commands_with_status("running")

    async def _commands_with_status(self, status: str) -> list[CaseCommand]:
        async with self._session_factory() as session:
            rows = (
                await session.scalars(
                    select(CaseCommandRow)
                    .where(CaseCommandRow.status == status)
                    .order_by(CaseCommandRow.created_at)
                )
            ).all()
            return [CaseCommand.model_validate(row.command_json) for row in rows]

    async def commit_batch(
        self,
        command: CaseCommand,
        batch: ExecutionBatch,
    ) -> list[EventEnvelope]:
        rejection: PatchRejectedError | None = None
        async with self._session_factory.begin() as session:
            case_row = await self._locked_case(session, command.case_id)
            aggregate = load_case_aggregate(case_row.state_json)
            committed: list[EventEnvelope] = []
            if batch.patch is not None:
                committed.append(
                    self._append_event(
                        session,
                        case_row,
                        command,
                        EventDraft(
                            event_type="patch.submitted",
                            producer=batch.patch.producer,
                            visibility="internal",
                            payload={"patch_id": str(batch.patch.patch_id)},
                        ),
                        case_version=aggregate.version,
                    )
                )
                result = self._state_manager.apply_patch(aggregate, batch.patch)
                if not result.accepted:
                    committed.append(
                        self._append_event(
                            session,
                            case_row,
                            command,
                            EventDraft(
                                event_type="patch.rejected",
                                producer="StateManager",
                                visibility="user",
                                payload={
                                    "patch_id": str(batch.patch.patch_id),
                                    "errors": result.errors,
                                },
                            ),
                            case_version=aggregate.version,
                        )
                    )
                    rejection = PatchRejectedError(result.errors, committed)
                else:
                    case_row.version = aggregate.version
                    case_row.stage = aggregate.state.interaction.stage.value
                    case_row.state_json = aggregate.model_dump(mode="json")
                    case_row.updated_at = aggregate.updated_at
                    session.add(
                        CaseSnapshotRow(
                            case_id=aggregate.case_id,
                            version=aggregate.version,
                            schema_version=aggregate.schema_version,
                            state_json=aggregate.model_dump(mode="json"),
                            created_at=aggregate.updated_at,
                        )
                    )
                    await self._sync_projections(session, aggregate)
                    committed.append(
                        self._append_event(
                            session,
                            case_row,
                            command,
                            EventDraft(
                                event_type="patch.applied",
                                producer="StateManager",
                                visibility="internal",
                                payload={
                                    "patch_id": str(batch.patch.patch_id),
                                    "new_version": aggregate.version,
                                },
                            ),
                            case_version=aggregate.version,
                        )
                    )
            for draft in batch.events if rejection is None else []:
                envelope = self._append_event(
                        session,
                        case_row,
                        command,
                        draft,
                        case_version=aggregate.version,
                    )
                committed.append(envelope)
                if draft.event_type == "message.received":
                    session.add(
                        MessageRow(
                            case_id=command.case_id,
                            command_id=command.command_id,
                            event_id=envelope.event_id,
                            speaker=command.actor_id,
                            content=str(draft.payload.get("text", "")),
                            created_at=envelope.occurred_at,
                        )
                    )
        if rejection is not None:
            raise rejection
        return committed

    async def _sync_projections(
        self,
        session: AsyncSession,
        aggregate: CaseAggregate,
    ) -> None:
        for evidence in aggregate.state.evidence.items.values():
            await session.merge(
                EvidenceFileRow(
                    evidence_id=evidence.evidence_id,
                    case_id=aggregate.case_id,
                    storage_key=evidence.storage_key,
                    display_name=evidence.display_name,
                    media_type=evidence.media_type,
                    sha256=evidence.sha256,
                    size=evidence.size,
                    status=evidence.status.value,
                    metadata_json=evidence.model_dump(mode="json"),
                    created_at=aggregate.created_at,
                    updated_at=aggregate.updated_at,
                )
            )
        for artifact in aggregate.state.outputs.artifacts.values():
            await session.merge(
                ArtifactRow(
                    artifact_id=artifact.artifact_id,
                    case_id=aggregate.case_id,
                    artifact_type=artifact.artifact_type,
                    title=artifact.title,
                    stale=artifact.stale,
                    revisions_json=[
                        revision.model_dump(mode="json")
                        for revision in artifact.revisions
                    ],
                    created_at=artifact.revisions[0].created_at,
                    updated_at=artifact.revisions[-1].created_at,
                )
            )

    async def finish_command(
        self,
        command: CaseCommand,
        *,
        error: Exception | None = None,
        cancelled: bool = False,
    ) -> EventEnvelope:
        async with self._session_factory.begin() as session:
            case_row = await self._locked_case(session, command.case_id)
            command_row = await session.get(CaseCommandRow, command.command_id)
            if command_row is None:
                raise KeyError(f"command not found: {command.command_id}")
            if command_row.status in {"completed", "failed", "cancelled"}:
                return await self._event_by_type(
                    session,
                    command.command_id,
                    {
                        "completed": "operation.completed",
                        "failed": "operation.failed",
                        "cancelled": "operation.cancelled",
                    }[command_row.status],
                )
            command_row.status = (
                "cancelled" if cancelled else "failed" if error else "completed"
            )
            command_row.completed_at = utc_now()
            command_row.error_json = (
                error_payload(error)
                if error
                else None
            )
            return self._append_event(
                session,
                case_row,
                command,
                EventDraft(
                    event_type=(
                        "operation.cancelled"
                        if cancelled
                        else "operation.failed"
                        if error
                        else "operation.completed"
                    ),
                    producer="CaseRuntime",
                    visibility="user",
                    payload=command_row.error_json
                    or {"status": "cancelled" if cancelled else "completed"},
                ),
                case_version=case_row.version,
            )

    async def list_events(
        self,
        case_id: UUID,
        *,
        after_sequence: int = 0,
        user_visible_only: bool = False,
    ) -> list[EventEnvelope]:
        async with self._session_factory() as session:
            query = (
                select(CaseEventRow)
                .where(
                    CaseEventRow.case_id == case_id,
                    CaseEventRow.sequence > after_sequence,
                )
                .order_by(CaseEventRow.sequence)
            )
            if user_visible_only:
                query = query.where(CaseEventRow.visibility == "user")
            rows = (await session.scalars(query)).all()
            return [EventEnvelope.model_validate(row.envelope_json) for row in rows]

    async def _locked_case(self, session: AsyncSession, case_id: UUID) -> CaseRow:
        row = await session.scalar(
            select(CaseRow).where(CaseRow.case_id == case_id).with_for_update()
        )
        if row is None:
            raise KeyError(f"case not found: {case_id}")
        return row

    async def _events_for_command(
        self,
        session: AsyncSession,
        command_id: UUID,
    ) -> list[EventEnvelope]:
        rows = (
            await session.scalars(
                select(CaseEventRow)
                .where(CaseEventRow.command_id == command_id)
                .order_by(CaseEventRow.sequence)
            )
        ).all()
        return [EventEnvelope.model_validate(row.envelope_json) for row in rows]

    async def _event_by_type(
        self,
        session: AsyncSession,
        command_id: UUID,
        event_type: str,
    ) -> EventEnvelope:
        row = await session.scalar(
            select(CaseEventRow)
            .where(
                CaseEventRow.command_id == command_id,
                CaseEventRow.event_type == event_type,
            )
            .order_by(CaseEventRow.sequence.desc())
            .limit(1)
        )
        if row is None:
            raise RuntimeError(
                f"command status references missing event: {command_id} {event_type}"
            )
        return EventEnvelope.model_validate(row.envelope_json)

    def _append_event(
        self,
        session: AsyncSession,
        case_row: CaseRow,
        command: CaseCommand,
        draft: EventDraft,
        *,
        case_version: int,
    ) -> EventEnvelope:
        envelope = EventEnvelope(
            sequence=case_row.next_sequence,
            case_id=command.case_id,
            command_id=command.command_id,
            correlation_id=command.correlation_id,
            causation_id=draft.causation_id,
            case_version=case_version,
            event_type=draft.event_type,
            producer=draft.producer,
            visibility=draft.visibility,
            payload=draft.payload,
        )
        case_row.next_sequence += 1
        session.add(
            CaseEventRow(
                event_id=envelope.event_id,
                case_id=envelope.case_id,
                command_id=envelope.command_id,
                sequence=envelope.sequence,
                event_type=envelope.event_type,
                visibility=envelope.visibility,
                envelope_json=envelope.model_dump(mode="json"),
                occurred_at=envelope.occurred_at,
            )
        )
        return envelope
