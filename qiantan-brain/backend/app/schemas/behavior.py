"""Behavior 路由的 Pydantic 响应模型。"""

from __future__ import annotations

from pydantic import BaseModel, Field

from app.schemas.common import ApiResponse


class FeedbackResult(BaseModel):
    purchase_style: str
    profile_label: str
    recommended_multiplier: float
    total_decisions_recorded: int


class BehaviorProfile(BaseModel):
    purchase_style: str
    profile_label: str
    quantity_multiplier: float
    total_decisions: int
    adoption_rate: float | None = None
    correction_rate: float | None = None
    risk_profile: str


class AvailableProfile(BaseModel):
    key: str
    label: str
    desc: str


FeedbackResponse = ApiResponse[FeedbackResult]


class ProfileResponse(ApiResponse[BehaviorProfile]):
    available_profiles: list[AvailableProfile] = Field(default_factory=list)
