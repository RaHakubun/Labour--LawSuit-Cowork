"""create asynchronous case runtime tables

Revision ID: 20260731_0001
Revises:
Create Date: 2026-07-31
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "20260731_0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "cases",
        sa.Column("case_id", sa.Uuid(), primary_key=True),
        sa.Column("owner_id", sa.String(200), nullable=False),
        sa.Column("role_id", sa.String(50), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("stage", sa.String(50), nullable=False),
        sa.Column("next_sequence", sa.BigInteger(), nullable=False),
        sa.Column("state_json", postgresql.JSONB(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_cases_owner_id", "cases", ["owner_id"])
    op.create_table(
        "case_commands",
        sa.Column("command_id", sa.Uuid(), primary_key=True),
        sa.Column(
            "case_id",
            sa.Uuid(),
            sa.ForeignKey("cases.case_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("idempotency_key", sa.String(200), nullable=False),
        sa.Column("actor_id", sa.String(200), nullable=False),
        sa.Column("command_type", sa.String(80), nullable=False),
        sa.Column("command_json", postgresql.JSONB(), nullable=False),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("error_json", postgresql.JSONB(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint(
            "case_id",
            "idempotency_key",
            name="uq_case_command_idempotency",
        ),
    )
    op.create_index("ix_case_commands_case_id", "case_commands", ["case_id"])
    op.create_index(
        "ix_case_commands_status_created",
        "case_commands",
        ["status", "created_at"],
    )
    op.create_table(
        "case_events",
        sa.Column("event_id", sa.Uuid(), primary_key=True),
        sa.Column(
            "case_id",
            sa.Uuid(),
            sa.ForeignKey("cases.case_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("command_id", sa.Uuid(), nullable=False),
        sa.Column("sequence", sa.BigInteger(), nullable=False),
        sa.Column("event_type", sa.String(100), nullable=False),
        sa.Column("visibility", sa.String(20), nullable=False),
        sa.Column("envelope_json", postgresql.JSONB(), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("case_id", "sequence", name="uq_case_event_sequence"),
    )
    op.create_index("ix_case_events_case_id", "case_events", ["case_id"])
    op.create_index("ix_case_events_command", "case_events", ["command_id"])
    op.create_table(
        "case_snapshots",
        sa.Column("snapshot_id", sa.Uuid(), primary_key=True),
        sa.Column(
            "case_id",
            sa.Uuid(),
            sa.ForeignKey("cases.case_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("schema_version", sa.String(20), nullable=False),
        sa.Column("state_json", postgresql.JSONB(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("case_id", "version", name="uq_case_snapshot_version"),
    )
    op.create_index("ix_case_snapshots_case_id", "case_snapshots", ["case_id"])
    _create_projection_tables()


def _create_projection_tables() -> None:
    op.create_table(
        "messages",
        sa.Column("message_id", sa.Uuid(), primary_key=True),
        sa.Column(
            "case_id",
            sa.Uuid(),
            sa.ForeignKey("cases.case_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("command_id", sa.Uuid(), nullable=False),
        sa.Column("event_id", sa.Uuid(), nullable=False),
        sa.Column("speaker", sa.String(100), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_messages_case_id", "messages", ["case_id"])
    op.create_index("ix_messages_command_id", "messages", ["command_id"])
    op.create_index("ix_messages_event_id", "messages", ["event_id"])
    op.create_table(
        "evidence_files",
        sa.Column("evidence_id", sa.Uuid(), primary_key=True),
        sa.Column(
            "case_id",
            sa.Uuid(),
            sa.ForeignKey("cases.case_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("storage_key", sa.String(500), nullable=False, unique=True),
        sa.Column("display_name", sa.String(500), nullable=False),
        sa.Column("media_type", sa.String(200), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("size", sa.BigInteger(), nullable=False),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("metadata_json", postgresql.JSONB(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_evidence_files_case_id", "evidence_files", ["case_id"])
    op.create_index("ix_evidence_files_sha256", "evidence_files", ["sha256"])
    op.create_table(
        "artifacts",
        sa.Column("artifact_id", sa.Uuid(), primary_key=True),
        sa.Column(
            "case_id",
            sa.Uuid(),
            sa.ForeignKey("cases.case_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("artifact_type", sa.String(100), nullable=False),
        sa.Column("title", sa.String(500), nullable=False),
        sa.Column("stale", sa.Boolean(), nullable=False),
        sa.Column("revisions_json", postgresql.JSONB(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_artifacts_case_id", "artifacts", ["case_id"])


def downgrade() -> None:
    for table in (
        "artifacts",
        "evidence_files",
        "messages",
        "case_snapshots",
        "case_events",
        "case_commands",
        "cases",
    ):
        op.drop_table(table)
