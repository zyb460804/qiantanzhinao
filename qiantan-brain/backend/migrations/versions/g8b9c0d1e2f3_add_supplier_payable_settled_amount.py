"""add supplier payable settled amount for targeted allocation

Revision ID: g8b9c0d1e2f3
Revises: f7a8b9c0d1e2
Create Date: 2026-07-13
"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "g8b9c0d1e2f3"
down_revision: str | None = "f7a8b9c0d1e2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("supplier_payables", schema=None) as batch_op:
        batch_op.add_column(sa.Column(
            "settled_amount", sa.Numeric(12, 2), nullable=False,
            server_default=sa.text("0"),
        ))
        batch_op.alter_column("settled_amount", server_default=None)


def downgrade() -> None:
    with op.batch_alter_table("supplier_payables", schema=None) as batch_op:
        batch_op.drop_column("settled_amount")
