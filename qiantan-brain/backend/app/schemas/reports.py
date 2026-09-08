"""Reports 路由的 Pydantic 响应模型。"""

from __future__ import annotations

from pydantic import BaseModel

from app.schemas.common import ApiResponse, DecimalNum


class DailyReportData(BaseModel):
    date: str
    revenue: DecimalNum
    cost: DecimalNum
    estimated_gross_profit: DecimalNum | None = None
    cash_balance: DecimalNum | None = None
    purchase_cost: DecimalNum | None = None
    estimated_cogs: DecimalNum | None = None
    waste_amount: DecimalNum
    order_count: int
    top_products: list[dict]
    slow_moving: list[dict]
    ai_summary: str
    action_items: list[str]
    health_score: int | None = None


class WeeklyReportData(BaseModel):
    week_start: str
    week_end: str
    revenue: DecimalNum
    cost: DecimalNum
    week_gross_profit: DecimalNum | None = None
    week_purchase_cost: DecimalNum | None = None
    week_estimated_cogs: DecimalNum | None = None
    waste_amount: DecimalNum
    daily_trend: list[dict]
    top_products: list[dict]
    waste_ranking: list[dict]
    adoption_rate: float | None = None
    ai_summary: str
    action_items: list[str]
    health_score: int | None = None


class TrendPoint(BaseModel):
    date: str
    revenue: DecimalNum
    cost: DecimalNum
    estimated_gross_profit: DecimalNum | None = None
    order_count: int


class ProductRankingItem(BaseModel):
    product_id: int
    product_name: str
    total_revenue: DecimalNum | None = None
    total_qty: DecimalNum | None = None
    waste_qty: DecimalNum | None = None
    rank: int


# ── 响应信封 ─────────────────────────────────────────────

DailyReportResponse = ApiResponse[DailyReportData]
WeeklyReportResponse = ApiResponse[WeeklyReportData]
TrendsResponse = ApiResponse[list[TrendPoint]]
ProductRankingResponse = ApiResponse[list[ProductRankingItem]]
