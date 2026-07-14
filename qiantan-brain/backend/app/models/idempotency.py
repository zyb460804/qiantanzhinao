"""Persistent idempotency records for retry-safe write requests."""

from __future__ import annotations

from datetime import datetime

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class IdempotencyRecord(Base):
    """Cached outcome for one principal, operation, and idempotency key."""

    __tablename__ = "idempotency_records"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    idempotency_key: Mapped[str] = mapped_column(sa.String(128), nullable=False)
    tenant_id: Mapped[str] = mapped_column(sa.String(128), nullable=False)
    operation: Mapped[str] = mapped_column(sa.String(160), nullable=False)
    request_hash: Mapped[str] = mapped_column(sa.String(64), nullable=False)
    response_body: Mapped[str | None] = mapped_column(sa.Text)
    content_type: Mapped[str | None] = mapped_column(sa.String(120))
    status_code: Mapped[int] = mapped_column(nullable=False, default=102)
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime,
        nullable=False,
        server_default=sa.func.now(),
    )

    __table_args__ = (
        sa.UniqueConstraint(
            "tenant_id",
            "operation",
            "idempotency_key",
            name="uq_idempotency_per_tenant_operation",
        ),
        sa.Index("ix_idempotency_records_key", "idempotency_key"),
        sa.Index("ix_idempotency_records_tenant", "tenant_id"),
    )
