"""Edge 路由的 Pydantic 请求/响应模型。"""

from __future__ import annotations

from pydantic import BaseModel

from app.schemas.common import ApiResponse


class EdgeIngestData(BaseModel):
    accepted: bool = True
    merchant_id: str
    # 幂等去重命中 (merchant_id, event_id) 时无检测结果，默认 0
    detection_count: int = 0
    weight_g: float | None = None
    # 客户端显式携带 event_id 时回显（服务端代生成时也为 UUID，始终回显）
    event_id: str | None = None
    duplicate: bool = False


# ── 响应信封类型别名 ──────────────────────────────────────

EdgeIngestResponse = ApiResponse[EdgeIngestData]
