"""经营行为跟踪 API router — adoption tracking + preference learning."""

import uuid

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import get_merchant_id
from app.database import get_db
from app.schemas.behavior import FeedbackResponse, ProfileResponse
from app.services.behavior import (
    PROFILES,
    get_merchant_profile,
    record_adoption,
)


router = APIRouter(prefix="/api/v1/behavior", tags=["behavior"])


class AdoptionFeedback(BaseModel):
    merchant_id: uuid.UUID
    recommendation_id: uuid.UUID | None = None
    was_adopted: bool
    actual_quantity: float | None = None


@router.post("/feedback", response_model=FeedbackResponse)
async def submit_feedback(
    body: AdoptionFeedback,
    merchant_id: uuid.UUID = Depends(get_merchant_id),
    db: AsyncSession = Depends(get_db),
):
    """Submit adoption feedback after a recommendation is acted upon.

    Called when a merchant confirms a voice log or manually adjusts inventory
    — the system learns from whether the recommendation was followed.
    merchant_id 来自 token（get_merchant_id），不再信任客户端 body。
    """
    if not body.recommendation_id:
        return {
            "code": 0,
            "message": "No recommendation to track (manual entry)",
        }

    result = await record_adoption(
        db,
        merchant_id=merchant_id,
        recommendation_id=body.recommendation_id,
        was_adopted=body.was_adopted,
        actual_quantity=body.actual_quantity,
    )

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
    """Get merchant behavioral profile for personalization."""
    profile = await get_merchant_profile(db, merchant_id)
    return {
        "code": 0,
        "data": profile,
        "available_profiles": [
            {"key": k, "label": v["label"], "desc": v["description"]} for k, v in PROFILES.items()
        ],
    }
