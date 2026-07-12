"""Business advice and simulation API router."""

import uuid

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import get_current_merchant, get_merchant_id
from app.core.timezone import local_days_ago
from app.database import get_db
from app.models.inventory import InventoryRecord
from app.models.merchant import Merchant
from app.models.product import ProductCategory
from app.schemas.advice import (
    ScenarioBatchRequest,
    ScenarioResponse,
    SimulationOutput,
    WhatIfRequest,
)
from app.schemas.common import AnyResponse
from app.services.advisor import build_daily_advice
from app.services.simulator import simulate_what_if


router = APIRouter(prefix="/api/v1", tags=["advice"])


@router.get("/advice/daily", response_model=AnyResponse)
async def get_daily_advice(
    merchant_id: uuid.UUID = Depends(get_merchant_id),
    db: AsyncSession = Depends(get_db),
):
    """Get daily business advice with three-line explainable format.

    编排逻辑已抽到 app.services.advisor.build_daily_advice，本路由只做薄封装。
    merchant_id 由 get_merchant_id 依赖注入（来自 token），不信任客户端传入。
    """
    data = await build_daily_advice(db, merchant_id)
    return {"code": 0, "data": data}


@router.post("/simulate/what-if", response_model=AnyResponse)
async def simulate_what_if_endpoint(
    body: WhatIfRequest,
    merchant_id: uuid.UUID = Depends(get_merchant_id),
    db: AsyncSession = Depends(get_db),
):
    """Run a What-if simulation with real DB data."""
    scenario = body.scenario
    product_id = body.product_id

    # Look up product name and history
    prod_query = select(ProductCategory).where(ProductCategory.id == product_id)
    prod_result = await db.execute(prod_query)
    product = prod_result.scalar_one_or_none()
    product_name = product.name if product else "白菜"

    # Get 7-day average sales as baseline
    seven_days_ago = local_days_ago(7)
    sales_query = select(func.sum(func.abs(InventoryRecord.quantity))).where(
        InventoryRecord.merchant_id == merchant_id,
        InventoryRecord.product_id == product_id,
        InventoryRecord.event_type == "sale",
        InventoryRecord.event_time >= seven_days_ago,
    )
    sales_result = await db.execute(sales_query)
    total_7d = float(sales_result.scalar() or 0)
    estimated_sales_base = round(total_7d / 7, 1) if total_7d > 0 else 18.0

    result = simulate_what_if(
        purchase_qty=scenario.get("purchase_qty", 0),
        unit_cost=scenario.get("unit_cost", 0),
        unit_price=scenario.get("unit_price", 0),
        product_name=product_name,
        estimated_sales_base=estimated_sales_base,
        avg_historical_price=scenario.get("avg_historical_price"),
    )

    return {"code": 0, "data": result}


@router.post("/simulate/scenario", response_model=ScenarioResponse)
async def simulate_scenario(
    body: ScenarioBatchRequest,
    merchant: Merchant = Depends(get_current_merchant),
    db: AsyncSession = Depends(get_db),
):
    """Run multi-scenario comparison.

    改造点（对照 千摊智脑-代码质量评审与团队提升方案.md）：
      1. 入参从裸 dict 改为强类型 ScenarioBatchRequest —— 字段自动校验。
      2. 删除了原先 "merchant_id 缺失则随机生成" 的危险逻辑（越权 / 幽灵商户）。
      3. 响应走 ScenarioResponse（信封 {code, data} + data 内层 SimulationOutput 强类型）。
    鉴权闸门：merchant 来自 token，纯无状态模拟也需登录（身份可控）。
    """
    results = []
    for sim in body.simulations:
        r = simulate_what_if(
            purchase_qty=sim.purchase_qty,
            unit_cost=sim.unit_cost,
            unit_price=sim.unit_price,
            product_name=sim.product_name or "白菜",
            estimated_sales_base=sim.estimated_sales_base or 18.0,
            avg_historical_price=sim.avg_historical_price,
        )
        results.append(SimulationOutput(**r["output"]))
    return ScenarioResponse(code=0, data=results)
