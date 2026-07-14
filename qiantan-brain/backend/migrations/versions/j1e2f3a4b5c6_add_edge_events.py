"""add edge_events table for edge device event persistence

Revision ID: j1e2f3a4b5c6
Revises: i0d1e2f3a4b5
Create Date: 2026-07-13

Provides idempotent persistent storage for edge device ingest events
with event_id dedup, merchant/tenant isolation, and time-series indexing.
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "j1e2f3a4b5c6"
down_revision: str | None = "i0d1e2f3a4b5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "edge_events",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("event_id", sa.String(64), nullable=False),
        sa.Column("device_id", sa.String(64), nullable=True),
        sa.Column(
            "merchant_id",
            sa.Uuid(),
            sa.ForeignKey("merchants.id"),
            nullable=False,
        ),
        sa.Column(
            "tenant_id",
            sa.Uuid(),
            sa.ForeignKey("tenants.id"),
            nullable=True,
        ),
        sa.Column("event_type", sa.String(30), nullable=False),
        sa.Column("occurred_at", sa.DateTime(), nullable=False),
        sa.Column("payload", sa.Text(), nullable=True),
        sa.Column("model_version", sa.String(30), nullable=True),
        sa.Column("sequence", sa.Integer(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.UniqueConstraint("event_id", name="uq_edge_events_event_id"),
    )
    op.create_index("ix_edge_events_event_id", "edge_events", ["event_id"])
    op.create_index("ix_edge_events_device_id", "edge_events", ["device_id"])
    op.create_index("ix_edge_events_merchant_id", "edge_events", ["merchant_id"])
    op.create_index("ix_edge_events_tenant_id", "edge_events", ["tenant_id"])
    op.create_index(
        "ix_edge_events_merchant_occurred",
        "edge_events",
        ["merchant_id", "occurred_at"],
    )


def downgrade() -> None:
    op.drop_table("edge_events")
