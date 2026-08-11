"""store encrypted external integration credentials

Revision ID: 20260812_0003
Revises: 20260811_0002
Create Date: 2026-08-12
"""

from alembic import op
import sqlalchemy as sa


revision = "20260812_0003"
down_revision = "20260811_0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "integration_credentials",
        sa.Column("provider", sa.String(20), primary_key=True),
        sa.Column("endpoint", sa.String(1000), nullable=True),
        sa.Column("model", sa.String(500), nullable=True),
        sa.Column("encrypted_secret", sa.LargeBinary(), nullable=False),
        sa.Column("nonce", sa.LargeBinary(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "provider IN ('llm', 'ocr', 'mcp')",
            name="ck_integration_credentials_provider",
        ),
    )


def downgrade() -> None:
    op.drop_table("integration_credentials")
