"""Merchant feedback model."""

import uuid as _uuid
from datetime import date as _date

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class MerchantFeedback(Base):
    """Store user feedback from the "我的" page."""

    __tablename__ = "merchant_feedback"
    __table_args__ = {"extend_existing": True}

    id: Mapped[_uuid.UUID] = mapped_column(sa.Uuid, primary_key=True, default=_uuid.uuid4)
    merchant_id: Mapped[_uuid.UUID] = mapped_column(
        sa.Uuid,
        sa.ForeignKey("merchants.id"),
        nullable=False,
        index=True,
    )
    content: Mapped[str] = mapped_column(sa.Text, nullable=False)
    page: Mapped[str | None] = mapped_column(sa.String(100), nullable=True)
    app_version: Mapped[str | None] = mapped_column(sa.String(20), nullable=True)
    created_at: Mapped[_date] = mapped_column(sa.Date, nullable=False, default=_date.today)
