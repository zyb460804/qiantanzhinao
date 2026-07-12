"""Reports 路由的 Pydantic 响应模型。"""

from __future__ import annotations

from pydantic import BaseModel

from app.schemas.common import ApiResponse


class DailyReportData(BaseModel):
    date: str
    revenue: float
    cost: float
    estimated_gross_profit: float | None = None
    cash_balance: float | None = None
    purchase_cost: float | None = None
    estimated_cogs: float | None = None
    waste_amount: float
    order_count: int
    top_products: list[dict]
    slow_moving: list[dict]
    ai_summary: str
    action_items: list[str]
    health_score: int | None = None


class WeeklyReportData(BaseModel):
    week_start: str
    week_end: str
    revenue: float
    cost: float
    week_gross_profit: float | None = None
    week_purchase_cost: float | None = None
    week_estimated_cogs: float | None = None
    waste_amount: float
    daily_trend: list[dict]
    top_products: list[dict]
    waste_ranking: list[dict]
    adoption_rate: float | None = None
    ai_summary: str
    action_items: list[str]
    health_score: int | None = None


class TrendPoint(BaseModel):
    date: str
    revenue: float
    cost: float
    estimated_gross_profit: float | None = None
    order_count: int


class ProductRankingItem(BaseModel):
    product_id: int
    product_name: str
    total_revenue: float | None = None
    total_qty: float | None = None
    waste_qty: float | None = None
    rank: int


# ── 响应信封 ─────────────────────────────────────────────

DailyReportResponse = ApiResponse[DailyReportData]
WeeklyReportResponse = ApiResponse[WeeklyReportData]
TrendsResponse = ApiResponse[list[TrendPoint]]
ProductRankingResponse = ApiResponse[list[ProductRankingItem]]
