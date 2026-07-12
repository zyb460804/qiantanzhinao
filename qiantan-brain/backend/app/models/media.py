"""Media file tracking model (§5.9, §5.10).

Tracks uploaded media files with business context, retention policies,
and idempotency for offline upload queue support.
"""

from __future__ import annotations

import uuid
from datetime import datetime

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class MediaFile(Base):
    """Track uploaded media files for idempotency, retention, and audit.

    §5.10: 凭证类文件记录上传人、商户、关联业务和保留期限。
    """

    __tablename__ = "media_files"
    __table_args__ = (
        sa.UniqueConstraint(
            "merchant_id", "idempotency_key", name="uq_media_idempotency_per_merchant"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(sa.Uuid, primary_key=True, default=uuid.uuid4)
    merchant_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid, sa.ForeignKey("merchants.id"), nullable=False
    )
    original_name: Mapped[str] = mapped_column(sa.String(255), nullable=False)
    stored_name: Mapped[str] = mapped_column(sa.String(255), nullable=False)  # UUID-based
    media_type: Mapped[str] = mapped_column(sa.String(20), nullable=False)  # image/audio/document
    business_type: Mapped[str | None] = mapped_column(sa.String(50))  # purchase_cert/...
    business_payload: Mapped[dict | None] = mapped_column(sa.JSON)
    mime_type: Mapped[str | None] = mapped_column(sa.String(100))
    file_size: Mapped[int] = mapped_column(sa.BigInteger, default=0)
    file_path: Mapped[str] = mapped_column(sa.String(500), nullable=False)
    idempotency_key: Mapped[str | None] = mapped_column(sa.String(64), index=True)
    retention_days: Mapped[int | None] = mapped_column(sa.Integer)  # 凭证类保留期限
    uploaded_at: Mapped[datetime] = mapped_column(sa.DateTime, server_default=sa.func.now())
