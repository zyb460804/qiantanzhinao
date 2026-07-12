"""市场管理后台模型 (section 4.18).

- Market: 市场/菜市场实体
- MarketMerchant: 商户入场登记
- MarketInspection: 市场巡检记录
- MarketComplaint: 投诉处理
- MarketNotice: 消息通知
"""

import uuid
from datetime import datetime

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class Market(Base):
    """菜市场实体。"""
    __tablename__ = "markets"

    id: Mapped[uuid.UUID] = mapped_column(sa.Uuid, primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(sa.String(100), nullable=False)
    address: Mapped[str | None] = mapped_column(sa.String(200))
    contact: Mapped[str | None] = mapped_column(sa.String(50))
    is_active: Mapped[bool] = mapped_column(sa.Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(sa.DateTime, server_default=sa.func.now())


class MarketMerchant(Base):
    """商户入场登记 — 摊位与市场的关联。"""
    __tablename__ = "market_merchants"

    id: Mapped[uuid.UUID] = mapped_column(sa.Uuid, primary_key=True, default=uuid.uuid4)
    market_id: Mapped[uuid.UUID] = mapped_column(sa.Uuid, sa.ForeignKey("markets.id"), nullable=False)
    merchant_id: Mapped[uuid.UUID] = mapped_column(sa.Uuid, sa.ForeignKey("merchants.id"), nullable=False)
    stall_number: Mapped[str | None] = mapped_column(sa.String(20))        # 摊位号
    category: Mapped[str | None] = mapped_column(sa.String(30))            # 蔬菜/肉类/水产/熟食
    license_number: Mapped[str | None] = mapped_column(sa.String(50))      # 营业执照号
    health_cert_expiry: Mapped[datetime | None] = mapped_column(sa.DateTime)  # 健康证到期
    food_safety_score: Mapped[int] = mapped_column(sa.Integer, default=100)   # 食安评分
    status: Mapped[str] = mapped_column(sa.String(20), default="active")    # active/suspended/closed
    created_at: Mapped[datetime] = mapped_column(sa.DateTime, server_default=sa.func.now())

    __table_args__ = (sa.UniqueConstraint("market_id", "merchant_id", name="uq_market_merchant"),)


class MarketInspection(Base):
    """市场巡检记录。"""
    __tablename__ = "market_inspections"

    id: Mapped[uuid.UUID] = mapped_column(sa.Uuid, primary_key=True, default=uuid.uuid4)
    market_id: Mapped[uuid.UUID] = mapped_column(sa.Uuid, sa.ForeignKey("markets.id"), nullable=False)
    merchant_id: Mapped[uuid.UUID | None] = mapped_column(sa.Uuid, sa.ForeignKey("merchants.id"))
    inspector: Mapped[str] = mapped_column(sa.String(50), nullable=False)
    inspection_type: Mapped[str] = mapped_column(sa.String(30), nullable=False)  # food_safety/equipment/hygiene
    result: Mapped[str] = mapped_column(sa.String(20), default="pass")  # pass/warn/fail
    notes: Mapped[str | None] = mapped_column(sa.Text)
    photos: Mapped[str | None] = mapped_column(sa.Text)
    created_at: Mapped[datetime] = mapped_column(sa.DateTime, server_default=sa.func.now())


class MarketComplaint(Base):
    """投诉处理。"""
    __tablename__ = "market_complaints"

    id: Mapped[uuid.UUID] = mapped_column(sa.Uuid, primary_key=True, default=uuid.uuid4)
    market_id: Mapped[uuid.UUID] = mapped_column(sa.Uuid, sa.ForeignKey("markets.id"), nullable=False)
    merchant_id: Mapped[uuid.UUID | None] = mapped_column(sa.Uuid, sa.ForeignKey("merchants.id"))
    complainant: Mapped[str | None] = mapped_column(sa.String(50))
    complaint_type: Mapped[str] = mapped_column(sa.String(30), nullable=False)
    description: Mapped[str] = mapped_column(sa.Text, nullable=False)
    status: Mapped[str] = mapped_column(sa.String(20), default="open")  # open/in_progress/resolved/closed
    resolution: Mapped[str | None] = mapped_column(sa.Text)
    created_at: Mapped[datetime] = mapped_column(sa.DateTime, server_default=sa.func.now())
    resolved_at: Mapped[datetime | None] = mapped_column(sa.DateTime)


class MarketNotice(Base):
    """市场通知/公告。"""
    __tablename__ = "market_notices"

    id: Mapped[uuid.UUID] = mapped_column(sa.Uuid, primary_key=True, default=uuid.uuid4)
    market_id: Mapped[uuid.UUID] = mapped_column(sa.Uuid, sa.ForeignKey("markets.id"), nullable=False)
    title: Mapped[str] = mapped_column(sa.String(100), nullable=False)
    content: Mapped[str] = mapped_column(sa.Text, nullable=False)
    notice_type: Mapped[str] = mapped_column(sa.String(20), default="info")  # info/warning/urgent
    is_active: Mapped[bool] = mapped_column(sa.Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(sa.DateTime, server_default=sa.func.now())
