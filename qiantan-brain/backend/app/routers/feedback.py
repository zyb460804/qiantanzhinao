"""Feedback API router — user feedback collection."""

import uuid
from datetime import date

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import get_merchant_id
from app.database import get_db
from app.schemas.common import AnyResponse


router = APIRouter(prefix="/api/v1", tags=["feedback"])


class FeedbackRequest(BaseModel):
    """Feedback submission schema."""

    content: str = Field(..., min_length=2, max_length=2000, description="反馈内容")
    page: str | None = Field(None, max_length=100, description="反馈来源页面")
    app_version: str | None = Field(None, max_length=20, description="小程序版本号")


@router.post("/feedback", response_model=AnyResponse)
async def submit_feedback(
    body: FeedbackRequest,
    merchant_id: uuid.UUID = Depends(get_merchant_id),
    db: AsyncSession = Depends(get_db),
):
    """Submit user feedback.

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
