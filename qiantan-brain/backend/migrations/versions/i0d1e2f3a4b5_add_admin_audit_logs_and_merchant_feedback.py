"""add admin_audit_logs and merchant_feedback tables

Revision ID: i0d1e2f3a4b5
Revises: h9c0d1e2f3a4
Create Date: 2026-07-13

- admin_audit_logs: platform admin operation audit trail
- merchant_feedback: miniprogram user feedback
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "i0d1e2f3a4b5"
down_revision: str | None = "h9c0d1e2f3a4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # ── admin_audit_logs ──
    op.create_table(
        "admin_audit_logs",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("admin_id", sa.String(36), nullable=False),
        sa.Column("admin_email", sa.String(200), nullable=False),
        sa.Column("action", sa.String(50), nullable=False),
        sa.Column("resource_type", sa.String(50), nullable=True),
        sa.Column("resource_id", sa.String(36), nullable=True),
        sa.Column("detail", sa.Text(), nullable=True),
        sa.Column("ip_address", sa.String(45), nullable=True),
        sa.Column("user_agent", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
    )
    op.create_index("ix_admin_audit_logs_admin_id", "admin_audit_logs", ["admin_id"])
    op.create_index("ix_admin_audit_logs_action", "admin_audit_logs", ["action"])
    op.create_index(
        "ix_admin_audit_logs_created_at", "admin_audit_logs", ["created_at"]
    )

    # ── merchant_feedback ──
    op.create_table(
        "merchant_feedback",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "merchant_id",
            sa.Uuid(),
            sa.ForeignKey("merchants.id"),
            nullable=False,
        ),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("page", sa.String(100), nullable=True),
        sa.Column("app_version", sa.String(20), nullable=True),
        sa.Column("created_at", sa.Date(), server_default=sa.text("(CURRENT_DATE)"), nullable=False),
    )
    op.create_index(
        "ix_merchant_feedback_merchant_id",
        "merchant_feedback",
        ["merchant_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_merchant_feedback_merchant_id", table_name="merchant_feedback")
    op.drop_table("merchant_feedback")
    op.drop_index("ix_admin_audit_logs_created_at", table_name="admin_audit_logs")
    op.drop_index("ix_admin_audit_logs_action", table_name="admin_audit_logs")
    op.drop_index("ix_admin_audit_logs_admin_id", table_name="admin_audit_logs")
    op.drop_table("admin_audit_logs")
