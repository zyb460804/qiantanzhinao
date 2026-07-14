"""Stocktake (inventory reconciliation) models.

Supports the full cycle: book inventory display → actual count input →
variance calculation → adjustment record generation → loss tracking.
"""

import uuid
from datetime import datetime

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class StocktakeSession(Base):
    """A single盘点 session — one merchant counting all (or subset of) products."""

    __tablename__ = "stocktake_sessions"

    id: Mapped[uuid.UUID] = mapped_column(sa.Uuid, primary_key=True, default=uuid.uuid4)
    merchant_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid, sa.ForeignKey("merchants.id"), nullable=False
    )
    status: Mapped[str] = mapped_column(
        sa.String(20), default="in_progress"
    )  # in_progress / completed / cancelled
    total_book_qty: Mapped[float | None] = mapped_column(sa.Numeric(12, 2))
    total_actual_qty: Mapped[float | None] = mapped_column(sa.Numeric(12, 2))
    total_variance: Mapped[float | None] = mapped_column(sa.Numeric(12, 2))
    total_loss_amount: Mapped[float | None] = mapped_column(sa.Numeric(12, 2))
    notes: Mapped[str | None] = mapped_column(sa.Text)
    started_at: Mapped[datetime] = mapped_column(sa.DateTime, server_default=sa.func.now())
    completed_at: Mapped[datetime | None] = mapped_column(sa.DateTime)


class StocktakeItem(Base):
    """Per-product snapshot line within a stocktake session."""

    __tablename__ = "stocktake_items"
    __table_args__ = (
        sa.UniqueConstraint(
            "session_id",
            "product_id",
            name="uq_stocktake_item_session_product",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(sa.Uuid, primary_key=True, default=uuid.uuid4)
    session_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid, sa.ForeignKey("stocktake_sessions.id"), nullable=False
    )
    merchant_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid, sa.ForeignKey("merchants.id"), nullable=False
    )
    product_id: Mapped[int] = mapped_column(
        sa.Integer, sa.ForeignKey("product_categories.id"), nullable=False
    )
    book_qty: Mapped[float] = mapped_column(sa.Numeric(10, 2), nullable=False)
    # Rows are created when the session starts so book_qty is a real snapshot.
    # actual_qty/variance stay NULL until the operator submits this line.
    actual_qty: Mapped[float | None] = mapped_column(sa.Numeric(10, 2))
    variance: Mapped[float | None] = mapped_column(sa.Numeric(10, 2))
    unit: Mapped[str] = mapped_column(sa.String(20), nullable=False, default="斤")
    # Possible cause: natural_loss / unrecorded_sale / weighing_error / theft / unknown
    variance_reason: Mapped[str | None] = mapped_column(sa.String(50))
    adjustment_record_id: Mapped[uuid.UUID | None] = mapped_column(sa.Uuid)
    created_at: Mapped[datetime] = mapped_column(sa.DateTime, server_default=sa.func.now())
