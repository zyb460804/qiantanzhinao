"""Twin / Digital Twin 路由的 Pydantic 响应模型。"""

from __future__ import annotations

from pydantic import BaseModel

from app.schemas.common import ApiResponse, DecimalNum


class DashboardData(BaseModel):
    today_revenue: DecimalNum
    today_profit: DecimalNum
    today_order_count: int
    inventory_value: DecimalNum
    estimated_gross_profit: DecimalNum | None = None
    cash_balance: DecimalNum | None = None
    estimated_cogs: DecimalNum | None = None
    trend_7d: list[dict]


class InventoryMirrorData(BaseModel):
    items: list[dict]
    lifecycle_heatmap: list[dict] | None = None


class BusinessMirrorData(BaseModel):
    sales_7d: list[dict]
    sales_30d: list[dict] | None = None
    sale_count: int | None = None
    avg_order_value: DecimalNum | None = None


class RiskMirrorData(BaseModel):
    risks: list[dict]  # [{name, level, score, description}]


# ── 响应信封 ─────────────────────────────────────────────

DashboardResponse = ApiResponse[DashboardData]
InventoryMirrorResponse = ApiResponse[InventoryMirrorData]
BusinessMirrorResponse = ApiResponse[BusinessMirrorData]
RiskMirrorResponse = ApiResponse[RiskMirrorData]
