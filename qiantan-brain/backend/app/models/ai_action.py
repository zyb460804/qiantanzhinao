"""AI 行动追踪模型 — 把 AI 建议变成可执行、可审计、可复盘的任务。

对应战略文档 #5 #7：AI 建议不能止于"推荐"，必须知道有没有被执行、效果如何。
"""

from __future__ import annotations

import uuid
from datetime import datetime

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class AIAction(Base):
    """AI 建议的一次可执行动作。"""

    __tablename__ = "ai_actions"

    id: Mapped[uuid.UUID] = mapped_column(sa.Uuid, primary_key=True, default=uuid.uuid4)
    merchant_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid, sa.ForeignKey("merchants.id"), nullable=False
    )
    # 来源建议（可选；直接指令也可能没有对应 recommendation）
    recommendation_id: Mapped[uuid.UUID | None] = mapped_column(sa.Uuid)
    # 动作类型：clearance(清货) / purchase(采购) / price(改价) / stock(备货) / custom
    action_type: Mapped[str] = mapped_column(sa.String(20), nullable=False)
    status: Mapped[str] = mapped_column(
        sa.String(20), default="pending"
    )  # pending / executed / rejected / failed / cancelled
    title: Mapped[str] = mapped_column(sa.String(100), nullable=False)
    payload: Mapped[dict | None] = mapped_column(sa.JSON)  # 执行所需参数
    result: Mapped[dict | None] = mapped_column(sa.JSON)  # 执行结果/错误信息
    executed_by: Mapped[str | None] = mapped_column(sa.String(50))  # merchant / employee / system
    created_at: Mapped[datetime] = mapped_column(sa.DateTime, server_default=sa.func.now())
    executed_at: Mapped[datetime | None] = mapped_column(sa.DateTime)
