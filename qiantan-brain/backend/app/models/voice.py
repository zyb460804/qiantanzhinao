"""Voice log model."""

import uuid
from datetime import datetime

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class VoiceLog(Base):
    __tablename__ = "voice_logs"

    id: Mapped[uuid.UUID] = mapped_column(sa.Uuid, primary_key=True, default=uuid.uuid4)
    merchant_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid, sa.ForeignKey("merchants.id"), nullable=False
    )
    audio_url: Mapped[str | None] = mapped_column(sa.String(500))
    asr_text: Mapped[str] = mapped_column(sa.Text, default="")
    parsed_event: Mapped[dict | None] = mapped_column(sa.JSON)
    status: Mapped[str] = mapped_column(
        sa.String(20), default="pending"
    )  # pending, parsed, confirmed, voided
    correction_count: Mapped[int] = mapped_column(sa.Integer, default=0)
    # --- 离线同步客户端幂等键 ---
    client_id: Mapped[str | None] = mapped_column(sa.String(64), index=True)
    created_at: Mapped[datetime] = mapped_column(sa.DateTime, server_default=sa.func.now())
