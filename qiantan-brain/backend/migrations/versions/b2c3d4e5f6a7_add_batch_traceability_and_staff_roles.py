"""add batch traceability fields, staff members, and sensitive operations tables

Revision ID: b2c3d4e5f6a7
Revises: a1b2c3d4e5f6
Create Date: 2026-07-12
"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "b2c3d4e5f6a7"
down_revision: str | None = "a1b2c3d4e5f6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # --- batch_lifecycles: 追溯 + 锁定/销毁字段 ---
    with op.batch_alter_table("batch_lifecycles", schema=None) as batch_op:
        batch_op.add_column(sa.Column("supplier_id", sa.Uuid(), nullable=True))
        batch_op.add_column(sa.Column("supplier_name", sa.String(50), nullable=True))
        batch_op.add_column(sa.Column("origin", sa.String(100), nullable=True))
        batch_op.add_column(sa.Column("unit_cost", sa.Numeric(10, 2), nullable=True))
        batch_op.add_column(sa.Column("certificates", sa.Text(), nullable=True))
        batch_op.add_column(sa.Column("inspection_result", sa.String(50), nullable=True))
        batch_op.add_column(sa.Column("locked_at", sa.DateTime(), nullable=True))
        batch_op.add_column(sa.Column("locked_reason", sa.String(200), nullable=True))
        batch_op.add_column(sa.Column("locked_by", sa.String(50), nullable=True))
        batch_op.add_column(sa.Column("destroyed_at", sa.DateTime(), nullable=True))
        batch_op.add_column(sa.Column("destroyed_reason", sa.String(200), nullable=True))
        batch_op.add_column(sa.Column("sale_orders", sa.Text(), nullable=True))

    # --- staff_members ---
    op.create_table(
        "staff_members",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("merchant_id", sa.Uuid(), sa.ForeignKey("merchants.id"), nullable=False),
        sa.Column("name", sa.String(50), nullable=False),
        sa.Column("phone", sa.String(20), nullable=True),
        sa.Column("role", sa.String(20), nullable=False, server_default="cashier"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.sql.true()),
        sa.Column("pin_code", sa.String(10), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("merchant_id", "phone", name="uq_staff_phone_per_merchant"),
    )

    # --- sensitive_operations ---
    op.create_table(
        "sensitive_operations",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("merchant_id", sa.Uuid(), sa.ForeignKey("merchants.id"), nullable=False),
        sa.Column("staff_id", sa.Uuid(), sa.ForeignKey("staff_members.id"), nullable=True),
        sa.Column("action", sa.String(50), nullable=False),
        sa.Column("target_type", sa.String(50), nullable=False),
        sa.Column("target_id", sa.String(64), nullable=False),
        sa.Column("before_snapshot", sa.Text(), nullable=True),
        sa.Column("after_snapshot", sa.Text(), nullable=True),
        sa.Column("authorized_by", sa.String(50), nullable=True),
        sa.Column("reason", sa.String(500), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("id"),
    )


def downgrade() -> None:
    op.drop_table("sensitive_operations")
    op.drop_table("staff_members")

    with op.batch_alter_table("batch_lifecycles", schema=None) as batch_op:
        batch_op.drop_column("sale_orders")
        batch_op.drop_column("destroyed_reason")
        batch_op.drop_column("destroyed_at")
        batch_op.drop_column("locked_by")
        batch_op.drop_column("locked_reason")
        batch_op.drop_column("locked_at")
        batch_op.drop_column("inspection_result")
        batch_op.drop_column("certificates")
        batch_op.drop_column("unit_cost")
        batch_op.drop_column("origin")
        batch_op.drop_column("supplier_name")
        batch_op.drop_column("supplier_id")
