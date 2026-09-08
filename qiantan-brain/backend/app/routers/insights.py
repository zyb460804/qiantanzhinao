"""算法能力闭环 — 定价 / 报童模型建议 API。

提供两个面向商户的算法建议端点：
- GET /api/v1/insights/pricing-suggestions    临期/库存驱动的动态定价建议
- GET /api/v1/insights/newsvendor-suggestions 报童模型最优进货量建议

两者都基于 inventory_records 的当前库存快照 + 近 N 个 CST 业务日销量统计，
复用现有 dynamic_pricing / inventory_optimizer 服务，不重复造轮子。
"""

from __future__ import annotations

import math
import uuid
from datetime import timedelta

from fastapi import APIRouter, Depends, Query
from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import get_current_merchant
from app.core.timezone import (
    cst_date_of_utc_naive,
    cst_days_ago_bounds_utc,
    cst_today,
    format_utc_iso,
    utc_now,
)
from app.database import get_db
from app.models.catalog import ProductSKU
from app.models.inventory import InventoryRecord
from app.models.merchant import Merchant
from app.models.product import ProductCategory
from app.schemas.common import AnyResponse
from app.services.dynamic_pricing import PriceTier, recommend_price
from app.services.inventory_optimizer import recommend_for_perishable


router = APIRouter(prefix="/api/v1/insights", tags=["insights"])

_URGENCY_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3}


async def _load_inventory_snapshot(
    db: AsyncSession,
    merchant_id: uuid.UUID,
) -> list[dict]:
    """聚合当前库存快照（与 /api/v1/inventory/current 同口径）。

    按 product_id 分组，quantity 求和，排除已作废记录；
    avg_cost 用正 quantity 的加权成本；sku_id 取该商品当前账本中最大的 SKU。
    """
    query = (
        select(
            InventoryRecord.product_id,
            func.max(InventoryRecord.sku_id).label("sku_id"),
            func.coalesce(func.sum(InventoryRecord.quantity), 0).label("qty"),
            func.coalesce(
                func.sum(
                    case(
                        (
                            InventoryRecord.quantity > 0,
                            InventoryRecord.unit_cost * InventoryRecord.quantity,
                        ),
                        else_=0,
                    )
                )
                / func.nullif(
                    func.sum(
                        case(
                            (InventoryRecord.quantity > 0, InventoryRecord.quantity),
                            else_=0,
                        )
                    ),
                    0,
                ),
                0,
            ).label("avg_cost"),
            func.max(InventoryRecord.unit).label("unit"),
        )
        .where(
            InventoryRecord.merchant_id == merchant_id,
            InventoryRecord.is_voided.is_(False),
        )
        .group_by(InventoryRecord.product_id)
    )
    rows = (await db.execute(query)).all()
    return [
        {
            "product_id": row.product_id,
            "sku_id": row.sku_id,
            "current_qty": float(row.qty or 0),
            "avg_cost": float(row.avg_cost or 0),
            "unit": row.unit or "斤",
        }
        for row in rows
    ]


async def _load_sales_stats(
    db: AsyncSession,
    merchant_id: uuid.UUID,
    days: int,
) -> dict[tuple[int, uuid.UUID | None], dict[str, float]]:
    """统计近 N 个 CST 业务日每个 (product_id, sku_id) 的日均销量与标准差。

    缺失日期补 0，使无销量的日子参与均值/波动计算。
    """
    start = cst_days_ago_bounds_utc(days)[0]
    rows = (
        await db.execute(
            select(
                InventoryRecord.product_id,
                InventoryRecord.sku_id,
                InventoryRecord.event_time,
                InventoryRecord.quantity,
            ).where(
                InventoryRecord.merchant_id == merchant_id,
                InventoryRecord.event_type == "sale",
                InventoryRecord.is_voided.is_(False),
                InventoryRecord.event_time >= start,
            )
        )
    ).all()

    daily_by_key: dict[tuple[int, uuid.UUID | None], dict[str, float]] = {}
    for product_id, sku_id, event_time, quantity in rows:
        key = (product_id, sku_id)
        day = cst_date_of_utc_naive(event_time).isoformat()
        by_day = daily_by_key.setdefault(key, {})
        by_day[day] = by_day.get(day, 0.0) + abs(float(quantity or 0))

    today = cst_today()
    dates = [(today - timedelta(days=days - 1 - i)).isoformat() for i in range(days)]
    stats: dict[tuple[int, uuid.UUID | None], dict[str, float]] = {}
    for key, by_day in daily_by_key.items():
        values = [by_day.get(d, 0.0) for d in dates]
        mean = sum(values) / days
        variance = sum((v - mean) ** 2 for v in values) / days
        std = math.sqrt(variance)
        stats[key] = {"mean_demand": round(mean, 2), "std_demand": round(std, 2)}
    return stats


def _sale_price(
    sku: ProductSKU | None,
    category: ProductCategory | None,
) -> float | None:
    """取商品售价：优先 SKU 默认售价，其次品类默认售价。"""
    if sku is not None and sku.default_sale_price is not None:
        return float(sku.default_sale_price)
    if category is not None and category.default_price is not None:
        return float(category.default_price)
    return None


def _sales_stats_for(
    stats: dict[tuple[int, uuid.UUID | None], dict[str, float]],
    product_id: int,
    sku_id: uuid.UUID | None,
) -> dict[str, float] | None:
    """按商品粒度取销量统计。

    优先精确匹配 (product_id, sku_id)；若账本 sku_id 与销量记录 sku_id 不一致
    （历史数据兼容），则回退到同一 product_id 下所有 SKU 的统计合并。
    """
    exact = stats.get((product_id, sku_id))
    if exact is not None:
        return exact

    candidates = [v for (pid, _sid), v in stats.items() if pid == product_id]
    if not candidates:
        return None
    if len(candidates) == 1:
        return candidates[0]
    mean = sum(c["mean_demand"] for c in candidates)
    std = math.sqrt(sum(c["std_demand"] ** 2 for c in candidates))
    return {"mean_demand": round(mean, 2), "std_demand": round(std, 2)}


def _resolve_catalog(
    snapshots: list[dict],
    sku_map: dict[uuid.UUID, ProductSKU],
    category_map: dict[int, ProductCategory],
    sku_by_name: dict[str, ProductSKU] | None = None,
    require_stock: bool = True,
) -> list[dict]:
    """把库存快照与 SKU/品类目录合并成统一的算法输入行。

    require_stock=True 时仅保留当前库存 > 0 的商品（定价建议需要库存）；
    报童建议只关心销量与价格，允许零库存商品也参与计算。
    sku_id 缺失或不属于本商户时，按品类名回退匹配本商户 SKU（历史数据兼容）。
    """
    sku_by_name = sku_by_name or {}
    items = []
    for row in snapshots:
        product_id = row["product_id"]
        sku = sku_map.get(row["sku_id"]) if row["sku_id"] else None
        category = category_map.get(product_id)
        if sku is None and category is not None:
            sku = sku_by_name.get(category.name)
        price = _sale_price(sku, category)
        current_qty = row["current_qty"]
        if price is None or price <= 0:
            continue
        if require_stock and current_qty <= 0:
            continue

        if sku is not None:
            product_name = sku.name
            category_group = sku.category_group or (category.category_group if category else None)
            unit = row["unit"] or sku.canonical_unit or "斤"
            shelf_life_hours = sku.shelf_life_hours
        else:
            product_name = category.name if category else f"商品{product_id}"
            category_group = category.category_group if category else None
            unit = row["unit"] or (category.unit if category else "斤")
            shelf_life_hours = category.shelf_life_hours if category else None

        items.append(
            {
                "product_id": product_id,
                "sku_id": row["sku_id"],
                "product_name": product_name,
                "unit": unit,
                "current_qty": current_qty,
                "avg_cost": row["avg_cost"],
                "sale_price": price,
                "category": category_group or "default",
                "shelf_life_hours": shelf_life_hours,
            }
        )
    return items


async def _catalog_maps(
    db: AsyncSession,
    merchant_id: uuid.UUID,
    snapshots: list[dict],
) -> tuple[dict[uuid.UUID, ProductSKU], dict[int, ProductCategory], dict[str, ProductSKU]]:
    """批量加载 SKU 与品类目录，避免 N+1 查询。

    额外返回按名称索引的本商户 SKU，供 sku_id 缺失的历史账本回退匹配。
    """
    sku_ids = {row["sku_id"] for row in snapshots if row["sku_id"]}
    sku_map: dict[uuid.UUID, ProductSKU] = {}
    if sku_ids:
        result = await db.execute(
            select(ProductSKU).where(
                ProductSKU.id.in_(sku_ids),
                ProductSKU.merchant_id == merchant_id,
            )
        )
        sku_map = {sku.id: sku for sku in result.scalars().all()}

    merchant_skus = (
        (
            await db.execute(
                select(ProductSKU).where(
                    ProductSKU.merchant_id == merchant_id,
                    ProductSKU.is_active == True,  # noqa: E712
                )
            )
        )
        .scalars()
        .all()
    )
    sku_by_name = {sku.name: sku for sku in merchant_skus}

    product_ids = {row["product_id"] for row in snapshots}
    category_map: dict[int, ProductCategory] = {}
    if product_ids:
        result = await db.execute(
            select(ProductCategory).where(ProductCategory.id.in_(product_ids))
        )
        category_map = {category.id: category for category in result.scalars().all()}
    return sku_map, category_map, sku_by_name


@router.get("/pricing-suggestions", response_model=AnyResponse)
async def pricing_suggestions(
    days: int = Query(7, ge=1, le=90, description="销量统计回溯天数"),
    price_tier: str = Query(
        "balanced",
        pattern="^(balanced|conservative|aggressive)$",
        description="定价档位：balanced/conservative/aggressive",
    ),
    limit: int = Query(20, ge=1, le=100, description="最多返回建议数"),
    merchant: Merchant = Depends(get_current_merchant),
    db: AsyncSession = Depends(get_db),
):
    """生成临期/库存驱动的动态定价建议。

    数据口径：
    - 当前库存：inventory_records 按 product_id 聚合（排除作废记录）。
    - 日销量：近 days 个 CST 业务日按 (product_id, sku_id) 统计。
    - 无销量数据时用 current_qty / 7（保底 0.5）作为日预测。
    """
    snapshots = await _load_inventory_snapshot(db, merchant.id)
    if not snapshots:
        generated_at = format_utc_iso(utc_now())
        return {
            "code": 0,
            "data": {
                "generated_at": generated_at,
                "count": 0,
                "price_tier": price_tier,
                "suggestions": [],
            },
        }

    sku_map, category_map, sku_by_name = await _catalog_maps(db, merchant.id, snapshots)
    stats = await _load_sales_stats(db, merchant.id, days)
    items = _resolve_catalog(snapshots, sku_map, category_map, sku_by_name)

    suggestions = []
    for item in items:
        sales = _sales_stats_for(stats, item["product_id"], item["sku_id"])
        if sales:
            daily_forecast = sales["mean_demand"]
        else:
            daily_forecast = max(item["current_qty"] / 7.0, 0.5)

        rec = recommend_price(
            product_name=item["product_name"],
            category=item["category"],
            unit_cost=item["avg_cost"] or 0,
            original_price=item["sale_price"],
            current_inventory=item["current_qty"],
            daily_forecast=daily_forecast,
            hours_since_arrival=0,
            hours_until_close=8,
            shelf_life_hours=item["shelf_life_hours"],
            price_tier=PriceTier(price_tier),
        )
        suggestions.append(
            {
                "product_id": item["product_id"],
                "sku_id": str(item["sku_id"]) if item["sku_id"] else None,
                "product_name": rec.product_name,
                "unit": item["unit"],
                "current_qty": round(item["current_qty"], 2),
                "daily_forecast": round(daily_forecast, 2),
                "original_price": round(rec.original_price, 2),
                "unit_cost": round(item["avg_cost"] or 0, 2),
                "recommended_price": round(rec.recommended_price, 2),
                "discount_pct": round(rec.discount_pct, 2),
                "strategy": rec.strategy.value,
                "urgency": rec.urgency,
                "reason": rec.reason,
                "expected_revenue": round(rec.expected_revenue, 2),
                "expected_waste_pct": round(rec.expected_waste_pct, 2),
                "alternative_prices": [
                    {
                        "strategy": str(alt.get("strategy") or ""),
                        "price": round(float(alt.get("price") or 0), 2),
                    }
                    for alt in (rec.alternative_prices or [])
                ],
            }
        )

    suggestions.sort(key=lambda x: _URGENCY_ORDER.get(x["urgency"], 99))
    limited = suggestions[:limit]
    return {
        "code": 0,
        "data": {
            "generated_at": format_utc_iso(utc_now()),
            "count": len(limited),
            "price_tier": price_tier,
            "suggestions": limited,
        },
    }


@router.get("/newsvendor-suggestions", response_model=AnyResponse)
async def newsvendor_suggestions(
    days: int = Query(14, ge=1, le=90, description="销量统计回溯天数"),
    limit: int = Query(20, ge=1, le=100, description="最多返回建议数"),
    merchant: Merchant = Depends(get_current_merchant),
    db: AsyncSession = Depends(get_db),
):
    """生成报童模型最优进货量建议。

    仅对近期有销量（mean_demand > 0）且有售价的商品计算；
    mean_demand < 5 时自动使用泊松分布。
    """
    snapshots = await _load_inventory_snapshot(db, merchant.id)
    if not snapshots:
        generated_at = format_utc_iso(utc_now())
        return {
            "code": 0,
            "data": {
                "generated_at": generated_at,
                "count": 0,
                "days": days,
                "suggestions": [],
            },
        }

    sku_map, category_map, sku_by_name = await _catalog_maps(db, merchant.id, snapshots)
    stats = await _load_sales_stats(db, merchant.id, days)
    items = _resolve_catalog(snapshots, sku_map, category_map, sku_by_name, require_stock=False)

    suggestions = []
    for item in items:
        sales = _sales_stats_for(stats, item["product_id"], item["sku_id"])
        if not sales or sales["mean_demand"] <= 0:
            continue

        mean_demand = sales["mean_demand"]
        std_demand = sales["std_demand"]
        advice = recommend_for_perishable(
            product_name=item["product_name"],
            selling_price=item["sale_price"],
            unit_cost=item["avg_cost"] or 0,
            salvage_value=0,
            mean_demand=mean_demand,
            std_demand=std_demand,
            use_poisson=mean_demand < 5,
        )
        suggestions.append(
            {
                "product_id": item["product_id"],
                "sku_id": str(item["sku_id"]) if item["sku_id"] else None,
                "product_name": item["product_name"],
                "unit": item["unit"],
                "selling_price": round(item["sale_price"], 2),
                "unit_cost": round(item["avg_cost"] or 0, 2),
                "mean_demand": round(mean_demand, 2),
                "std_demand": round(std_demand, 2),
                "optimal_quantity": round(float(advice["optimal_quantity"]), 2),
                "critical_ratio": round(float(advice["critical_ratio"]), 4),
                "underage_cost": round(float(advice["underage_cost"]), 2),
                "overage_cost": round(float(advice["overage_cost"]), 2),
                "expected_profit": round(float(advice["expected_profit"]), 2),
                "expected_leftover": round(float(advice["expected_leftover"]), 2),
                "expected_lost_sales": round(float(advice["expected_lost_sales"]), 2),
                "waste_rate_pct": round(float(advice["waste_rate_pct"]), 2),
                "suggestion": advice["suggestion"],
            }
        )

    suggestions.sort(
        key=lambda x: (
            x["optimal_quantity"] - x["mean_demand"],
            x["waste_rate_pct"],
        ),
        reverse=True,
    )
    limited = suggestions[:limit]
    return {
        "code": 0,
        "data": {
            "generated_at": format_utc_iso(utc_now()),
            "count": len(limited),
            "days": days,
            "suggestions": limited,
        },
    }
