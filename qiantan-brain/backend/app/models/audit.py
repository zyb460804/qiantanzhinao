"""Audit log model — tracks all record modifications, voids, and corrections."""

import uuid
from datetime import datetime

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class AuditLog(Base):
    """Immutable audit trail for every data-altering operation.

    Action types: create / edit / void / stocktake / purchase_confirm
    """

    __tablename__ = "audit_logs"

    id: Mapped[uuid.UUID] = mapped_column(sa.Uuid, primary_key=True, default=uuid.uuid4)
    merchant_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid, sa.ForeignKey("merchants.id"), nullable=False
    )
    action: Mapped[str] = mapped_column(sa.String(30), nullable=False)
    # Polymorphic target — which table / record was affected
    target_table: Mapped[str] = mapped_column(sa.String(50), nullable=False)
    target_id: Mapped[str] = mapped_column(sa.String(64), nullable=False)
    # Snapshot of data before / after (JSON)
    before_data: Mapped[dict | None] = mapped_column(sa.JSON)
    after_data: Mapped[dict | None] = mapped_column(sa.JSON)
    reason: Mapped[str | None] = mapped_column(sa.String(500))
    operator: Mapped[str] = mapped_column(sa.String(50), default="merchant")
    request_id: Mapped[str | None] = mapped_column(sa.String(64))  # §5.14: 请求追踪
    created_at: Mapped[datetime] = mapped_column(sa.DateTime, server_default=sa.func.now())
