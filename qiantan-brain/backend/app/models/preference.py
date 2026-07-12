"""Merchant preference model."""

import uuid

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class MerchantPreference(Base):
    __tablename__ = "merchant_preferences"

    id: Mapped[int] = mapped_column(sa.Integer, primary_key=True, autoincrement=True)
    merchant_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid, sa.ForeignKey("merchants.id"), unique=True, nullable=False
    )
    risk_profile: Mapped[str] = mapped_column(sa.String(20), default="neutral")
    voice_dialect: Mapped[str] = mapped_column(sa.String(30), default="mandarin")
    favorite_products: Mapped[list[int] | None] = mapped_column(sa.JSON, default=list)
    avg_order_size: Mapped[float | None] = mapped_column(sa.Numeric(10, 2))
    preference_data: Mapped[dict] = mapped_column(sa.JSON, default=dict)
