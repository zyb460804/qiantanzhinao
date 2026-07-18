"""SaaS 多租户模型 — 租户/套餐/订阅/账单/用量/API密钥。

在现有 Merchant 之上引入 Tenant（租户/组织）层，使一个租户下可包含
多个商户（如连锁菜市场管理多个摊位）。配套套餐、订阅、账单、用量计量、
API 密钥，形成完整 SaaS 多租户能力。

角色体系（与 Merchant.role 配合）:
  platform_admin  — 平台超级管理员，管理所有租户/套餐/计费
  tenant_admin    — 租户管理员，管理本组织下的商户和订阅
  market_admin    — 市场管理员（现有）
  owner           — 摊主（现有）
  employee        — 员工（现有）
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class Tenant(Base):
    """租户/组织 — SaaS 多租户顶层实体。

    一个租户对应一个组织（如"XX农贸市场管理公司"），下辖多个 Merchant。
    租户绑定一个 Plan，决定功能配额。
    """

    __tablename__ = "tenants"

    id: Mapped[uuid.UUID] = mapped_column(sa.Uuid, primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(sa.String(100), nullable=False)
    # URL 友好标识，用于管理后台路由 / admin?tenant=xxx
    slug: Mapped[str] = mapped_column(sa.String(60), unique=True, nullable=False)
    # 当前订阅套餐（外键到 plans.id）
    plan_id: Mapped[uuid.UUID | None] = mapped_column(sa.Uuid, sa.ForeignKey("plans.id"))
    # 租户状态: trial(试用) / active(正常) / suspended(已停用) / expired(已过期)
    status: Mapped[str] = mapped_column(sa.String(20), default="trial", nullable=False)
    # 联系人/邮箱（平台管理员联系租户用）
    contact_email: Mapped[str | None] = mapped_column(sa.String(200))
    contact_phone: Mapped[str | None] = mapped_column(sa.String(30))
    # 试用到期时间（注册后 14 天）
    trial_ends_at: Mapped[datetime | None] = mapped_column(sa.DateTime)
    # 平台级备注（如"大客户-手工开通"）
    admin_notes: Mapped[str | None] = mapped_column(sa.Text)
    # 扩展元数据
    metadata_: Mapped[dict | None] = mapped_column(sa.JSON)
    created_at: Mapped[datetime] = mapped_column(sa.DateTime, server_default=sa.func.now())
    updated_at: Mapped[datetime] = mapped_column(
        sa.DateTime, server_default=sa.func.now(), onupdate=sa.func.now()
    )


class Plan(Base):
    """订阅套餐定义。

    预置: free(免费版) / pro(专业版) / enterprise(企业版)
    每个套餐定义商户数上限、月 API 调用上限、存储上限及功能开关。
    """

    __tablename__ = "plans"

    id: Mapped[uuid.UUID] = mapped_column(sa.Uuid, primary_key=True, default=uuid.uuid4)
    # 套餐代码: free / pro / enterprise
    code: Mapped[str] = mapped_column(sa.String(30), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(sa.String(60), nullable=False)
    # 月费 / 年费（Decimal 精度，避免浮点误差）
    price_monthly: Mapped[Decimal] = mapped_column(
        sa.Numeric(10, 2), default=Decimal("0"), nullable=False
    )
    price_yearly: Mapped[Decimal] = mapped_column(
        sa.Numeric(10, 2), default=Decimal("0"), nullable=False
    )
    # 配额
    max_merchants: Mapped[int] = mapped_column(sa.Integer, default=1, nullable=False)
    max_api_calls_monthly: Mapped[int] = mapped_column(sa.Integer, default=1000, nullable=False)
    max_storage_mb: Mapped[int] = mapped_column(sa.Integer, default=100, nullable=False)
    # 功能开关（JSON，如 {"ai_advisor": true, "vision": false}）
    features: Mapped[dict | None] = mapped_column(sa.JSON)
    # 是否公开（公开套餐可在注册页选择，非公开由平台管理员手动开通）
    is_public: Mapped[bool] = mapped_column(sa.Boolean, default=True, nullable=False)
    is_active: Mapped[bool] = mapped_column(sa.Boolean, default=True, nullable=False)
    # 排序（管理后台展示顺序）
    sort_order: Mapped[int] = mapped_column(sa.Integer, default=0, nullable=False)
    created_at: Mapped[datetime] = mapped_column(sa.DateTime, server_default=sa.func.now())
    updated_at: Mapped[datetime] = mapped_column(
        sa.DateTime, server_default=sa.func.now(), onupdate=sa.func.now()
    )


class Subscription(Base):
    """租户订阅记录 — 记录租户与套餐的绑定关系及计费周期。

    状态流: trialing → active → past_due → canceled / expired
    """

    __tablename__ = "subscriptions"

    id: Mapped[uuid.UUID] = mapped_column(sa.Uuid, primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid, sa.ForeignKey("tenants.id"), nullable=False, index=True
    )
    plan_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid, sa.ForeignKey("plans.id"), nullable=False, index=True
    )
    # 计费周期: monthly / yearly
    billing_cycle: Mapped[str] = mapped_column(sa.String(20), default="monthly", nullable=False)
    # 状态: trialing(试用) / active(有效) / past_due(逾期) / canceled(已取消) / expired(已过期)
    status: Mapped[str] = mapped_column(sa.String(20), default="trialing", nullable=False)
    # 当前计费周期
    current_period_start: Mapped[datetime | None] = mapped_column(sa.DateTime)
    current_period_end: Mapped[datetime | None] = mapped_column(sa.DateTime)
    # 取消时间（取消后到 current_period_end 仍有效）
    canceled_at: Mapped[datetime | None] = mapped_column(sa.DateTime)
    # 降级/升级时记录原套餐
    previous_plan_id: Mapped[uuid.UUID | None] = mapped_column(sa.Uuid, sa.ForeignKey("plans.id"))
    # 自动续费
    auto_renew: Mapped[bool] = mapped_column(sa.Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(sa.DateTime, server_default=sa.func.now())
    updated_at: Mapped[datetime] = mapped_column(
        sa.DateTime, server_default=sa.func.now(), onupdate=sa.func.now()
    )

    __table_args__ = (
        # 一个租户同一时间只允许一个有效订阅
        sa.UniqueConstraint("tenant_id", "status", name="uq_subscription_per_tenant_status"),
    )


class Invoice(Base):
    """账单 — 每个计费周期生成一张账单。

    状态流: draft → sent → paid / overdue / void
    """

    __tablename__ = "saas_invoices"

    id: Mapped[uuid.UUID] = mapped_column(sa.Uuid, primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid, sa.ForeignKey("tenants.id"), nullable=False, index=True
    )
    subscription_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.Uuid, sa.ForeignKey("subscriptions.id")
    )
    # 账单编号（如 INV-202607-0001）
    invoice_no: Mapped[str] = mapped_column(sa.String(40), unique=True, nullable=False)
    # 账单金额
    amount: Mapped[Decimal] = mapped_column(sa.Numeric(12, 2), nullable=False)
    # 币种（ISO 4217，默认 CNY）
    currency: Mapped[str] = mapped_column(sa.String(3), default="CNY", nullable=False)
    # 状态: draft(草稿) / sent(已发送) / paid(已付) / overdue(逾期) / void(作废)
    status: Mapped[str] = mapped_column(sa.String(20), default="draft", nullable=False)
    # 计费周期
    period_start: Mapped[datetime | None] = mapped_column(sa.DateTime)
    period_end: Mapped[datetime | None] = mapped_column(sa.DateTime)
    # 到期日
    due_date: Mapped[datetime | None] = mapped_column(sa.DateTime)
    # 付款时间
    paid_at: Mapped[datetime | None] = mapped_column(sa.DateTime)
    # 付款方式: wechat_pay / alipay / bank_transfer / manual
    payment_method: Mapped[str | None] = mapped_column(sa.String(30))
    # 交易流水号（第三方支付回调填入）
    transaction_id: Mapped[str | None] = mapped_column(sa.String(100))
    # 账单明细（JSON 数组，如 [{"name":"专业版月费","amount":99.00}]）
    line_items: Mapped[list[dict[str, object]] | None] = mapped_column(sa.JSON)
    # 备注
    notes: Mapped[str | None] = mapped_column(sa.Text)
    created_at: Mapped[datetime] = mapped_column(sa.DateTime, server_default=sa.func.now())
    updated_at: Mapped[datetime] = mapped_column(
        sa.DateTime, server_default=sa.func.now(), onupdate=sa.func.now()
    )


class UsageRecord(Base):
    """用量计量 — 按指标按日聚合记录租户用量。

    用于配额检查和计费。每条记录代表某租户在某日对某指标的使用量。
    """

    __tablename__ = "usage_records"

    id: Mapped[uuid.UUID] = mapped_column(sa.Uuid, primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid, sa.ForeignKey("tenants.id"), nullable=False, index=True
    )
    # 指标: api_calls / storage_mb / merchant_count / voice_seconds
    metric: Mapped[str] = mapped_column(sa.String(30), nullable=False)
    # 计量日期（按日聚合）
    recorded_date: Mapped[str] = mapped_column(sa.String(10), nullable=False)  # YYYY-MM-DD
    # 累计值（当天该指标的总量）
    value: Mapped[int] = mapped_column(sa.Integer, default=0, nullable=False)
    created_at: Mapped[datetime] = mapped_column(sa.DateTime, server_default=sa.func.now())

    __table_args__ = (
        # 每租户每日每指标唯一，防止重复记录
        sa.UniqueConstraint(
            "tenant_id",
            "metric",
            "recorded_date",
            name="uq_usage_per_tenant_metric_date",
        ),
    )


class ApiKey(Base):
    """API 密钥 — 供租户程序化访问后端 API。

    密钥只存储哈希（SHA-256），创建时返回明文仅一次。
    前缀 "qt_" 便于识别，如 qt_live_a1b2c3d4e5f6...
    """

    __tablename__ = "api_keys"

    id: Mapped[uuid.UUID] = mapped_column(sa.Uuid, primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid, sa.ForeignKey("tenants.id"), nullable=False, index=True
    )
    # 密钥名称（用户自定义，如"边缘端同步"）
    name: Mapped[str] = mapped_column(sa.String(60), nullable=False)
    # SHA-256 哈希（不存明文）
    key_hash: Mapped[str] = mapped_column(sa.String(128), unique=True, nullable=False)
    # 密钥前缀（用于展示，如 "qt_live_a1b2..."）
    key_prefix: Mapped[str] = mapped_column(sa.String(30), nullable=False)
    # 权限范围（JSON，如 ["read:inventory","write:inventory"]）
    scopes: Mapped[list[str] | None] = mapped_column(sa.JSON)
    # 最后使用时间
    last_used_at: Mapped[datetime | None] = mapped_column(sa.DateTime)
    # 过期时间（None = 永不过期）
    expires_at: Mapped[datetime | None] = mapped_column(sa.DateTime)
    is_active: Mapped[bool] = mapped_column(sa.Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(sa.DateTime, server_default=sa.func.now())
    updated_at: Mapped[datetime] = mapped_column(
        sa.DateTime, server_default=sa.func.now(), onupdate=sa.func.now()
    )


class PlatformAdmin(Base):
    """平台管理员账号 — Web 管理后台登录用。

    与 Merchant 区分：平台管理员不归属任何 Tenant，
    有全局管理权限（管理所有租户、套餐、计费）。
    登录方式：邮箱 + 密码（bcrypt 哈希）。
    """

    __tablename__ = "platform_admins"

    id: Mapped[uuid.UUID] = mapped_column(sa.Uuid, primary_key=True, default=uuid.uuid4)
    email: Mapped[str] = mapped_column(sa.String(200), unique=True, nullable=False)
    # bcrypt 哈希（绝不存明文）
    password_hash: Mapped[str] = mapped_column(sa.String(128), nullable=False)
    name: Mapped[str] = mapped_column(sa.String(60), nullable=False)
    # 角色：super_admin（全部权限）/ ops_admin（运营管理，无计费）
    role: Mapped[str] = mapped_column(sa.String(20), default="super_admin", nullable=False)
    is_active: Mapped[bool] = mapped_column(sa.Boolean, default=True, nullable=False)
    last_login_at: Mapped[datetime | None] = mapped_column(sa.DateTime)
    created_at: Mapped[datetime] = mapped_column(sa.DateTime, server_default=sa.func.now())
    updated_at: Mapped[datetime] = mapped_column(
        sa.DateTime, server_default=sa.func.now(), onupdate=sa.func.now()
    )
