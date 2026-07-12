"""Behavior 路由的 Pydantic 响应模型。"""

from __future__ import annotations

from pydantic import BaseModel

from app.schemas.common import ApiResponse


class FeedbackResult(BaseModel):
    acknowledged: bool = True


class BehaviorProfile(BaseModel):
    merchant_id: str
    adoption_rate: float | None = None
    correction_rate: float | None = None
    event_distribution: dict | None = None


# ── 响应信封 ─────────────────────────────────────────────

FeedbackResponse = ApiResponse[FeedbackResult]
ProfileResponse = ApiResponse[BehaviorProfile]
