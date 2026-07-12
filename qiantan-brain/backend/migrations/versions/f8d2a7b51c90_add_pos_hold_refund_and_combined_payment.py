"""add POS hold, refund, and combined-payment support columns

Revision ID: f8d2a7b51c90
Revises: e7b1a4c92f60
Create Date: 2026-07-12
"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "f8d2a7b51c90"
down_revision: str | None = "e7b1a4c92f60"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # --- sale_orders: 挂单 + 退款字段 ---
    with op.batch_alter_table("sale_orders", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("refunded_amount", sa.Numeric(12, 2), nullable=False, server_default="0"),
        )
        batch_op.add_column(
            sa.Column("held_at", sa.DateTime(), nullable=True),
        )
        batch_op.add_column(
            sa.Column("refund_reason", sa.String(500), nullable=True),
        )
        batch_op.add_column(
            sa.Column("refunded_at", sa.DateTime(), nullable=True),
        )

    # --- sale_order_items: 单品退款追踪 ---
    with op.batch_alter_table("sale_order_items", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("refund_quantity", sa.Numeric(10, 2), nullable=False, server_default="0"),
        )
        batch_op.add_column(
            sa.Column("return_to_stock", sa.Boolean(), nullable=False, server_default=sa.sql.false()),
        )


def downgrade() -> None:
    with op.batch_alter_table("sale_order_items", schema=None) as batch_op:
        batch_op.drop_column("return_to_stock")
        batch_op.drop_column("refund_quantity")

    with op.batch_alter_table("sale_orders", schema=None) as batch_op:
        batch_op.drop_column("refunded_at")
        batch_op.drop_column("refund_reason")
        batch_op.drop_column("held_at")
        batch_op.drop_column("refunded_amount")
