"""Auth-related models — token revocation for logout."""

from datetime import datetime

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class AuthRevokedToken(Base):
    """吊销的 JWT 记录（按 jti）。注销后该令牌立即失效。"""

    __tablename__ = "auth_revoked_tokens"

    jti: Mapped[str] = mapped_column(sa.String(64), primary_key=True)
    revoked_at: Mapped[datetime] = mapped_column(sa.DateTime, server_default=sa.func.now())
    expires_at: Mapped[datetime | None] = mapped_column(sa.DateTime, nullable=True)
