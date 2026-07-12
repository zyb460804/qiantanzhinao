"""设备管理模型 (sections 4.15, 4.16).

- Device: 智能秤/摄像头/价签等硬件设备注册
- PriceDisplay: 顾客价目屏同步状态
"""

import uuid
from datetime import datetime

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class Device(Base):
    """IoT device registry — 智能秤、摄像头、电子价签等。"""
    __tablename__ = "devices"

    id: Mapped[uuid.UUID] = mapped_column(sa.Uuid, primary_key=True, default=uuid.uuid4)
    merchant_id: Mapped[uuid.UUID] = mapped_column(sa.Uuid, sa.ForeignKey("merchants.id"), nullable=False)
    device_type: Mapped[str] = mapped_column(sa.String(30), nullable=False)  # scale/camera/esl/printer
    device_name: Mapped[str] = mapped_column(sa.String(50), nullable=False)
    serial_number: Mapped[str | None] = mapped_column(sa.String(64), unique=True)
    firmware_version: Mapped[str | None] = mapped_column(sa.String(20))
    is_active: Mapped[bool] = mapped_column(sa.Boolean, default=True)
    last_heartbeat: Mapped[datetime | None] = mapped_column(sa.DateTime)
    last_error: Mapped[str | None] = mapped_column(sa.String(200))
    config: Mapped[str | None] = mapped_column(sa.Text)  # JSON config
    created_at: Mapped[datetime] = mapped_column(sa.DateTime, server_default=sa.func.now())

    __table_args__ = (sa.UniqueConstraint("merchant_id", "serial_number", name="uq_device_serial_per_merchant"),)


class PriceDisplay(Base):
    """电子价签 / 顾客价目屏同步状态 (section 4.16)。"""
    __tablename__ = "price_displays"

    id: Mapped[uuid.UUID] = mapped_column(sa.Uuid, primary_key=True, default=uuid.uuid4)
    merchant_id: Mapped[uuid.UUID] = mapped_column(sa.Uuid, sa.ForeignKey("merchants.id"), nullable=False)
    device_id: Mapped[uuid.UUID | None] = mapped_column(sa.Uuid, sa.ForeignKey("devices.id"))
    sku_id: Mapped[uuid.UUID] = mapped_column(sa.Uuid, sa.ForeignKey("product_skus.id"), nullable=False)
    current_price: Mapped[float] = mapped_column(sa.Float, default=0)
    price_source: Mapped[str] = mapped_column(sa.String(20), default="manual")  # manual/ai_discount/clearance
    sync_status: Mapped[str] = mapped_column(sa.String(20), default="synced")  # synced/pending/failed
    sync_error: Mapped[str | None] = mapped_column(sa.String(200))
    last_synced: Mapped[datetime | None] = mapped_column(sa.DateTime)
    created_at: Mapped[datetime] = mapped_column(sa.DateTime, server_default=sa.func.now())

    __table_args__ = (sa.UniqueConstraint("merchant_id", "sku_id", name="uq_price_display_per_sku"),)
