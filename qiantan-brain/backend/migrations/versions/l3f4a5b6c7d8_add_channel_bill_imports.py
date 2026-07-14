"""add channel bill import batches and normalized entries

Revision ID: l3f4a5b6c7d8
Revises: k2f3a4b5c6d7
Create Date: 2026-07-14
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "l3f4a5b6c7d8"
down_revision: str | None = "k2f3a4b5c6d7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "channel_bill_imports",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "task_id",
            sa.Uuid(),
            sa.ForeignKey("reconciliation_tasks.id"),
            nullable=False,
        ),
        sa.Column(
            "merchant_id",
            sa.Uuid(),
            sa.ForeignKey("merchants.id"),
            nullable=False,
        ),
        sa.Column("channel", sa.String(20), nullable=False),
        sa.Column("bill_date", sa.Date(), nullable=False),
        sa.Column("file_name", sa.String(255), nullable=False),
        sa.Column("file_hash", sa.String(64), nullable=False),
        sa.Column("row_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("inserted_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("duplicate_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "created_at",
            sa.DateTime(),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.UniqueConstraint(
            "merchant_id",
            "channel",
            "bill_date",
            "file_hash",
            name="uq_channel_bill_import_file",
        ),
    )
    op.create_index("ix_channel_bill_imports_task_id", "channel_bill_imports", ["task_id"])
    op.create_index(
        "ix_channel_bill_imports_merchant_id", "channel_bill_imports", ["merchant_id"]
    )

    op.create_table(
        "channel_bill_entries",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "import_id",
            sa.Uuid(),
            sa.ForeignKey("channel_bill_imports.id"),
            nullable=False,
        ),
        sa.Column(
            "task_id",
            sa.Uuid(),
            sa.ForeignKey("reconciliation_tasks.id"),
            nullable=False,
        ),
        sa.Column(
            "merchant_id",
            sa.Uuid(),
            sa.ForeignKey("merchants.id"),
            nullable=False,
        ),
        sa.Column("channel", sa.String(20), nullable=False),
        sa.Column("bill_date", sa.Date(), nullable=False),
        sa.Column("entry_key", sa.String(64), nullable=False),
        sa.Column("record_type", sa.String(20), nullable=False),
        sa.Column("channel_ref", sa.String(128), nullable=True),
        sa.Column("merchant_ref", sa.String(128), nullable=True),
        sa.Column("amount", sa.Numeric(12, 2), nullable=False),
        sa.Column("fee_amount", sa.Numeric(12, 2), nullable=False, server_default="0"),
        sa.Column("occurred_at", sa.DateTime(), nullable=True),
        sa.Column("channel_status", sa.String(50), nullable=True),
        sa.Column("matched_payment_id", sa.Uuid(), sa.ForeignKey("payments.id"), nullable=True),
        sa.Column("match_status", sa.String(30), nullable=False, server_default="unmatched"),
        sa.Column("raw_data", sa.JSON(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.UniqueConstraint(
            "merchant_id",
            "channel",
            "bill_date",
            "entry_key",
            name="uq_channel_bill_entry_key",
        ),
    )
    op.create_index("ix_channel_bill_entries_import_id", "channel_bill_entries", ["import_id"])
    op.create_index("ix_channel_bill_entries_task_id", "channel_bill_entries", ["task_id"])
    op.create_index(
        "ix_channel_bill_entries_merchant_id", "channel_bill_entries", ["merchant_id"]
    )
    op.create_index("ix_channel_bill_entries_channel_ref", "channel_bill_entries", ["channel_ref"])
    op.create_index("ix_channel_bill_entries_merchant_ref", "channel_bill_entries", ["merchant_ref"])
    op.create_index(
        "ix_channel_bill_entries_matched_payment_id",
        "channel_bill_entries",
        ["matched_payment_id"],
    )
    op.create_index(
        "ix_channel_bill_entry_task_match",
        "channel_bill_entries",
        ["task_id", "match_status"],
    )


def downgrade() -> None:
    op.drop_table("channel_bill_entries")
    op.drop_table("channel_bill_imports")
