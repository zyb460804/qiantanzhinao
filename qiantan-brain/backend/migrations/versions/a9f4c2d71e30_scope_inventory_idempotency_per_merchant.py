"""scope inventory idempotency keys to each merchant

Revision ID: a9f4c2d71e30
Revises: 80bd7e0fc1ac
Create Date: 2026-07-12
"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "a9f4c2d71e30"
down_revision: str | None = "80bd7e0fc1ac"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Allow different merchants to reuse a client-generated idempotency key."""
    with op.batch_alter_table("inventory_records", schema=None) as batch_op:
        batch_op.drop_index("ix_inventory_records_idempotency_key")
        batch_op.create_index(
            "ix_inventory_records_idempotency_key",
            ["idempotency_key"],
            unique=False,
        )
        batch_op.create_unique_constraint(
            "uq_inventory_idempotency_per_merchant",
            ["merchant_id", "idempotency_key"],
        )


def downgrade() -> None:
    """Restore the former global idempotency-key uniqueness."""
    with op.batch_alter_table("inventory_records", schema=None) as batch_op:
        batch_op.drop_constraint(
            "uq_inventory_idempotency_per_merchant", type_="unique"
        )
        batch_op.drop_index("ix_inventory_records_idempotency_key")
        batch_op.create_index(
            "ix_inventory_records_idempotency_key",
            ["idempotency_key"],
            unique=True,
        )
