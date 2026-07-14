"""管理员审计日志模型 — 记录平台管理员操作。"""

from __future__ import annotations

import uuid
from datetime import datetime

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class AdminAuditLog(Base):
    """管理员操作审计日志（独立于商户审计表 audit_logs）。"""

    __tablename__ = "admin_audit_logs"

    id: Mapped[uuid.UUID] = mapped_column(sa.Uuid, primary_key=True, default=uuid.uuid4)
    admin_id: Mapped[str] = mapped_column(sa.String(36), index=True)
    admin_email: Mapped[str] = mapped_column(sa.String(200))
    action: Mapped[str] = mapped_column(sa.String(50))  # login/logout/create/update/delete
    resource_type: Mapped[str | None] = mapped_column(
        sa.String(50)
    )  # tenant/plan/subscription/invoice
    resource_id: Mapped[str | None] = mapped_column(sa.String(36))
    detail: Mapped[str | None] = mapped_column(sa.Text)
    ip_address: Mapped[str | None] = mapped_column(sa.String(45))
    user_agent: Mapped[str | None] = mapped_column(sa.Text)
    created_at: Mapped[datetime] = mapped_column(sa.DateTime, server_default=sa.func.now())

    __table_args__ = (
        sa.Index("ix_admin_audit_logs_action", "action"),
        sa.Index("ix_admin_audit_logs_created_at", "created_at"),
    )
