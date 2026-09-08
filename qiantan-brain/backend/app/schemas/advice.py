"""Pydantic schemas for business advice and simulation."""

from uuid import UUID

from pydantic import BaseModel, Field

from app.schemas.common import ApiResponse, DecimalNum


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
    recommended_qty: DecimalNum | None = None
    confidence: float | None = None


class DailyAdviceResponse(BaseModel):
    recommendations: list[DailyAdviceItem] = []
    generated_at: str


class ScenarioInput(BaseModel):
    # P2-8 修复：进货量/成本/售价必须为正，拒绝负数与 0（此前 -10 也能算出垃圾结果）。
    purchase_qty: DecimalNum = Field(..., gt=0, le=1_000_000)
    unit_cost: DecimalNum = Field(..., gt=0, le=100_000_000)
    unit_price: DecimalNum = Field(..., gt=0, le=100_000_000)
    product_name: str | None = None
    estimated_sales_base: DecimalNum | None = None
    avg_historical_price: DecimalNum | None = None


class WhatIfRequest(BaseModel):
    product_id: int
    scenario: ScenarioInput  # 强类型校验：取代原先的裸 dict
    # merchant_id 由 get_merchant_id 依赖注入（token），不在 body 中传递


class ScenarioBatchRequest(BaseModel):
    """多场景对比请求。取代原先的裸 dict 入参。"""

    simulations: list[ScenarioInput]


class SimulationOutput(BaseModel):
    estimated_sales: DecimalNum
    estimated_revenue: DecimalNum
    total_cost: DecimalNum
    waste_qty: DecimalNum
    waste_loss: DecimalNum
    net_profit: DecimalNum
    margin_rate: float
    waste_rate: float


class ScenarioResponse(BaseModel):
    """与全局信封 {code, data} 对齐，data 内层用 SimulationOutput 强类型。"""

    code: int = 0
    data: list[SimulationOutput]


class SimulationComparison(BaseModel):
    baseline_net_profit: DecimalNum
    improvement: DecimalNum
    recommendation: str


class WhatIfResponse(BaseModel):
    input: ScenarioInput
    output: SimulationOutput
    comparison: SimulationComparison | None = None


class AdviceFeedbackRequest(BaseModel):
    recommendation_id: UUID
    was_adopted: bool
    actual_qty: DecimalNum | None = None


# ── 响应信封 ─────────────────────────────────────────────

DailyAdviceEnvelope = ApiResponse[DailyAdviceResponse]
WhatIfEnvelope = ApiResponse[dict]
