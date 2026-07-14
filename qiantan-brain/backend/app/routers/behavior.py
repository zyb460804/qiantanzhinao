"""经营行为跟踪 API router — adoption tracking + preference learning."""

import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import get_merchant_id
from app.database import get_db
from app.schemas.behavior import FeedbackResponse, ProfileResponse
from app.services.behavior import PROFILES, get_merchant_profile, record_adoption


router = APIRouter(prefix="/api/v1/behavior", tags=["behavior"])


class AdoptionFeedback(BaseModel):
    recommendation_id: uuid.UUID | None = None
    was_adopted: bool
    actual_quantity: float | None = None


@router.post("/feedback", response_model=FeedbackResponse)
async def submit_feedback(
    body: AdoptionFeedback,
    merchant_id: uuid.UUID = Depends(get_merchant_id),
    db: AsyncSession = Depends(get_db),
):
    """提交建议采纳反馈；商户身份只来自认证上下文。"""
    if not body.recommendation_id:
        return {
            "code": 0,
            "message": "No recommendation to track (manual entry)",
        }

    try:
        result = await record_adoption(
            db,
            merchant_id=merchant_id,
            recommendation_id=body.recommendation_id,
            was_adopted=body.was_adopted,
            actual_quantity=body.actual_quantity,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    return {
        "code": 0,
        "data": result,
        "message": f"已记录: 商户偏好为{result['profile_label']}",
    }


@router.get("/profile", response_model=ProfileResponse)
async def get_profile(
    merchant_id: uuid.UUID = Depends(get_merchant_id),
    db: AsyncSession = Depends(get_db),
):
    """获取当前商户的行为画像和可用画像说明。"""
    profile = await get_merchant_profile(db, merchant_id)
    return {
        "code": 0,
        "data": profile,
        "available_profiles": [
            {"key": key, "label": definition["label"], "desc": definition["description"]}
            for key, definition in PROFILES.items()
        ],
    }
