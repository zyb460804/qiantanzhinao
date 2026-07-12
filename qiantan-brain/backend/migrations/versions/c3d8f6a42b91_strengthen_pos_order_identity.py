"""strengthen POS order identity and credit customer linkage

Revision ID: c3d8f6a42b91
Revises: a9f4c2d71e30
Create Date: 2026-07-12
"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "c3d8f6a42b91"
down_revision: str | None = "a9f4c2d71e30"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("sale_orders", schema=None) as batch_op:
        batch_op.drop_index("ix_sale_orders_client_id")
        batch_op.add_column(sa.Column("customer_name", sa.String(100), nullable=True))
        batch_op.create_index(
            "ix_sale_orders_client_id", ["client_id"], unique=False
        )
        batch_op.create_unique_constraint(
            "uq_sale_order_client_per_merchant", ["merchant_id", "client_id"]
        )

    with op.batch_alter_table("daily_settlements", schema=None) as batch_op:
        batch_op.add_column(sa.Column("wechat_amount", sa.Numeric(12, 2), nullable=False, server_default="0"))
        batch_op.add_column(sa.Column("alipay_amount", sa.Numeric(12, 2), nullable=False, server_default="0"))
        batch_op.add_column(sa.Column("card_amount", sa.Numeric(12, 2), nullable=False, server_default="0"))


def downgrade() -> None:
    with op.batch_alter_table("daily_settlements", schema=None) as batch_op:
        batch_op.drop_column("card_amount")
        batch_op.drop_column("alipay_amount")
        batch_op.drop_column("wechat_amount")

    with op.batch_alter_table("sale_orders", schema=None) as batch_op:
        batch_op.drop_constraint(
            "uq_sale_order_client_per_merchant", type_="unique"
        )
        batch_op.drop_index("ix_sale_orders_client_id")
        batch_op.drop_column("customer_name")
        batch_op.create_index(
            "ix_sale_orders_client_id", ["client_id"], unique=True
        )
