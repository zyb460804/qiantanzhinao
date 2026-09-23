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
        sa.CheckConstraint(
            "status IN ('pending','paid','credit','partial',"
            "'held','cancelled','partial_refund','refunded')",
            name="ck_sale_order_status",
        ),
        {"comment": "POS 销售订单：零售/赊销/挂单/退款状态机，含离线幂等 client_id"},
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
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime, server_default=sa.func.now(), nullable=False
    )
    paid_at: Mapped[datetime | None] = mapped_column(sa.DateTime)


class SaleOrderItem(Base):
    """销售订单行项目。"""

    __tablename__ = "sale_order_items"
    __table_args__ = {"comment": "销售订单行项目：数量/单价/FIFO 成本，支持单品退款与回库"}

    id: Mapped[uuid.UUID] = mapped_column(sa.Uuid, primary_key=True, default=uuid.uuid4)
    order_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid, sa.ForeignKey("sale_orders.id", ondelete="CASCADE"), nullable=False
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
    unit_cost: Mapped[Decimal | None] = mapped_column(sa.Numeric(10, 2))
    total_amount: Mapped[Decimal | None] = mapped_column(sa.Numeric(12, 2))
    # --- 退款追踪（P0: POS 退款/退货）---
    refund_quantity: Mapped[Decimal] = mapped_column(sa.Numeric(10, 2), default=Decimal("0"))
    return_to_stock: Mapped[bool] = mapped_column(sa.Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime, server_default=sa.func.now(), nullable=False
    )


class Payment(Base):
    """支付流水。"""

    __tablename__ = "payments"
    __table_args__ = (
        sa.CheckConstraint(
            "method IN ('cash','wechat','alipay','card','credit')",
            name="ck_payment_method",
        ),
        sa.CheckConstraint(
            "status IN ('success','failed','refunded')",
            name="ck_payment_status",
        ),
        {"comment": "支付流水：现金/微信/支付宝/卡/赊账，支持组合支付与退款关联订单"},
    )

    id: Mapped[uuid.UUID] = mapped_column(sa.Uuid, primary_key=True, default=uuid.uuid4)
    merchant_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid, sa.ForeignKey("merchants.id"), nullable=False
    )
    order_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.Uuid, sa.ForeignKey("sale_orders.id", ondelete="CASCADE")
    )
    amount: Mapped[Decimal] = mapped_column(sa.Numeric(12, 2), nullable=False)
    method: Mapped[str] = mapped_column(
        sa.String(20), nullable=False
    )  # cash / wechat / alipay / card / credit
    status: Mapped[str] = mapped_column(
        sa.String(20), default="success"
    )  # success / failed / refunded
    transaction_id: Mapped[str | None] = mapped_column(sa.String(64), unique=True)
    note: Mapped[str | None] = mapped_column(sa.Text)
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime, server_default=sa.func.now(), nullable=False
    )


class DailySettlement(Base):
    """每日日结汇总。"""

    __tablename__ = "daily_settlements"

    id: Mapped[uuid.UUID] = mapped_column(sa.Uuid, primary_key=True, default=uuid.uuid4)
    merchant_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid, sa.ForeignKey("merchants.id"), nullable=False
    )
    date: Mapped[date] = mapped_column(sa.Date, nullable=False)
    # 净额口径（QA 拍板）：total_sales = 销售总额 - 当日退款合计（净销售额）；
    # 退款合计等完整统计存 snapshot.refunds_total，无独立列。
    total_sales: Mapped[Decimal] = mapped_column(sa.Numeric(12, 2), default=Decimal("0"))
    # 净实收：正向收款 - 退款流水（退款行金额为负），与 total_sales 同口径。
    total_payments: Mapped[Decimal] = mapped_column(sa.Numeric(12, 2), default=Decimal("0"))
    cash_amount: Mapped[Decimal] = mapped_column(sa.Numeric(12, 2), default=Decimal("0"))
    wechat_amount: Mapped[Decimal] = mapped_column(sa.Numeric(12, 2), default=Decimal("0"))
    alipay_amount: Mapped[Decimal] = mapped_column(sa.Numeric(12, 2), default=Decimal("0"))
    card_amount: Mapped[Decimal] = mapped_column(sa.Numeric(12, 2), default=Decimal("0"))
    credit_amount: Mapped[Decimal] = mapped_column(sa.Numeric(12, 2), default=Decimal("0"))
    # 差异 = 净销售额(total_sales) - 净实收(total_payments) - 赊账净额(credit_amount)，
    # 正常记账恒为 0；非 0 即真实账目差异（漏记流水/手工改账）。
    diff_amount: Mapped[Decimal] = mapped_column(sa.Numeric(12, 2), default=Decimal("0"))
    status: Mapped[str] = mapped_column(sa.String(20), default="open")  # open / closed
    # P2-6 修复：关闭时的完整统计快照（order_count/refund_amount/estimated_cogs 等）。
    # 此前这些字段只在实时统计里有，closed 回显丢字段（order_count=None）。
    snapshot: Mapped[dict | None] = mapped_column(sa.JSON)
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime, server_default=sa.func.now(), nullable=False
    )
    closed_at: Mapped[datetime | None] = mapped_column(sa.DateTime)

    __table_args__ = (
        sa.UniqueConstraint("merchant_id", "date", name="uq_settlement_per_day"),
        {"comment": "每日日结：按渠道收款汇总与销售-实收差异，关闭时保存统计快照"},
    )


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
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime, server_default=sa.func.now(), nullable=False
    )

    __table_args__ = (
        sa.UniqueConstraint("merchant_id", "date", name="uq_reconciliation_per_day"),
        {"comment": "日结对账记录：销售总额 vs 支付总额 vs 库存消耗成本的每日核对"},
    )
