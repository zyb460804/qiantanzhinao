"""Purchase list models — AI recommendation → executable list → acceptance → inventory.

状态机（阶段A 采购验收闭环）:
  draft → confirmed → partial_arrival → accepted → stored → completed
                                                ↘ returned (退货抵扣应付)

PurchaseItem 承载到货验收明细：缺斤、破损、不合格、退货、补货、包装与称重。
"""

import uuid
from datetime import datetime
from decimal import Decimal

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class PurchaseList(Base):
    """A purchase list aggregating AI recommendations into an actionable checklist.

    Status flow: draft → confirmed → partial_arrival → accepted → stored → completed
    """

    __tablename__ = "purchase_lists"

    id: Mapped[uuid.UUID] = mapped_column(sa.Uuid, primary_key=True, default=uuid.uuid4)
    merchant_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid, sa.ForeignKey("merchants.id"), nullable=False
    )
    # draft / confirmed / partial_arrival / accepted / stored / completed / cancelled / returned
    status: Mapped[str] = mapped_column(sa.String(20), default="draft")
    total_estimated_cost: Mapped[Decimal | None] = mapped_column(sa.Numeric(12, 2))
    total_actual_cost: Mapped[Decimal | None] = mapped_column(sa.Numeric(12, 2))
    item_count: Mapped[int] = mapped_column(sa.Integer, default=0)
    notes: Mapped[str | None] = mapped_column(sa.Text)
    # 预计到货时间
    expected_arrival_date: Mapped[datetime | None] = mapped_column(sa.DateTime)
    # 付款状态: unpaid / partial / credit / paid
    payment_status: Mapped[str] = mapped_column(sa.String(20), default="unpaid")
    paid_amount: Mapped[Decimal] = mapped_column(sa.Numeric(12, 2), default=Decimal("0"))
    created_at: Mapped[datetime] = mapped_column(sa.DateTime, server_default=sa.func.now())
    confirmed_at: Mapped[datetime | None] = mapped_column(sa.DateTime)
    accepted_at: Mapped[datetime | None] = mapped_column(sa.DateTime)
    stored_at: Mapped[datetime | None] = mapped_column(sa.DateTime)
    completed_at: Mapped[datetime | None] = mapped_column(sa.DateTime)


class PurchaseItem(Base):
    """Individual line item in a purchase list — 含到货验收明细."""

    __tablename__ = "purchase_items"

    id: Mapped[uuid.UUID] = mapped_column(sa.Uuid, primary_key=True, default=uuid.uuid4)
    list_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid, sa.ForeignKey("purchase_lists.id"), nullable=False
    )
    merchant_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid, sa.ForeignKey("merchants.id"), nullable=False
    )
    recommendation_id: Mapped[uuid.UUID | None] = mapped_column(sa.Uuid)
    supplier_id: Mapped[uuid.UUID | None] = mapped_column(sa.Uuid, sa.ForeignKey("suppliers.id"))
    product_id: Mapped[int] = mapped_column(
        sa.Integer, sa.ForeignKey("product_categories.id"), nullable=False
    )
    sku_id: Mapped[uuid.UUID | None] = mapped_column(sa.Uuid, sa.ForeignKey("product_skus.id"))
    # --- 下单数据 ---
    recommended_qty: Mapped[Decimal | None] = mapped_column(sa.Numeric(10, 2))
    actual_qty: Mapped[Decimal] = mapped_column(sa.Numeric(10, 2), nullable=False)
    unit: Mapped[str] = mapped_column(sa.String(20), nullable=False, default="斤")
    estimated_unit_cost: Mapped[Decimal | None] = mapped_column(sa.Numeric(10, 2))
    actual_unit_cost: Mapped[Decimal | None] = mapped_column(sa.Numeric(10, 2))
    estimated_cost: Mapped[Decimal | None] = mapped_column(sa.Numeric(12, 2))
    actual_cost: Mapped[Decimal | None] = mapped_column(sa.Numeric(12, 2))
    # pending / purchased / returned / cancelled
    status: Mapped[str] = mapped_column(sa.String(20), default="pending")
    reason: Mapped[str | None] = mapped_column(sa.Text)
    deviation_ratio: Mapped[Decimal | None] = mapped_column(sa.Numeric(5, 2))
    # --- 到货验收（阶段A 采购验收闭环）---
    # 包装与称重
    package_count: Mapped[int | None] = mapped_column(sa.Integer)  # 筐/袋/件数
    gross_weight: Mapped[Decimal | None] = mapped_column(sa.Numeric(10, 2))  # 毛重
    tare_weight: Mapped[Decimal | None] = mapped_column(sa.Numeric(10, 2))   # 皮重
    net_weight: Mapped[Decimal | None] = mapped_column(sa.Numeric(10, 2))    # 净重
    # 到货差异
    arrival_qty: Mapped[Decimal | None] = mapped_column(sa.Numeric(10, 2))   # 实际到货
    shortage_qty: Mapped[Decimal | None] = mapped_column(sa.Numeric(10, 2))  # 缺斤
    damaged_qty: Mapped[Decimal | None] = mapped_column(sa.Numeric(10, 2))   # 破损
    rejected_qty: Mapped[Decimal | None] = mapped_column(sa.Numeric(10, 2))  # 不合格拒收
    returned_qty: Mapped[Decimal | None] = mapped_column(sa.Numeric(10, 2))  # 退货
    replenish_qty: Mapped[Decimal | None] = mapped_column(sa.Numeric(10, 2)) # 供应商补货
    # 验收结果
    accepted_qty: Mapped[Decimal | None] = mapped_column(sa.Numeric(10, 2))  # 合格入库量
    quality_ok: Mapped[bool | None] = mapped_column(sa.Boolean)              # 质量是否合格
    acceptance_photos: Mapped[str | None] = mapped_column(sa.Text)           # 到货照片(JSON array)
    # 凭证(合格证/检疫证, JSON array)
    certificates: Mapped[str | None] = mapped_column(sa.Text)
    acceptance_notes: Mapped[str | None] = mapped_column(sa.Text)            # 验收备注
    # Inventory record generated on confirm (idempotency)
    inventory_record_id: Mapped[uuid.UUID | None] = mapped_column(sa.Uuid)
    created_at: Mapped[datetime] = mapped_column(sa.DateTime, server_default=sa.func.now())
    purchased_at: Mapped[datetime | None] = mapped_column(sa.DateTime)
    accepted_at: Mapped[datetime | None] = mapped_column(sa.DateTime)
