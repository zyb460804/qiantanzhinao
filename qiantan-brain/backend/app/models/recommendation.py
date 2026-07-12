"""Recommendation model."""

import uuid
from datetime import datetime

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class Recommendation(Base):
    __tablename__ = "recommendations"

    id: Mapped[uuid.UUID] = mapped_column(sa.Uuid, primary_key=True, default=uuid.uuid4)
    merchant_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid, sa.ForeignKey("merchants.id"), nullable=False
    )
    product_id: Mapped[int] = mapped_column(
        sa.Integer, sa.ForeignKey("product_categories.id"), nullable=False
    )
    # SKU 关联（P0-B 真正收尾）：建议精确到 SKU，category 仅兼容。
    sku_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.Uuid, sa.ForeignKey("product_skus.id"), index=True
    )
    suggestion: Mapped[str] = mapped_column(sa.Text, nullable=False)
    basis: Mapped[list] = mapped_column(sa.JSON, default=list)
    risk_warning: Mapped[str | None] = mapped_column(sa.Text)
    recommended_qty: Mapped[float | None] = mapped_column(sa.Numeric(10, 2))
    confidence: Mapped[float | None] = mapped_column(sa.Numeric(4, 2))
    was_adopted: Mapped[bool | None] = mapped_column(sa.Boolean)
    actual_deviation: Mapped[float | None] = mapped_column(sa.Numeric(10, 2))
    created_at: Mapped[datetime] = mapped_column(sa.DateTime, server_default=sa.func.now())
