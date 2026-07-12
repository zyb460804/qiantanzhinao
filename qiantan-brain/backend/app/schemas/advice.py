"""Pydantic schemas for business advice and simulation."""

from uuid import UUID

from pydantic import BaseModel

from app.schemas.common import ApiResponse


class BasisItem(BaseModel):
    factor: str
    value: str
    impact: str  # "+" or "-" or "="


class DailyAdviceItem(BaseModel):
    product_id: int
    product_name: str
    suggestion: str
    basis: list[BasisItem] = []
    risk_warning: str | None = None
    recommended_qty: float | None = None
    confidence: float | None = None


class DailyAdviceResponse(BaseModel):
    recommendations: list[DailyAdviceItem] = []
    generated_at: str


class WhatIfRequest(BaseModel):
    merchant_id: UUID
    product_id: int
    scenario: dict  # {"purchase_qty": 50, "unit_cost": 0.3, "unit_price": 1.5}


class ScenarioInput(BaseModel):
    purchase_qty: float
    unit_cost: float
    unit_price: float
    product_name: str | None = None
    estimated_sales_base: float | None = None
    avg_historical_price: float | None = None


class ScenarioBatchRequest(BaseModel):
    """多场景对比请求。取代原先的裸 dict 入参。"""

    simulations: list[ScenarioInput]


class SimulationOutput(BaseModel):
    estimated_sales: float
    estimated_revenue: float
    total_cost: float
    waste_qty: float
    waste_loss: float
    net_profit: float
    margin_rate: float
    waste_rate: float


class ScenarioResponse(BaseModel):
    """与全局信封 {code, data} 对齐，data 内层用 SimulationOutput 强类型。"""

    code: int = 0
    data: list[SimulationOutput]


class SimulationComparison(BaseModel):
    baseline_net_profit: float
    improvement: float
    recommendation: str


class WhatIfResponse(BaseModel):
    input: ScenarioInput
    output: SimulationOutput
    comparison: SimulationComparison | None = None


class AdviceFeedbackRequest(BaseModel):
    recommendation_id: UUID
    was_adopted: bool
    actual_qty: float | None = None


# ── 响应信封 ─────────────────────────────────────────────

DailyAdviceEnvelope = ApiResponse[DailyAdviceResponse]
WhatIfEnvelope = ApiResponse[dict]
