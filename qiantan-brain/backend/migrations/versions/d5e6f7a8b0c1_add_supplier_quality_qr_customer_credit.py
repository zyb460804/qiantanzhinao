"""add supplier quality metrics, batch qr_data, customer credit profiles, media files

Revision ID: d5e6f7a8b0c1
Revises: c4d5e6f7a8b9
Create Date: 2026-07-12
"""
from collections.abc import Sequence
from alembic import op
import sqlalchemy as sa


revision: str = "d5e6f7a8b0c1"
down_revision: str | None = "c4d5e6f7a8b9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # --- suppliers: quality metrics (§4.2) ---
    op.add_column("suppliers", sa.Column("address", sa.String(200), nullable=True))
    op.add_column("suppliers", sa.Column("business_category", sa.String(100), nullable=True))
    op.add_column("suppliers", sa.Column("default_credit_days", sa.Integer(), nullable=True))
    op.add_column("suppliers", sa.Column("certificates", sa.Text(), nullable=True))
    op.add_column("suppliers", sa.Column("shortage_rate", sa.Numeric(5, 2), nullable=True))
    op.add_column("suppliers", sa.Column("return_rate", sa.Numeric(5, 2), nullable=True))
    op.add_column("suppliers", sa.Column("quality_issue_rate", sa.Numeric(5, 2), nullable=True))
    op.add_column("suppliers", sa.Column("on_time_rate", sa.Numeric(5, 2), nullable=True))
    op.add_column("suppliers", sa.Column("composite_score", sa.Numeric(5, 2), nullable=True))
    op.add_column("suppliers", sa.Column("total_orders", sa.Integer(), server_default="0", nullable=False))
    op.add_column("suppliers", sa.Column("is_blacklisted", sa.Boolean(), server_default="false", nullable=False))

    # --- batch_lifecycles: QR code data (§4.13) ---
    op.add_column("batch_lifecycles", sa.Column("qr_data", sa.Text(), nullable=True))

    # --- customer_credit_profiles (§4.8) ---
    op.create_table(
        "customer_credit_profiles",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("merchant_id", sa.Uuid(), sa.ForeignKey("merchants.id"), nullable=False),
        sa.Column("customer_name", sa.String(50), nullable=False),
        sa.Column("credit_limit", sa.Numeric(12, 2), nullable=True),
        sa.Column("default_credit_days", sa.Integer(), nullable=True),
        sa.Column("is_blocked", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("block_reason", sa.String(200), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("merchant_id", "customer_name", name="uq_customer_profile_per_merchant"),
    )

    # --- media_files (§5.9, §5.10) ---
    op.create_table(
        "media_files",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("merchant_id", sa.Uuid(), sa.ForeignKey("merchants.id"), nullable=False),
        sa.Column("original_name", sa.String(255), nullable=False),
        sa.Column("stored_name", sa.String(255), nullable=False),
        sa.Column("media_type", sa.String(20), nullable=False),
        sa.Column("business_type", sa.String(50), nullable=True),
        sa.Column("business_payload", sa.JSON(), nullable=True),
        sa.Column("mime_type", sa.String(100), nullable=True),
        sa.Column("file_size", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("file_path", sa.String(500), nullable=False),
        sa.Column("idempotency_key", sa.String(64), nullable=True, index=True),
        sa.Column("retention_days", sa.Integer(), nullable=True),
        sa.Column("uploaded_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("merchant_id", "idempotency_key", name="uq_media_idempotency_per_merchant"),
    )

    # --- audit_logs: request_id for traceability (§5.14) ---
    op.add_column("audit_logs", sa.Column("request_id", sa.String(64), nullable=True))


def downgrade() -> None:
    op.drop_column("audit_logs", "request_id")
    op.drop_table("media_files")
    op.drop_table("customer_credit_profiles")
    op.drop_column("batch_lifecycles", "qr_data")
    op.drop_column("suppliers", "is_blacklisted")
    op.drop_column("suppliers", "total_orders")
    op.drop_column("suppliers", "composite_score")
    op.drop_column("suppliers", "on_time_rate")
    op.drop_column("suppliers", "quality_issue_rate")
    op.drop_column("suppliers", "return_rate")
    op.drop_column("suppliers", "shortage_rate")
    op.drop_column("suppliers", "certificates")
    op.drop_column("suppliers", "default_credit_days")
    op.drop_column("suppliers", "business_category")
    op.drop_column("suppliers", "address")
