"""Vision 路由的 Pydantic 请求/响应模型。"""

from __future__ import annotations

from pydantic import BaseModel

from app.schemas.common import ApiResponse


class VisionCategory(BaseModel):
    product_id: int
    name: str
    category_group: str | None = None
    unit: str | None = None


class VisionDetection(BaseModel):
    product_id: int | None = None
    name: str
    confidence: float


class VisionRecognizeData(BaseModel):
    detections: list[VisionDetection] = []
    suggested_product: VisionDetection | None = None
    processing_time_ms: int
    source: str  # edge_yolo / demo / placeholder


class VisionFeedbackData(BaseModel):
    original: str | None = None
    corrected: str | None = None


class VisionFeedbackRequest(BaseModel):
    original_prediction: str | None = None
    user_correction: str | None = None
    confidence: float | None = None
    image_hash: str | None = None


# ── 响应信封类型别名 ──────────────────────────────────────

VisionCategoriesResponse = ApiResponse[list[VisionCategory]]
VisionRecognizeResponse = ApiResponse[VisionRecognizeData]
VisionFeedbackResponse = ApiResponse[VisionFeedbackData]
