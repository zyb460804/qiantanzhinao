"""add SaaS multi-tenant tables (tenants, plans, subscriptions, invoices, usage_records, api_keys) and merchant.tenant_id

Revision ID: h9c0d1e2f3a4
Revises: g8b9c0d1e2f3
Create Date: 2026-07-13

This migration introduces the SaaS multi-tenant layer:
  - plans: subscription plan definitions (free/pro/enterprise)
  - tenants: top-level tenant/organization entity
  - merchants.tenant_id: FK to tenants (nullable for backward compat)
  - subscriptions: tenant subscription records
  - invoices: billing invoices
  - usage_records: usage metering per tenant/metric/day
  - api_keys: programmatic API access keys for tenants

Data seeding is handled by scripts/seed_saas.py (plans, demo tenant, platform admin).
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "h9c0d1e2f3a4"
down_revision: str | None = "g8b9c0d1e2f3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # ──────────────────────────────────────────────
    # 1. plans — 套餐定义（先建，因 tenants 有 FK 指向它）
    # ──────────────────────────────────────────────
    op.create_table(
        "plans",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("code", sa.String(30), nullable=False, unique=True),
        sa.Column("name", sa.String(60), nullable=False),
        sa.Column("price_monthly", sa.Numeric(10, 2), nullable=False, server_default="0"),
        sa.Column("price_yearly", sa.Numeric(10, 2), nullable=False, server_default="0"),
        sa.Column("max_merchants", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("max_api_calls_monthly", sa.Integer(), nullable=False, server_default="1000"),
        sa.Column("max_storage_mb", sa.Integer(), nullable=False, server_default="100"),
        sa.Column("features", sa.JSON(), nullable=True),
        sa.Column("is_public", sa.Boolean(), nullable=False, server_default=sa.text("1")),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("1")),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False),
    )

    # ──────────────────────────────────────────────
    # 2. tenants — 租户/组织
    # ──────────────────────────────────────────────
    op.create_table(
        "tenants",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("slug", sa.String(60), nullable=False, unique=True),
        sa.Column("plan_id", sa.Uuid(), sa.ForeignKey("plans.id"), nullable=True),
        sa.Column("status", sa.String(20), nullable=False, server_default="trial"),
        sa.Column("contact_email", sa.String(200), nullable=True),
        sa.Column("contact_phone", sa.String(30), nullable=True),
        sa.Column("trial_ends_at", sa.DateTime(), nullable=True),
        sa.Column("admin_notes", sa.Text(), nullable=True),
        sa.Column("metadata_", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False),
    )

    # ──────────────────────────────────────────────
    # 3. merchants.tenant_id — 新增列（可空，兼容存量）
    # 用 op.add_column 替代 batch_alter_table，避免 SQLite 重建表时的 FK 问题。
    # FK 约束在应用层由鉴权链保证，数据库层通过 index 加速查询。
    # ──────────────────────────────────────────────
    op.add_column("merchants", sa.Column("tenant_id", sa.Uuid(), nullable=True))
    op.create_index("ix_merchants_tenant_id", "merchants", ["tenant_id"])

    # ──────────────────────────────────────────────
    # 4. subscriptions — 订阅
    # ──────────────────────────────────────────────
    op.create_table(
        "subscriptions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("plan_id", sa.Uuid(), sa.ForeignKey("plans.id"), nullable=False),
        sa.Column("billing_cycle", sa.String(20), nullable=False, server_default="monthly"),
        sa.Column("status", sa.String(20), nullable=False, server_default="trialing"),
        sa.Column("current_period_start", sa.DateTime(), nullable=True),
        sa.Column("current_period_end", sa.DateTime(), nullable=True),
        sa.Column("canceled_at", sa.DateTime(), nullable=True),
        sa.Column("previous_plan_id", sa.Uuid(), sa.ForeignKey("plans.id"), nullable=True),
        sa.Column("auto_renew", sa.Boolean(), nullable=False, server_default=sa.text("1")),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False),
        sa.UniqueConstraint("tenant_id", "status", name="uq_subscription_per_tenant_status"),
    )
    op.create_index("ix_subscriptions_tenant_id", "subscriptions", ["tenant_id"])
    op.create_index("ix_subscriptions_plan_id", "subscriptions", ["plan_id"])

    # ──────────────────────────────────────────────
    # 5. saas_invoices — SaaS 计费账单（不与 POS 的 invoices 表冲突）
    # ──────────────────────────────────────────────
    op.create_table(
        "saas_invoices",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("subscription_id", sa.Uuid(), sa.ForeignKey("subscriptions.id"), nullable=True),
        sa.Column("invoice_no", sa.String(40), nullable=False, unique=True),
        sa.Column("amount", sa.Numeric(12, 2), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False, server_default="CNY"),
        sa.Column("status", sa.String(20), nullable=False, server_default="draft"),
        sa.Column("period_start", sa.DateTime(), nullable=True),
        sa.Column("period_end", sa.DateTime(), nullable=True),
        sa.Column("due_date", sa.DateTime(), nullable=True),
        sa.Column("paid_at", sa.DateTime(), nullable=True),
        sa.Column("payment_method", sa.String(30), nullable=True),
        sa.Column("transaction_id", sa.String(100), nullable=True),
        sa.Column("line_items", sa.JSON(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False),
    )
    op.create_index("ix_saas_invoices_tenant_id", "saas_invoices", ["tenant_id"])

    # ──────────────────────────────────────────────
    # 6. usage_records — 用量计量
    # ──────────────────────────────────────────────
    op.create_table(
        "usage_records",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("metric", sa.String(30), nullable=False),
        sa.Column("recorded_date", sa.String(10), nullable=False),
        sa.Column("value", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False),
        sa.UniqueConstraint("tenant_id", "metric", "recorded_date", name="uq_usage_per_tenant_metric_date"),
    )
    op.create_index("ix_usage_records_tenant_id", "usage_records", ["tenant_id"])

    # ──────────────────────────────────────────────
    # 7. api_keys — API 密钥
    # ──────────────────────────────────────────────
    op.create_table(
        "api_keys",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("name", sa.String(60), nullable=False),
        sa.Column("key_hash", sa.String(128), nullable=False, unique=True),
        sa.Column("key_prefix", sa.String(30), nullable=False),
        sa.Column("scopes", sa.JSON(), nullable=True),
        sa.Column("last_used_at", sa.DateTime(), nullable=True),
        sa.Column("expires_at", sa.DateTime(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("1")),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False),
    )
    op.create_index("ix_api_keys_tenant_id", "api_keys", ["tenant_id"])

    # ──────────────────────────────────────────────
    # 8. platform_admins — 平台管理员账号（Web 管理后台登录）
    # ──────────────────────────────────────────────
    op.create_table(
        "platform_admins",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("email", sa.String(200), nullable=False, unique=True),
        sa.Column("password_hash", sa.String(128), nullable=False),
        sa.Column("name", sa.String(60), nullable=False),
        sa.Column("role", sa.String(20), nullable=False, server_default="super_admin"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("1")),
        sa.Column("last_login_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("platform_admins")
    op.drop_table("api_keys")
    op.drop_table("usage_records")
    op.drop_table("saas_invoices")
    op.drop_table("subscriptions")

    # 移除 merchants.tenant_id
    op.drop_index("ix_merchants_tenant_id", table_name="merchants")
    op.drop_column("merchants", "tenant_id")

    op.drop_table("tenants")
    op.drop_table("plans")
