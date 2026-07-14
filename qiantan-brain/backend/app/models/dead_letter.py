"""Dead letter queue model — persisted failed sync events for retry and diagnosis."""

from __future__ import annotations

import uuid
from datetime import datetime

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSON
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class DeadLetterEvent(Base):
    """A sync event that failed and requires operator attention or retry."""

    __tablename__ = "dead_letter_events"

    id: Mapped[uuid.UUID] = mapped_column(sa.Uuid, primary_key=True, default=uuid.uuid4)
    merchant_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid, sa.ForeignKey("merchants.id"), nullable=False
    )
    idempotency_key: Mapped[str | None] = mapped_column(sa.String(64))
    event_type: Mapped[str] = mapped_column(sa.String(30), nullable=False)
    # purchase / sale / waste / stocktake
    payload: Mapped[dict] = mapped_column(JSON, nullable=False)
    error_message: Mapped[str] = mapped_column(sa.Text, nullable=False)
    retry_count: Mapped[int] = mapped_column(sa.Integer, default=0)
    max_retries: Mapped[int] = mapped_column(sa.Integer, default=3)
    next_retry_at: Mapped[datetime | None] = mapped_column(sa.DateTime)
    status: Mapped[str] = mapped_column(sa.String(20), default="pending")
    # pending / retrying / permanent_failure / resolved
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime, server_default=sa.func.now(), nullable=True
    )
    resolved_at: Mapped[datetime | None] = mapped_column(sa.DateTime)