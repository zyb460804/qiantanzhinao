"""Merchant model."""

import uuid
from datetime import datetime

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class Merchant(Base):
    __tablename__ = "merchants"

    id: Mapped[uuid.UUID] = mapped_column(sa.Uuid, primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(sa.String(100), nullable=False)
    business_type: Mapped[str | None] = mapped_column(sa.String(50))
    location: Mapped[str | None] = mapped_column(sa.String(200))
    preferences: Mapped[dict] = mapped_column(sa.JSON, default=dict)
    # --- P0-1 鉴权字段 ---
    # 微信 openid：登录后绑定，唯一；一个微信用户对应一个商户（员工/市场管理员另建）
    wechat_openid: Mapped[str | None] = mapped_column(sa.String(64), unique=True)
    # 角色：owner（摊主）/ employee（员工）/ market_admin（市场管理员）
    role: Mapped[str] = mapped_column(sa.String(20), default="owner")
    created_at: Mapped[datetime] = mapped_column(sa.DateTime, server_default=sa.func.now())
    updated_at: Mapped[datetime] = mapped_column(
        sa.DateTime, server_default=sa.func.now(), onupdate=sa.func.now()
    )
