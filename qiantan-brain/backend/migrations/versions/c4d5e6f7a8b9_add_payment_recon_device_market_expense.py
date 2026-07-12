"""add payment channels, reconciliation, devices, price displays, markets, expenses, invoices

Revision ID: c4d5e6f7a8b9
Revises: b2c3d4e5f6a7
Create Date: 2026-07-12
"""
from collections.abc import Sequence
from alembic import op
import sqlalchemy as sa

revision: str = "c4d5e6f7a8b9"
down_revision: str | None = "b2c3d4e5f6a7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # payment_channels
    op.create_table("payment_channels",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("merchant_id", sa.Uuid(), sa.ForeignKey("merchants.id"), nullable=False),
        sa.Column("channel", sa.String(20), nullable=False),
        sa.Column("sub_mch_id", sa.String(64), nullable=True),
        sa.Column("fee_rate", sa.Numeric(6, 4), nullable=False, server_default="0.006"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.sql.true()),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("merchant_id", "channel", name="uq_channel_per_merchant"),
    )

    # reconciliation_tasks
    op.create_table("reconciliation_tasks",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("merchant_id", sa.Uuid(), sa.ForeignKey("merchants.id"), nullable=False),
        sa.Column("channel", sa.String(20), nullable=False),
        sa.Column("date", sa.Date(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("system_total", sa.Numeric(12, 2), nullable=False, server_default="0"),
        sa.Column("channel_total", sa.Numeric(12, 2), nullable=False, server_default="0"),
        sa.Column("diff_amount", sa.Numeric(12, 2), nullable=False, server_default="0"),
        sa.Column("matched_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("unmatched_system", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("unmatched_channel", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("fee_amount", sa.Numeric(12, 2), nullable=False, server_default="0"),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("merchant_id", "channel", "date", name="uq_recon_per_day_channel"),
    )

    # reconciliation_differences
    op.create_table("reconciliation_differences",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("task_id", sa.Uuid(), sa.ForeignKey("reconciliation_tasks.id"), nullable=False),
        sa.Column("merchant_id", sa.Uuid(), sa.ForeignKey("merchants.id"), nullable=False),
        sa.Column("diff_type", sa.String(30), nullable=False),
        sa.Column("system_ref", sa.String(64), nullable=True),
        sa.Column("channel_ref", sa.String(64), nullable=True),
        sa.Column("system_amount", sa.Numeric(12, 2), nullable=True),
        sa.Column("channel_amount", sa.Numeric(12, 2), nullable=True),
        sa.Column("status", sa.String(20), nullable=False, server_default="open"),
        sa.Column("resolution", sa.String(200), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("id"),
    )

    # devices
    op.create_table("devices",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("merchant_id", sa.Uuid(), sa.ForeignKey("merchants.id"), nullable=False),
        sa.Column("device_type", sa.String(30), nullable=False),
        sa.Column("device_name", sa.String(50), nullable=False),
        sa.Column("serial_number", sa.String(64), nullable=True),
        sa.Column("firmware_version", sa.String(20), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.sql.true()),
        sa.Column("last_heartbeat", sa.DateTime(), nullable=True),
        sa.Column("last_error", sa.String(200), nullable=True),
        sa.Column("config", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("merchant_id", "serial_number", name="uq_device_serial_per_merchant"),
    )

    # price_displays
    op.create_table("price_displays",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("merchant_id", sa.Uuid(), sa.ForeignKey("merchants.id"), nullable=False),
        sa.Column("device_id", sa.Uuid(), sa.ForeignKey("devices.id"), nullable=True),
        sa.Column("sku_id", sa.Uuid(), sa.ForeignKey("product_skus.id"), nullable=False),
        sa.Column("current_price", sa.Float(), nullable=False, server_default="0"),
        sa.Column("price_source", sa.String(20), nullable=False, server_default="manual"),
        sa.Column("sync_status", sa.String(20), nullable=False, server_default="synced"),
        sa.Column("sync_error", sa.String(200), nullable=True),
        sa.Column("last_synced", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("merchant_id", "sku_id", name="uq_price_display_per_sku"),
    )

    # expenses
    op.create_table("expenses",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("merchant_id", sa.Uuid(), sa.ForeignKey("merchants.id"), nullable=False),
        sa.Column("category", sa.String(30), nullable=False),
        sa.Column("amount", sa.Numeric(12, 2), nullable=False),
        sa.Column("description", sa.String(200), nullable=True),
        sa.Column("expense_date", sa.Date(), nullable=False),
        sa.Column("payment_method", sa.String(20), nullable=True),
        sa.Column("invoice_id", sa.Uuid(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("id"),
    )

    # invoices
    op.create_table("invoices",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("merchant_id", sa.Uuid(), sa.ForeignKey("merchants.id"), nullable=False),
        sa.Column("invoice_number", sa.String(50), nullable=False),
        sa.Column("invoice_type", sa.String(20), nullable=False, server_default="electronic"),
        sa.Column("supplier_name", sa.String(100), nullable=True),
        sa.Column("amount", sa.Numeric(12, 2), nullable=False),
        sa.Column("tax_amount", sa.Numeric(12, 2), nullable=True),
        sa.Column("invoice_date", sa.Date(), nullable=False),
        sa.Column("file_url", sa.String(500), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("merchant_id", "invoice_number", name="uq_invoice_per_merchant"),
    )

    # markets
    op.create_table("markets",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("address", sa.String(200), nullable=True),
        sa.Column("contact", sa.String(50), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.sql.true()),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("id"),
    )

    # market_merchants
    op.create_table("market_merchants",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("market_id", sa.Uuid(), sa.ForeignKey("markets.id"), nullable=False),
        sa.Column("merchant_id", sa.Uuid(), sa.ForeignKey("merchants.id"), nullable=False),
        sa.Column("stall_number", sa.String(20), nullable=True),
        sa.Column("category", sa.String(30), nullable=True),
        sa.Column("license_number", sa.String(50), nullable=True),
        sa.Column("health_cert_expiry", sa.DateTime(), nullable=True),
        sa.Column("food_safety_score", sa.Integer(), nullable=False, server_default="100"),
        sa.Column("status", sa.String(20), nullable=False, server_default="active"),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("market_id", "merchant_id", name="uq_market_merchant"),
    )

    # market_inspections
    op.create_table("market_inspections",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("market_id", sa.Uuid(), sa.ForeignKey("markets.id"), nullable=False),
        sa.Column("merchant_id", sa.Uuid(), sa.ForeignKey("merchants.id"), nullable=True),
        sa.Column("inspector", sa.String(50), nullable=False),
        sa.Column("inspection_type", sa.String(30), nullable=False),
        sa.Column("result", sa.String(20), nullable=False, server_default="pass"),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("photos", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("id"),
    )

    # market_complaints
    op.create_table("market_complaints",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("market_id", sa.Uuid(), sa.ForeignKey("markets.id"), nullable=False),
        sa.Column("merchant_id", sa.Uuid(), sa.ForeignKey("merchants.id"), nullable=True),
        sa.Column("complainant", sa.String(50), nullable=True),
        sa.Column("complaint_type", sa.String(30), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="open"),
        sa.Column("resolution", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now()),
        sa.Column("resolved_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )

    # market_notices
    op.create_table("market_notices",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("market_id", sa.Uuid(), sa.ForeignKey("markets.id"), nullable=False),
        sa.Column("title", sa.String(100), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("notice_type", sa.String(20), nullable=False, server_default="info"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.sql.true()),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("id"),
    )


def downgrade() -> None:
    op.drop_table("market_notices")
    op.drop_table("market_complaints")
    op.drop_table("market_inspections")
    op.drop_table("market_merchants")
    op.drop_table("markets")
    op.drop_table("invoices")
    op.drop_table("expenses")
    op.drop_table("price_displays")
    op.drop_table("devices")
    op.drop_table("reconciliation_differences")
    op.drop_table("reconciliation_tasks")
    op.drop_table("payment_channels")
