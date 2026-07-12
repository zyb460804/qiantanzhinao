"""Cloud / Experience Cloud 路由的 Pydantic 响应模型。"""

from __future__ import annotations

from pydantic import BaseModel

from app.schemas.common import ApiResponse


class WeatherRule(BaseModel):
    condition: str
    advice: str
    source: str | None = None


class BenchmarkItem(BaseModel):
    category: str
    metric: str
    value: float
    unit: str | None = None


class TopProductItem(BaseModel):
    product_name: str
    score: float
    rank: int


# ── 响应信封类型别名 ──────────────────────────────────────

WeatherRulesResponse = ApiResponse[list[WeatherRule]]
BenchmarksResponse = ApiResponse[list[BenchmarkItem]]
TopProductsResponse = ApiResponse[list[TopProductItem]]
