"""Twin / Digital Twin 路由的 Pydantic 响应模型。"""

from __future__ import annotations

from pydantic import BaseModel

from app.schemas.common import ApiResponse, DecimalNum


class DashboardData(BaseModel):
    """经营台指标（口径：收入=净销售额扣退款；成本=已售成本 COGS；毛利=收入-成本；
    采购支出 today_purchase_cost 单列，不与成本混用）。"""

    today_revenue: DecimalNum
    today_cost: DecimalNum | None = None
    today_profit: DecimalNum
    today_purchase_cost: DecimalNum | None = None
    today_refund_total: DecimalNum | None = None
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
