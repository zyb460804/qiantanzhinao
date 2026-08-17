"""经营行为跟踪 API router — adoption tracking + preference learning."""

import uuid
from datetime import date

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import get_merchant_id
from app.database import get_db
from app.schemas.behavior import FeedbackResponse, ProfileResponse
from app.schemas.common import AnyResponse
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


# ---------------------------------------------------------------------------
# 产品意见反馈（原 app/routers/feedback.py 合并至此，路径保持 /api/v1/feedback）
# ---------------------------------------------------------------------------

feedback_router = APIRouter(prefix="/api/v1", tags=["feedback"])


class ProductFeedbackRequest(BaseModel):
    """产品意见反馈提交 schema。"""

    content: str = Field(..., min_length=2, max_length=2000, description="反馈内容")
    page: str | None = Field(None, max_length=100, description="反馈来源页面")
    app_version: str | None = Field(None, max_length=20, description="小程序版本号")


@feedback_router.post("/feedback", response_model=AnyResponse)
async def submit_product_feedback(
    body: ProductFeedbackRequest,
    merchant_id: uuid.UUID = Depends(get_merchant_id),
    db: AsyncSession = Depends(get_db),
):
    """提交产品意见反馈。

    Feedback is stored as a row in the merchant_feedback table.
    Simple text feedback with optional page context and app version.
    """
    from app.models.feedback import MerchantFeedback

    feedback = MerchantFeedback(
        id=uuid.uuid4(),
        merchant_id=merchant_id,
        content=body.content,
        page=body.page,
        app_version=body.app_version,
        created_at=date.today(),
    )
    db.add(feedback)
    await db.commit()

    return {
        "code": 0,
        "message": "反馈已提交，感谢你的建议！",
        "data": {"feedback_id": str(feedback.id)},
    }
