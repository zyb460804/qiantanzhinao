"""Inventory record model."""

import uuid
from datetime import datetime
from decimal import Decimal

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class InventoryRecord(Base):
    __tablename__ = "inventory_records"
    __table_args__ = (
        sa.UniqueConstraint(
            "merchant_id",
            "idempotency_key",
            name="uq_inventory_idempotency_per_merchant",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(sa.Uuid, primary_key=True, default=uuid.uuid4)
    merchant_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid, sa.ForeignKey("merchants.id"), nullable=False
    )
    product_id: Mapped[int] = mapped_column(
        sa.Integer, sa.ForeignKey("product_categories.id"), nullable=False
    )
    # SKU 关联（P0-B：取消 category 孤儿化）。
    # 账本真正挂在 SKU 上，category 仅保留兼容。可空以兼容旧数据，
    # 新写入必须尽量由 sku_service.resolve_sku_id 解析并填充。
    sku_id: Mapped[uuid.UUID | None] = mapped_column(sa.Uuid, sa.ForeignKey("product_skus.id"))
    quantity: Mapped[Decimal] = mapped_column(sa.Numeric(10, 2), nullable=False)
    unit: Mapped[str] = mapped_column(sa.String(20), nullable=False)
    unit_cost: Mapped[Decimal | None] = mapped_column(sa.Numeric(10, 2))
    unit_price: Mapped[Decimal | None] = mapped_column(sa.Numeric(10, 2))
    total_amount: Mapped[Decimal | None] = mapped_column(sa.Numeric(12, 2))
    event_type: Mapped[str] = mapped_column(sa.String(30), nullable=False)
    event_time: Mapped[datetime] = mapped_column(sa.DateTime, nullable=False)
    source: Mapped[str] = mapped_column(sa.String(30), default="voice")
    voice_log_id: Mapped[uuid.UUID | None] = mapped_column(sa.Uuid)
    batch_label: Mapped[str | None] = mapped_column(sa.String(50))
    notes: Mapped[str | None] = mapped_column(sa.Text)
    # --- 幂等键（P0: 防止网络重试造成重复记账）---
    # 由客户端生成并随每条业务请求携带；服务端唯一约束保证同一条动作只入账一次。
    # 允许为 NULL（旧数据 / 手动补录），NULL 不参与唯一冲突（PG/SQLite 均如此）。
    idempotency_key: Mapped[str | None] = mapped_column(sa.String(64), index=True)
    # --- 离线同步客户端幂等键（P0: 设备端生成，保证断网重连后不重复）---
    client_id: Mapped[str | None] = mapped_column(sa.String(64), index=True)
    client_reference: Mapped[str | None] = mapped_column(sa.String(64))  # 客户端业务单号/流水号
    # --- 撤销/作废字段（P0: 记录纠错） ---
    is_voided: Mapped[bool] = mapped_column(sa.Boolean, default=False, nullable=False)
    voided_at: Mapped[datetime | None] = mapped_column(sa.DateTime)
    void_reason: Mapped[str | None] = mapped_column(sa.String(200))
    voided_by: Mapped[str | None] = mapped_column(sa.String(50))  # voice/manual/stocktake
    # --- 修改追踪（编辑已确认记录时生成冲正记录） ---
    original_record_id: Mapped[uuid.UUID | None] = mapped_column(sa.Uuid)  # 指向被修正的原记录
    is_correction: Mapped[bool] = mapped_column(sa.Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(sa.DateTime, server_default=sa.func.now())


class CurrentInventory(Base):
    """Summarized view of current stock per product. Refreshed by trigger/logic."""

    __tablename__ = "current_inventory"

    merchant_id: Mapped[uuid.UUID] = mapped_column(sa.Uuid, primary_key=True)
    product_id: Mapped[int] = mapped_column(sa.Integer, primary_key=True)
    # SKU 维度（P0-B 收尾）：当前 category:sku 一对一，sku_id 可空兼容；
    # 未来一品类多 SKU 时，把 sku_id 提升为主键并重建视图。
    sku_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.Uuid, sa.ForeignKey("product_skus.id"), index=True
    )
    current_qty: Mapped[Decimal] = mapped_column(sa.Numeric(12, 2))
    avg_cost: Mapped[Decimal | None] = mapped_column(sa.Numeric(10, 2))
    last_updated: Mapped[datetime | None] = mapped_column(sa.DateTime)
