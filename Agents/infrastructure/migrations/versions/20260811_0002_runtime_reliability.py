"""enforce runtime command lifecycle

Revision ID: 20260811_0002
Revises: 20260731_0001
Create Date: 2026-08-11
"""

from alembic import op


revision = "20260811_0002"
down_revision = "20260731_0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_check_constraint(
        "ck_case_commands_status",
        "case_commands",
        "status IN ('accepted', 'running', 'completed', 'failed', 'cancelled')",
    )
    op.create_index(
        "ix_case_commands_case_status",
        "case_commands",
        ["case_id", "status"],
    )


def downgrade() -> None:
    op.drop_index("ix_case_commands_case_status", table_name="case_commands")
    op.drop_constraint(
        "ck_case_commands_status",
        "case_commands",
        type_="check",
    )
