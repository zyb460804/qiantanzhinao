"""add idempotency_records table for write-request deduplication

Revision ID: k2f3a4b5c6d7
Revises: j1e2f3a4b5c6
Create Date: 2026-07-13

Protects against duplicate write operations (orders, payments, inventory, etc.)
when clients retry due to network issues. Enforces (tenant_id, operation,
idempotency_key) uniqueness with request body hash validation.
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "k2f3a4b5c6d7"
down_revision: str | None = "j1e2f3a4b5c6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "idempotency_records",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("idempotency_key", sa.String(128), nullable=False),
        sa.Column("tenant_id", sa.String(128), nullable=False),
        sa.Column("operation", sa.String(160), nullable=False),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column("response_body", sa.Text(), nullable=True),
        sa.Column("content_type", sa.String(120), nullable=True),
        sa.Column("status_code", sa.Integer(), nullable=False, server_default="102"),
        sa.Column(
            "created_at",
            sa.DateTime(),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.UniqueConstraint(
            "tenant_id", "operation", "idempotency_key",
            name="uq_idempotency_per_tenant_operation",
        ),
    )
    op.create_index(
        "ix_idempotency_records_key", "idempotency_records", ["idempotency_key"]
    )
    op.create_index(
        "ix_idempotency_records_tenant", "idempotency_records", ["tenant_id"]
    )


def downgrade() -> None:
    op.drop_table("idempotency_records")
