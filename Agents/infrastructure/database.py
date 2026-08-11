from __future__ import annotations

import os
from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class CaseRow(Base):
    __tablename__ = "cases"

    case_id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    owner_id: Mapped[str] = mapped_column(String(200), index=True)
    role_id: Mapped[str] = mapped_column(String(50))
    version: Mapped[int] = mapped_column(Integer, default=0)
    stage: Mapped[str] = mapped_column(String(50))
    next_sequence: Mapped[int] = mapped_column(BigInteger, default=1)
    state_json: Mapped[dict[str, Any]] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class CaseCommandRow(Base):
    __tablename__ = "case_commands"
    __table_args__ = (
        UniqueConstraint("case_id", "idempotency_key", name="uq_case_command_idempotency"),
        Index("ix_case_commands_status_created", "status", "created_at"),
        Index("ix_case_commands_case_status", "case_id", "status"),
        CheckConstraint(
            "status IN ('accepted', 'running', 'completed', 'failed', 'cancelled')",
            name="ck_case_commands_status",
        ),
    )

    command_id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    case_id: Mapped[UUID] = mapped_column(
        Uuid,
        ForeignKey("cases.case_id", ondelete="CASCADE"),
        index=True,
    )
    idempotency_key: Mapped[str] = mapped_column(String(200))
    actor_id: Mapped[str] = mapped_column(String(200))
    command_type: Mapped[str] = mapped_column(String(80))
    command_json: Mapped[dict[str, Any]] = mapped_column(JSONB)
    status: Mapped[str] = mapped_column(String(30))
    error_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class CaseEventRow(Base):
    __tablename__ = "case_events"
    __table_args__ = (
        UniqueConstraint("case_id", "sequence", name="uq_case_event_sequence"),
        Index("ix_case_events_command", "command_id"),
    )

    event_id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    case_id: Mapped[UUID] = mapped_column(
        Uuid,
        ForeignKey("cases.case_id", ondelete="CASCADE"),
        index=True,
    )
    command_id: Mapped[UUID] = mapped_column(Uuid)
    sequence: Mapped[int] = mapped_column(BigInteger)
    event_type: Mapped[str] = mapped_column(String(100))
    visibility: Mapped[str] = mapped_column(String(20))
    envelope_json: Mapped[dict[str, Any]] = mapped_column(JSONB)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class CaseSnapshotRow(Base):
    __tablename__ = "case_snapshots"
    __table_args__ = (
        UniqueConstraint("case_id", "version", name="uq_case_snapshot_version"),
    )

    snapshot_id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    case_id: Mapped[UUID] = mapped_column(
        Uuid,
        ForeignKey("cases.case_id", ondelete="CASCADE"),
        index=True,
    )
    version: Mapped[int] = mapped_column(Integer)
    schema_version: Mapped[str] = mapped_column(String(20))
    state_json: Mapped[dict[str, Any]] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class MessageRow(Base):
    __tablename__ = "messages"

    message_id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    case_id: Mapped[UUID] = mapped_column(
        Uuid,
        ForeignKey("cases.case_id", ondelete="CASCADE"),
        index=True,
    )
    command_id: Mapped[UUID] = mapped_column(Uuid, index=True)
    event_id: Mapped[UUID] = mapped_column(Uuid, index=True)
    speaker: Mapped[str] = mapped_column(String(100))
    content: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class EvidenceFileRow(Base):
    __tablename__ = "evidence_files"

    evidence_id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    case_id: Mapped[UUID] = mapped_column(
        Uuid,
        ForeignKey("cases.case_id", ondelete="CASCADE"),
        index=True,
    )
    storage_key: Mapped[str] = mapped_column(String(500), unique=True)
    display_name: Mapped[str] = mapped_column(String(500))
    media_type: Mapped[str] = mapped_column(String(200))
    sha256: Mapped[str] = mapped_column(String(64), index=True)
    size: Mapped[int] = mapped_column(BigInteger)
    status: Mapped[str] = mapped_column(String(30))
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class ArtifactRow(Base):
    __tablename__ = "artifacts"

    artifact_id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    case_id: Mapped[UUID] = mapped_column(
        Uuid,
        ForeignKey("cases.case_id", ondelete="CASCADE"),
        index=True,
    )
    artifact_type: Mapped[str] = mapped_column(String(100))
    title: Mapped[str] = mapped_column(String(500))
    stale: Mapped[bool]
    revisions_json: Mapped[list[dict[str, Any]]] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


def create_engine(database_url: str | None = None) -> AsyncEngine:
    url = (database_url or os.getenv("DATABASE_URL") or "").strip()
    if not url:
        raise RuntimeError("DATABASE_URL is required")
    if not url.startswith("postgresql+asyncpg://"):
        raise RuntimeError("DATABASE_URL must use postgresql+asyncpg")
    return create_async_engine(url, pool_pre_ping=True)


def create_session_factory(
    engine: AsyncEngine,
) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False)
