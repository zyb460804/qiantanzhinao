"""Batch lifecycle tracking model — 一批一码追溯 (section 4.13).

批次状态机:
  pending_acceptance → sellable → near_expiry → sold_out
                    ↘ locked (快检不合格)
                    ↘ wasted (报损)
                    ↘ returned (退货)
  locked → recalled → destroyed
         ↘ removed (下架销毁)

POS FIFO 消费时自动跳过 locked/recalled/destroyed 批次。
"""

import uuid
from datetime import datetime
from decimal import Decimal

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class BatchLifecycle(Base):
    __tablename__ = "batch_lifecycles"

    id: Mapped[uuid.UUID] = mapped_column(sa.Uuid, primary_key=True, default=uuid.uuid4)
    merchant_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid, sa.ForeignKey("merchants.id"), nullable=False
    )
    product_id: Mapped[int] = mapped_column(
        sa.Integer, sa.ForeignKey("product_categories.id"), nullable=False
    )
    sku_id: Mapped[uuid.UUID | None] = mapped_column(sa.Uuid, sa.ForeignKey("product_skus.id"))
    batch_label: Mapped[str] = mapped_column(sa.String(50), nullable=False)
    purchase_date: Mapped[datetime] = mapped_column(sa.DateTime, nullable=False)
    purchase_qty: Mapped[Decimal] = mapped_column(sa.Numeric(10, 2), nullable=False)
    remaining_qty: Mapped[Decimal] = mapped_column(sa.Numeric(10, 2), nullable=False, default=0)
    expiry_date: Mapped[datetime | None] = mapped_column(sa.DateTime)

    # 临期批次级促销；不得改写 SKU 的常规售价。
    promotion_price: Mapped[Decimal | None] = mapped_column(sa.Numeric(10, 2))
    promotion_start_at: Mapped[datetime | None] = mapped_column(sa.DateTime)
    promotion_end_at: Mapped[datetime | None] = mapped_column(sa.DateTime)

    # 状态机: pending_acceptance/sellable/near_expiry/locked/sold_out/wasted/returned/
    # recalled/destroyed/removed
    status: Mapped[str] = mapped_column(sa.String(20), default="sellable")

    # --- 一批一码追溯字段 (section 4.13) ---
    # Historical schema keeps this relation application-enforced for SQLite compatibility.
    supplier_id: Mapped[uuid.UUID | None] = mapped_column(sa.Uuid)
    supplier_name: Mapped[str | None] = mapped_column(sa.String(50))  # 供应商名(冗余)
    origin: Mapped[str | None] = mapped_column(sa.String(100))  # 产地
    unit_cost: Mapped[Decimal | None] = mapped_column(sa.Numeric(10, 2))  # 实际单位成本
    certificates: Mapped[str | None] = mapped_column(sa.Text)  # 合格证/检疫证 JSON
    inspection_result: Mapped[str | None] = mapped_column(
        sa.String(50)
    )  # 快检结果: pass/fail/pending

    # --- 锁定/下架/召回 (section 4.14) ---
    locked_at: Mapped[datetime | None] = mapped_column(sa.DateTime)
    locked_reason: Mapped[str | None] = mapped_column(sa.String(200))
    locked_by: Mapped[str | None] = mapped_column(sa.String(50))
    destroyed_at: Mapped[datetime | None] = mapped_column(sa.DateTime)
    destroyed_reason: Mapped[str | None] = mapped_column(sa.String(200))

    # --- 销售去向缓存 (for traceability) ---
    sale_orders: Mapped[str | None] = mapped_column(sa.Text)  # JSON array of order_ids

    # --- 批次二维码数据 (§4.13) ---
    qr_data: Mapped[str | None] = mapped_column(sa.Text)  # JSON: {trace_code, urls, ...}

    last_check: Mapped[datetime] = mapped_column(sa.DateTime, server_default=sa.func.now())


# Allowed status transitions
BATCH_TRANSITIONS: dict[str, set[str]] = {
    "pending_acceptance": {"sellable", "returned", "wasted"},
    "sellable": {"near_expiry", "locked", "sold_out", "wasted", "returned"},
    "near_expiry": {"sellable", "locked", "sold_out", "wasted"},
    "locked": {"recalled", "removed", "sellable"},  # sellable = unlock after re-check
    "recalled": {"destroyed", "returned"},
    "destroyed": set(),
    "removed": set(),
    "sold_out": set(),
    "wasted": set(),
    "returned": set(),
}
