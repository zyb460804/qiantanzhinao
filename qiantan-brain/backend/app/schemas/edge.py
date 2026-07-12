"""Edge 路由的 Pydantic 请求/响应模型。"""

from __future__ import annotations

from pydantic import BaseModel

from app.schemas.common import ApiResponse


class EdgeIngestData(BaseModel):
    accepted: bool = True
    merchant_id: str
    detection_count: int
    weight_g: float | None = None


# ── 响应信封类型别名 ──────────────────────────────────────

EdgeIngestResponse = ApiResponse[EdgeIngestData]
