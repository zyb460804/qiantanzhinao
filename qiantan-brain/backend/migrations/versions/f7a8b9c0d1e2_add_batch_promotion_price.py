"""add batch-level near-expiry promotion pricing

Revision ID: f7a8b9c0d1e2
Revises: e6f7a8b9c0d1
Create Date: 2026-07-13
"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "f7a8b9c0d1e2"
down_revision: str | None = "e6f7a8b9c0d1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("batch_lifecycles", schema=None) as batch_op:
        batch_op.add_column(sa.Column("promotion_price", sa.Numeric(10, 2), nullable=True))
        batch_op.add_column(sa.Column("promotion_start_at", sa.DateTime(), nullable=True))
        batch_op.add_column(sa.Column("promotion_end_at", sa.DateTime(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("batch_lifecycles", schema=None) as batch_op:
        batch_op.drop_column("promotion_end_at")
        batch_op.drop_column("promotion_start_at")
        batch_op.drop_column("promotion_price")
