"""add exact FIFO unit cost to POS order items

Revision ID: m4g5b6c7d8e9
Revises: l3f4a5b6c7d8
Create Date: 2026-07-14
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "m4g5b6c7d8e9"
down_revision: str | None = "l3f4a5b6c7d8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("sale_order_items", sa.Column("unit_cost", sa.Numeric(10, 2), nullable=True))


def downgrade() -> None:
    op.drop_column("sale_order_items", "unit_cost")
