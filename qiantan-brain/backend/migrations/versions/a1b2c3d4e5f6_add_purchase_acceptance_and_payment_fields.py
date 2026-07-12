"""add purchase acceptance, return, and payment tracking fields

Revision ID: a1b2c3d4e5f6
Revises: f8d2a7b51c90
Create Date: 2026-07-12
"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "a1b2c3d4e5f6"
down_revision: str | None = "f8d2a7b51c90"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # --- purchase_lists: 验收与状态机 ---
    with op.batch_alter_table("purchase_lists", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("expected_arrival_date", sa.DateTime(), nullable=True),
        )
        batch_op.add_column(
            sa.Column("accepted_at", sa.DateTime(), nullable=True),
        )
        batch_op.add_column(
            sa.Column("stored_at", sa.DateTime(), nullable=True),
        )
        batch_op.add_column(
            sa.Column("completed_at", sa.DateTime(), nullable=True),
        )

    # --- purchase_items: 到货验收明细 ---
    with op.batch_alter_table("purchase_items", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("package_count", sa.Integer(), nullable=True),
        )
        batch_op.add_column(
            sa.Column("gross_weight", sa.Numeric(10, 2), nullable=True),
        )
        batch_op.add_column(
            sa.Column("tare_weight", sa.Numeric(10, 2), nullable=True),
        )
        batch_op.add_column(
            sa.Column("net_weight", sa.Numeric(10, 2), nullable=True),
        )
        batch_op.add_column(
            sa.Column("arrival_qty", sa.Numeric(10, 2), nullable=True),
        )
        batch_op.add_column(
            sa.Column("shortage_qty", sa.Numeric(10, 2), nullable=True),
        )
        batch_op.add_column(
            sa.Column("damaged_qty", sa.Numeric(10, 2), nullable=True),
        )
        batch_op.add_column(
            sa.Column("rejected_qty", sa.Numeric(10, 2), nullable=True),
        )
        batch_op.add_column(
            sa.Column("returned_qty", sa.Numeric(10, 2), nullable=True),
        )
        batch_op.add_column(
            sa.Column("replenish_qty", sa.Numeric(10, 2), nullable=True),
        )
        batch_op.add_column(
            sa.Column("accepted_qty", sa.Numeric(10, 2), nullable=True),
        )
        batch_op.add_column(
            sa.Column("quality_ok", sa.Boolean(), nullable=True),
        )
        batch_op.add_column(
            sa.Column("acceptance_photos", sa.Text(), nullable=True),
        )
        batch_op.add_column(
            sa.Column("certificates", sa.Text(), nullable=True),
        )
        batch_op.add_column(
            sa.Column("acceptance_notes", sa.Text(), nullable=True),
        )
        batch_op.add_column(
            sa.Column("accepted_at", sa.DateTime(), nullable=True),
        )


def downgrade() -> None:
    with op.batch_alter_table("purchase_items", schema=None) as batch_op:
        batch_op.drop_column("accepted_at")
        batch_op.drop_column("acceptance_notes")
        batch_op.drop_column("certificates")
        batch_op.drop_column("acceptance_photos")
        batch_op.drop_column("quality_ok")
        batch_op.drop_column("accepted_qty")
        batch_op.drop_column("replenish_qty")
        batch_op.drop_column("returned_qty")
        batch_op.drop_column("rejected_qty")
        batch_op.drop_column("damaged_qty")
        batch_op.drop_column("shortage_qty")
        batch_op.drop_column("arrival_qty")
        batch_op.drop_column("net_weight")
        batch_op.drop_column("tare_weight")
        batch_op.drop_column("gross_weight")
        batch_op.drop_column("package_count")

    with op.batch_alter_table("purchase_lists", schema=None) as batch_op:
        batch_op.drop_column("completed_at")
        batch_op.drop_column("stored_at")
        batch_op.drop_column("accepted_at")
        batch_op.drop_column("expected_arrival_date")
