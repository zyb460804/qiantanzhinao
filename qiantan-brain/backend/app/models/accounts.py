"""往来账模型 — 供应商应付账款 & 客户应收账款 + 客户信用档案。

为什么独立成表而不是在 Supplier/商户上挂一个可变余额字段：
- 流水为真相：当前欠款 = SUM(应付流水) - SUM(已付流水)。余额不直接维护，
  避免「改余额」带来的对账黑洞（与库存流水账同一原则，红线 #1）。
- 支持语音记账：
    "张记饭店拿了80块菜，先记账"  → CustomerReceivable(+80, 未结)
    "给老王结了昨天的500块货款"    → SupplierPayable(-500, 已付)
- 金额一律 Decimal/NUMERIC，禁止 float 累加（红线 #7）。
- 带 idempotency_key：网络重试不会重复记一笔账（红线 #4）。

客户信用档案 (§4.8): 信用额度、默认账期、停赊标记。
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class SupplierPayable(Base):
    """供应商应付账款流水。

    direction:
      - purchase : 进货产生应付（余额 +amount）
      - payment  : 付款/部分付款（余额 -amount）
    余额由聚合得到，不在此表维护可变字段。
    """

    __tablename__ = "supplier_payables"
    __table_args__ = (
        sa.UniqueConstraint(
            "merchant_id",
            "idempotency_key",
            name="uq_supplier_payable_idempotency_per_merchant",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(sa.Uuid, primary_key=True, default=uuid.uuid4)
    merchant_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid, sa.ForeignKey("merchants.id"), nullable=False
    )
    supplier_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid, sa.ForeignKey("suppliers.id"), nullable=False
    )
    direction: Mapped[str] = mapped_column(sa.String(10), default="purchase")  # purchase / payment
    amount: Mapped[Decimal] = mapped_column(sa.Numeric(12, 2), nullable=False)
    purchase_list_id: Mapped[uuid.UUID | None] = mapped_column(sa.Uuid)
    note: Mapped[str | None] = mapped_column(sa.Text)
    due_date: Mapped[date | None] = mapped_column(sa.Date)
    settled: Mapped[bool] = mapped_column(sa.Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(sa.DateTime, server_default=sa.func.now())
    idempotency_key: Mapped[str | None] = mapped_column(sa.String(64), index=True)


class CustomerReceivable(Base):
    """客户应收账款流水（饭店/食堂/老顾客赊账）。

    direction:
      - charge : 赊账产生应收（余额 +amount）
      - repay  : 回款/月底结算（余额 -amount）
    """

    __tablename__ = "customer_receivables"
    __table_args__ = (
        sa.UniqueConstraint(
            "merchant_id",
            "idempotency_key",
            name="uq_customer_receivable_idempotency_per_merchant",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(sa.Uuid, primary_key=True, default=uuid.uuid4)
    merchant_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid, sa.ForeignKey("merchants.id"), nullable=False
    )
    customer_name: Mapped[str] = mapped_column(sa.String(50), nullable=False)
    direction: Mapped[str] = mapped_column(sa.String(10), default="charge")  # charge / repay
    amount: Mapped[Decimal] = mapped_column(sa.Numeric(12, 2), nullable=False)
    sale_order_id: Mapped[uuid.UUID | None] = mapped_column(sa.Uuid)
    note: Mapped[str | None] = mapped_column(sa.Text)
    due_date: Mapped[date | None] = mapped_column(sa.Date)
    settled: Mapped[bool] = mapped_column(sa.Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(sa.DateTime, server_default=sa.func.now())
    idempotency_key: Mapped[str | None] = mapped_column(sa.String(64), index=True)


class CustomerCreditProfile(Base):
    """客户信用档案 (§4.8) — 信用额度、默认账期、停赊标记。

    每个商户的每个赊账客户一条记录。余额由 CustomerReceivable 流水聚合得到，
    本表只存信用控制参数。
    """

    __tablename__ = "customer_credit_profiles"
    __table_args__ = (
        sa.UniqueConstraint(
            "merchant_id", "customer_name", name="uq_customer_profile_per_merchant"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(sa.Uuid, primary_key=True, default=uuid.uuid4)
    merchant_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid, sa.ForeignKey("merchants.id"), nullable=False
    )
    customer_name: Mapped[str] = mapped_column(sa.String(50), nullable=False)
    credit_limit: Mapped[Decimal | None] = mapped_column(
        sa.Numeric(12, 2)
    )  # 信用额度（NULL=无限制）
    default_credit_days: Mapped[int | None] = mapped_column(
        sa.Integer
    )  # 默认账期（天）
    is_blocked: Mapped[bool] = mapped_column(
        sa.Boolean, default=False
    )  # 停止赊账
    block_reason: Mapped[str | None] = mapped_column(sa.String(200))
    notes: Mapped[str | None] = mapped_column(sa.Text)
    created_at: Mapped[datetime] = mapped_column(sa.DateTime, server_default=sa.func.now())
    updated_at: Mapped[datetime] = mapped_column(
        sa.DateTime, server_default=sa.func.now(), onupdate=sa.func.now()
    )
