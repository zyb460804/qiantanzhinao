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
        sa.DateTime, server_default=sa.func.now(), nullable=False
    )
    resolved_at: Mapped[datetime | None] = mapped_column(sa.DateTime)

    # 对齐 m4b5c6d7e8f9 建库迁移已建的索引（ORM 未声明导致 alembic 漂移），
    # 补声明而非删索引：保住按商户/按状态的死信查询性能。
    __table_args__ = (
        sa.Index("ix_dead_letter_events_merchant_id", "merchant_id"),
        sa.Index("ix_dead_letter_events_status", "status"),
    )
