"""千摊经验云 API router — anonymous cross-merchant knowledge."""

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.schemas.cloud import BenchmarksResponse, TopProductsResponse, WeatherRulesResponse
from app.services.experience_cloud import (
    get_category_benchmarks,
    get_top_products,
    get_weather_impact_rules,
)


router = APIRouter(prefix="/api/v1/cloud", tags=["cloud"])


@router.get("/weather-rules", response_model=WeatherRulesResponse)
async def weather_impact_rules(
    product_name: str | None = None,
    db: AsyncSession = Depends(get_db),
):
    """Get anonymous weather impact patterns across all merchants."""
    rules = await get_weather_impact_rules(db, product_name)
    return {
        "code": 0,
        "data": rules,
        "message": "匿名聚合的环境影响规律" if rules else "数据不足,暂无法生成规律",
    }


@router.get("/benchmarks", response_model=BenchmarksResponse)
async def category_benchmarks(db: AsyncSession = Depends(get_db)):
    """Get cross-merchant benchmark data: average daily sales by category."""
    data = await get_category_benchmarks(db)
    return {
        "code": 0,
        "data": data,
        "message": "跨商户品类销售基准" if data else "数据不足",
    }


@router.get("/top-products", response_model=TopProductsResponse)
async def top_products_endpoint(
    limit: int = 5,
    db: AsyncSession = Depends(get_db),
):
    """Get most frequently traded products across all merchants."""
    data = await get_top_products(db, limit)
    return {"code": 0, "data": data}
