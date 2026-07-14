"""Edge event persistence model.

Stores incoming events from edge devices (scale, camera, etc.)
with idempotency protection via event_id unique constraint.
"""

import uuid
from datetime import datetime

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class EdgeEvent(Base):
    __tablename__ = "edge_events"

    id: Mapped[uuid.UUID] = mapped_column(sa.Uuid, primary_key=True, default=uuid.uuid4)
    event_id: Mapped[str] = mapped_column(sa.String(64), nullable=False)
    device_id: Mapped[str | None] = mapped_column(sa.String(64), nullable=True, index=True)
    merchant_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid, sa.ForeignKey("merchants.id"), nullable=False, index=True
    )
    tenant_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.Uuid, sa.ForeignKey("tenants.id"), nullable=True, index=True
    )
    event_type: Mapped[str] = mapped_column(
        sa.String(30), nullable=False
    )  # weight/vision/heartbeat
    occurred_at: Mapped[datetime] = mapped_column(sa.DateTime, nullable=False)
    payload: Mapped[str | None] = mapped_column(sa.Text, nullable=True)  # JSON payload
    model_version: Mapped[str | None] = mapped_column(sa.String(30), nullable=True)
    sequence: Mapped[int | None] = mapped_column(sa.Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(sa.DateTime, server_default=sa.func.now())

    __table_args__ = (
        sa.UniqueConstraint("event_id", name="uq_edge_events_event_id"),
        sa.Index("ix_edge_events_event_id", "event_id"),
        sa.Index("ix_edge_events_merchant_occurred", "merchant_id", "occurred_at"),
    )
