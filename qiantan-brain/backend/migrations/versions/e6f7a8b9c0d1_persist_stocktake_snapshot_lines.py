"""persist stocktake snapshot lines and allow pending counts

Revision ID: e6f7a8b9c0d1
Revises: d5e6f7a8b0c1
Create Date: 2026-07-13
"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "e6f7a8b9c0d1"
down_revision: str | None = "d5e6f7a8b0c1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("stocktake_items", schema=None) as batch_op:
        batch_op.alter_column(
            "actual_qty",
            existing_type=sa.Numeric(10, 2),
            nullable=True,
        )
        batch_op.alter_column(
            "variance",
            existing_type=sa.Numeric(10, 2),
            nullable=True,
        )
        batch_op.create_unique_constraint(
            "uq_stocktake_item_session_product",
            ["session_id", "product_id"],
        )


def downgrade() -> None:
    # Old rows were only created after submission; pending snapshot rows cannot be
    # represented by the previous schema, so remove them before restoring NOT NULL.
    op.execute(
        "DELETE FROM stocktake_items WHERE actual_qty IS NULL OR variance IS NULL"
    )
    with op.batch_alter_table("stocktake_items", schema=None) as batch_op:
        batch_op.drop_constraint(
            "uq_stocktake_item_session_product",
            type_="unique",
        )
        batch_op.alter_column(
            "variance",
            existing_type=sa.Numeric(10, 2),
            nullable=False,
        )
        batch_op.alter_column(
            "actual_qty",
            existing_type=sa.Numeric(10, 2),
            nullable=False,
        )
