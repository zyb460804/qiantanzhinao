"""
models_offline.py — 离线记账所需模型增量（参考实现 / 教学样例）
────────────────────────────────────────────────────────────────────────
对应 PRD §4.1.2 幂等键 与 附录 A：在 InventoryRecord / VoiceLog 上增加
`client_id` 字段，并建立 (merchant_id, client_id) 唯一约束——这是「不记两次」
的唯一可信保证，必须由数据库约束兜底，不能只靠前端去重。

⚠️ 这是「参考实现」。接入生产时，请把下面的 client_id 字段与唯一约束
直接加进现有的 app/models/inventory.py、app/models/voice.py（不要新建表）。
下方 InventoryRecordOffline 仅用于演示「增量应该长什么样」。
"""

from __future__ import annotations

import uuid
from datetime import datetime

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class InventoryRecordOffline(Base):
    """演示用：在现有 InventoryRecord 基础上仅多出 client_id 与唯一约束。"""

    __tablename__ = "inventory_records"
    __table_args__ = (
        # 幂等兜底：同一商户下 client_id 唯一；跨商户天然隔离（merchant_id 在约束内）
        sa.UniqueConstraint(
            "merchant_id", "client_id", name="uq_ir_merchant_client"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(sa.Uuid, primary_key=True, default=uuid.uuid4)
    merchant_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid, sa.ForeignKey("merchants.id"), nullable=False
    )
    # —— 离线幂等键（新增）——
    client_id: Mapped[str | None] = mapped_column(sa.String(64), nullable=True, index=True)

    product_id: Mapped[int] = mapped_column(sa.Integer, nullable=False)
    quantity: Mapped[float] = mapped_column(sa.Numeric(10, 2), nullable=False)
    unit: Mapped[str] = mapped_column(sa.String(20), nullable=False)
    total_amount: Mapped[float | None] = mapped_column(sa.Numeric(12, 2))
    event_type: Mapped[str] = mapped_column(sa.String(30), nullable=False)
    event_time: Mapped[datetime] = mapped_column(sa.DateTime, nullable=False)
    source: Mapped[str] = mapped_column(sa.String(30), default="offline")
    created_at: Mapped[datetime] = mapped_column(sa.DateTime, server_default=sa.func.now())


# VoiceLog 同理增加 client_id + 唯一约束（此处仅示意字段）：
#     client_id: Mapped[str | None] = mapped_column(sa.String(64), nullable=True)
#     __table_args__ = (sa.UniqueConstraint("merchant_id", "client_id",
#                                           name="uq_vl_merchant_client"),)
