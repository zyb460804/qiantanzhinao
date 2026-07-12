"""POS 销售 / 支付 / 日结对账模型。

- SaleOrder: 一笔销售订单（零售/赊销），含一个或多个 SKU。
  状态流: pending → paid/credit → partial_refund → refunded
          pending → held → pending → paid/credit
- SaleOrderItem: 订单行项目，支持单品退款。
- Payment: 订单支付记录（现金/微信/支付宝/赊账），支持组合支付与退款。
- DailySettlement: 商户每日日结汇总，用于对账。
- Reconciliation: 日结对账记录（销售总额 vs 实收总额 vs 库存消耗）。

金额全部使用 Decimal（红线 #7）。
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class SaleOrder(Base):
    """POS 销售订单。"""

    __tablename__ = "sale_orders"
    __table_args__ = (
        sa.UniqueConstraint("merchant_id", "client_id", name="uq_sale_order_client_per_merchant"),
    )

    id: Mapped[uuid.UUID] = mapped_column(sa.Uuid, primary_key=True, default=uuid.uuid4)
    merchant_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid, sa.ForeignKey("merchants.id"), nullable=False
    )
    order_no: Mapped[str] = mapped_column(
        sa.String(32), nullable=False, unique=True
    )  # 如 POS20260712001
    status: Mapped[str] = mapped_column(
        sa.String(20), default="pending"
    )  # pending / paid / credit / partial / held / cancelled / partial_refund / refunded
    total_amount: Mapped[Decimal] = mapped_column(sa.Numeric(12, 2), default=Decimal("0"))
    paid_amount: Mapped[Decimal] = mapped_column(sa.Numeric(12, 2), default=Decimal("0"))
    refunded_amount: Mapped[Decimal] = mapped_column(sa.Numeric(12, 2), default=Decimal("0"))
    discount_amount: Mapped[Decimal] = mapped_column(sa.Numeric(12, 2), default=Decimal("0"))
    # 离线客户端幂等键
    client_id: Mapped[str | None] = mapped_column(sa.String(64), index=True)
    customer_name: Mapped[str | None] = mapped_column(sa.String(100))
    note: Mapped[str | None] = mapped_column(sa.Text)
    # --- 挂单（P0: POS 挂单/取单）---
    held_at: Mapped[datetime | None] = mapped_column(sa.DateTime)
    # --- 退款（P0: POS 退款/退货）---
    refund_reason: Mapped[str | None] = mapped_column(sa.String(500))
    refunded_at: Mapped[datetime | None] = mapped_column(sa.DateTime)
    created_at: Mapped[datetime] = mapped_column(sa.DateTime, server_default=sa.func.now())
    paid_at: Mapped[datetime | None] = mapped_column(sa.DateTime)


class SaleOrderItem(Base):
    """销售订单行项目。"""

    __tablename__ = "sale_order_items"

    id: Mapped[uuid.UUID] = mapped_column(sa.Uuid, primary_key=True, default=uuid.uuid4)
    order_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid, sa.ForeignKey("sale_orders.id"), nullable=False
    )
    merchant_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid, sa.ForeignKey("merchants.id"), nullable=False
    )
    sku_id: Mapped[uuid.UUID | None] = mapped_column(sa.Uuid, sa.ForeignKey("product_skus.id"))
    product_id: Mapped[int | None] = mapped_column(
        sa.Integer, sa.ForeignKey("product_categories.id")
    )
    quantity: Mapped[Decimal] = mapped_column(sa.Numeric(10, 2), nullable=False)
    unit: Mapped[str] = mapped_column(sa.String(20), nullable=False, default="斤")
    unit_price: Mapped[Decimal | None] = mapped_column(sa.Numeric(10, 2))
    total_amount: Mapped[Decimal | None] = mapped_column(sa.Numeric(12, 2))
    # --- 退款追踪（P0: POS 退款/退货）---
    refund_quantity: Mapped[Decimal] = mapped_column(sa.Numeric(10, 2), default=Decimal("0"))
    return_to_stock: Mapped[bool] = mapped_column(sa.Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(sa.DateTime, server_default=sa.func.now())


class Payment(Base):
    """支付流水。"""

    __tablename__ = "payments"

    id: Mapped[uuid.UUID] = mapped_column(sa.Uuid, primary_key=True, default=uuid.uuid4)
    merchant_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid, sa.ForeignKey("merchants.id"), nullable=False
    )
    order_id: Mapped[uuid.UUID | None] = mapped_column(sa.Uuid, sa.ForeignKey("sale_orders.id"))
    amount: Mapped[Decimal] = mapped_column(sa.Numeric(12, 2), nullable=False)
    method: Mapped[str] = mapped_column(
        sa.String(20), nullable=False
    )  # cash / wechat / alipay / card / credit
    status: Mapped[str] = mapped_column(
        sa.String(20), default="success"
    )  # success / failed / refunded
    transaction_id: Mapped[str | None] = mapped_column(sa.String(64), unique=True)
    note: Mapped[str | None] = mapped_column(sa.Text)
    created_at: Mapped[datetime] = mapped_column(sa.DateTime, server_default=sa.func.now())


class DailySettlement(Base):
    """每日日结汇总。"""

    __tablename__ = "daily_settlements"

    id: Mapped[uuid.UUID] = mapped_column(sa.Uuid, primary_key=True, default=uuid.uuid4)
    merchant_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid, sa.ForeignKey("merchants.id"), nullable=False
    )
    date: Mapped[date] = mapped_column(sa.Date, nullable=False)
    total_sales: Mapped[Decimal] = mapped_column(sa.Numeric(12, 2), default=Decimal("0"))
    total_payments: Mapped[Decimal] = mapped_column(sa.Numeric(12, 2), default=Decimal("0"))
    cash_amount: Mapped[Decimal] = mapped_column(sa.Numeric(12, 2), default=Decimal("0"))
    wechat_amount: Mapped[Decimal] = mapped_column(sa.Numeric(12, 2), default=Decimal("0"))
    alipay_amount: Mapped[Decimal] = mapped_column(sa.Numeric(12, 2), default=Decimal("0"))
    card_amount: Mapped[Decimal] = mapped_column(sa.Numeric(12, 2), default=Decimal("0"))
    credit_amount: Mapped[Decimal] = mapped_column(sa.Numeric(12, 2), default=Decimal("0"))
    # 差异 = 销售总额 - 实收总额（赊账单独列）
    diff_amount: Mapped[Decimal] = mapped_column(sa.Numeric(12, 2), default=Decimal("0"))
    status: Mapped[str] = mapped_column(sa.String(20), default="open")  # open / closed
    created_at: Mapped[datetime] = mapped_column(sa.DateTime, server_default=sa.func.now())
    closed_at: Mapped[datetime | None] = mapped_column(sa.DateTime)

    __table_args__ = (sa.UniqueConstraint("merchant_id", "date", name="uq_settlement_per_day"),)


class Reconciliation(Base):
    """日结对账记录（销售 vs 支付 vs 库存消耗）。"""

    __tablename__ = "reconciliations"

    id: Mapped[uuid.UUID] = mapped_column(sa.Uuid, primary_key=True, default=uuid.uuid4)
    merchant_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid, sa.ForeignKey("merchants.id"), nullable=False
    )
    date: Mapped[date] = mapped_column(sa.Date, nullable=False)
    sale_total: Mapped[Decimal] = mapped_column(sa.Numeric(12, 2), default=Decimal("0"))
    payment_total: Mapped[Decimal] = mapped_column(sa.Numeric(12, 2), default=Decimal("0"))
    inventory_cost_total: Mapped[Decimal] = mapped_column(sa.Numeric(12, 2), default=Decimal("0"))
    diff_amount: Mapped[Decimal] = mapped_column(sa.Numeric(12, 2), default=Decimal("0"))
    status: Mapped[str] = mapped_column(
        sa.String(20), default="pending"
    )  # pending / balanced / exception
    note: Mapped[str | None] = mapped_column(sa.Text)
    created_at: Mapped[datetime] = mapped_column(sa.DateTime, server_default=sa.func.now())

    __table_args__ = (sa.UniqueConstraint("merchant_id", "date", name="uq_reconciliation_per_day"),)
