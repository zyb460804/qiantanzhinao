"""Digital twin visualization API router — reads real DB data."""

import uuid
from collections.abc import Sequence
from datetime import date, timedelta

from fastapi import APIRouter, Depends
from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import get_merchant_id
from app.core.timezone import local_days_ago, local_today_start
from app.database import get_db
from app.models.catalog import ProductSKU
from app.models.environment import EnvironmentRecord
from app.models.inventory import InventoryRecord
from app.models.product import ProductCategory
from app.schemas.common import AnyResponse
from app.services.batch import count_expiring_batches, get_active_batches
from app.services.lifecycle import calc_batch_status


router = APIRouter(prefix="/api/v1/twin", tags=["twin"])


async def _estimate_cogs_twin(
    db: AsyncSession, merchant_id: uuid.UUID, records: Sequence, cutoff_days: int = 30
) -> float:
    """Estimate cost of goods sold, preferring actual FIFO costs from sale records.

    Sale records populated with unit_cost via FIFO batch consumption are used
    directly. Records without unit_cost fall back to the 30-day purchase average
    for the product.
    """
    cogs = 0.0
    unknown_products: dict[int, float] = {}

    for r in records:
        if r.event_type != "sale":
            continue
        if r.unit_cost is not None:
            cogs += abs(float(r.quantity)) * float(r.unit_cost)
        else:
            pid = r.product_id
            unknown_products[pid] = unknown_products.get(pid, 0) + abs(float(r.quantity))

    if unknown_products:
        cutoff = local_days_ago(cutoff_days)
        cost_query = (
            select(
                InventoryRecord.product_id,
                func.avg(InventoryRecord.unit_cost).label("avg_cost"),
            )
            .where(
                InventoryRecord.merchant_id == merchant_id,
                InventoryRecord.is_voided == False,  # noqa: E712
                InventoryRecord.event_type == "purchase",
                InventoryRecord.unit_cost.isnot(None),
                InventoryRecord.product_id.in_(set(unknown_products)),
                InventoryRecord.event_time >= cutoff,
            )
            .group_by(InventoryRecord.product_id)
        )
        cost_result = await db.execute(cost_query)
        avg_costs = {row.product_id: float(row.avg_cost) for row in cost_result}
        for pid, qty in unknown_products.items():
            avg_cost = avg_costs.get(pid, 0)
            cogs += qty * avg_cost

    return round(cogs, 2)


@router.get("/dashboard", response_model=AnyResponse)
async def get_dashboard(
    merchant_id: uuid.UUID = Depends(get_merchant_id),
    db: AsyncSession = Depends(get_db),
):
    """Get homepage dashboard: today's revenue/cost/profit + inventory summary.

    Profit = estimated gross profit (revenue - estimated COGS), not cash flow.
    """
    today_start = local_today_start()

    # Fetch today's non-voided records for COGS estimation
    today_query = select(InventoryRecord).where(
        InventoryRecord.merchant_id == merchant_id,
        InventoryRecord.is_voided == False,  # noqa: E712
        InventoryRecord.event_time >= today_start,
    )
    today_result = await db.execute(today_query)
    today_records = today_result.scalars().all()

    today_revenue = round(
        sum(float(r.total_amount or 0) for r in today_records if r.event_type == "sale"), 2
    )
    today_cost = round(
        sum(float(r.total_amount or 0) for r in today_records if r.event_type == "purchase"), 2
    )

    # Estimated COGS for accurate gross profit
    estimated_cogs = await _estimate_cogs_twin(db, merchant_id, today_records)
    estimated_gross_profit = round(today_revenue - estimated_cogs, 2)
    cash_balance = round(today_revenue - today_cost, 2)
    today_profit = estimated_gross_profit  # Use gross profit for display

    # Total inventory: sum of all non-voided quantity for this merchant
    inv_query = select(func.coalesce(func.sum(InventoryRecord.quantity), 0)).where(
        InventoryRecord.merchant_id == merchant_id,
        InventoryRecord.is_voided == False,  # noqa: E712
    )
    inv_result = await db.execute(inv_query)
    total_inventory_qty = round(float(inv_result.scalar() or 0), 1)

    # Risk score (0-100)
    risk_score = _calc_risk_score(total_inventory_qty, today_profit)

    expiring_count = await count_expiring_batches(db, merchant_id, within_hours=24)

    return {
        "code": 0,
        "data": {
            "today_revenue": today_revenue,
            "today_cost": today_cost,
            "today_profit": today_profit,
            "estimated_gross_profit": estimated_gross_profit,
            "estimated_cogs": estimated_cogs,
            "cash_balance": cash_balance,
            "total_inventory_qty": max(0, total_inventory_qty),
            "expiring_count": expiring_count,
            "risk_score": risk_score,
        },
    }


@router.get("/inventory-mirror", response_model=AnyResponse)
async def get_inventory_mirror(
    merchant_id: uuid.UUID = Depends(get_merchant_id),
    db: AsyncSession = Depends(get_db),
):
    """Inventory mirror: by-category bar chart + lifecycle heatmap data."""
    # Aggregate inventory by product, joined with category (exclude voided)
    inv_query = (
        select(
            ProductCategory.category_group,
            ProductCategory.name,
            ProductCategory.id,
            func.sum(InventoryRecord.quantity).label("qty"),
        )
        .join(ProductCategory, InventoryRecord.product_id == ProductCategory.id)
        .where(
            InventoryRecord.merchant_id == merchant_id,
            InventoryRecord.is_voided == False,  # noqa: E712
        )
        .group_by(ProductCategory.category_group, ProductCategory.name, ProductCategory.id)
    )
    inv_result = await db.execute(inv_query)
    rows = inv_result.all()

    # Load SKU name map for this merchant (category.name == sku.name in current design)
    sku_rows = (
        await db.execute(
            select(ProductSKU.id, ProductSKU.name).where(
                ProductSKU.merchant_id == merchant_id,
                ProductSKU.is_active == True,  # noqa: E712
            )
        )
    ).all()
    sku_by_name = {name: sid for sid, name in sku_rows}

    # Group by category
    by_category = {}
    for group, name, pid, qty in rows:
        qty_val = round(float(qty or 0), 1)
        if group not in by_category:
            by_category[group] = {"category": group, "total_qty": 0, "products": []}
        by_category[group]["total_qty"] += qty_val
        sid = sku_by_name.get(name)
        by_category[group]["products"].append(
            {
                "product_id": pid,
                "sku_id": str(sid) if sid else None,
                "sku_name": name if sid else None,
                "name": name,
                "qty": qty_val,
            }
        )

    # Lifecycle heatmap: batch status matrix by product/SKU × time-to-expiry
    batches = await get_active_batches(db, merchant_id)
    product_names = {}
    sku_map_by_id = {str(sid): name for sid, name in sku_rows}
    for _group_name, products_list in [(g, d["products"]) for g, d in by_category.items()]:
        for p in products_list:
            product_names[p["product_id"]] = p["name"]

    heatmap = []
    for batch in batches:
        name = product_names.get(batch.product_id, f"商品{batch.product_id}")
        status = calc_batch_status(
            product_name=name,
            purchase_date=batch.purchase_date,
            remaining_qty=float(batch.remaining_qty),
            purchase_qty=float(batch.purchase_qty),
        )
        hours_left = status.get("hours_remaining")
        # Bucket: today (<24h), 1day (24-48h), 2days (48-72h), 3days+ (>72h)
        if hours_left is not None:
            if hours_left <= 24:
                bucket = "today"
            elif hours_left <= 48:
                bucket = "1day"
            elif hours_left <= 72:
                bucket = "2days"
            else:
                bucket = "3days+"
        else:
            bucket = "unknown"

        heatmap.append(
            {
                "product_id": batch.product_id,
                "sku_id": str(batch.sku_id) if batch.sku_id else None,
                "sku_name": sku_map_by_id.get(str(batch.sku_id)) if batch.sku_id else None,
                "product_name": name,
                "batch_label": batch.batch_label,
                "remaining_qty": round(float(batch.remaining_qty), 1),
                "status": status.get("status", "fresh"),
                "color": status.get("color", "green"),
                "hours_remaining": hours_left,
                "time_bucket": bucket,
            }
        )

    return {
        "code": 0,
        "data": {
            "by_category": sorted(
                list(by_category.values()), key=lambda x: x["total_qty"], reverse=True
            ),
            "lifecycle_heatmap": heatmap,
        },
    }


@router.get("/business-mirror", response_model=AnyResponse)
async def get_business_mirror(
    merchant_id: uuid.UUID = Depends(get_merchant_id),
    db: AsyncSession = Depends(get_db),
):
    """Business mirror: 7-day and 30-day daily sales/profit trends + customer price."""
    # 7-day daily breakdown
    sales_7d = await _daily_breakdown(db, merchant_id, 7)
    # 30-day weekly-aggregated
    sales_30d = await _daily_breakdown(db, merchant_id, 30)

    # Calculate 7-day aggregates
    week_revenue = sum(d["revenue"] for d in sales_7d)
    week_sale_count = sum(d["sale_count"] for d in sales_7d)
    week_avg_customer_price = round(week_revenue / week_sale_count, 2) if week_sale_count > 0 else 0

    # Calculate 30-day aggregates
    month_revenue = sum(d["revenue"] for d in sales_30d)
    month_sale_count = sum(d["sale_count"] for d in sales_30d)
    month_avg_customer_price = (
        round(month_revenue / month_sale_count, 2) if month_sale_count > 0 else 0
    )

    return {
        "code": 0,
        "data": {
            "sales_7d": sales_7d,
            "sales_30d": sales_30d,
            "week_avg_customer_price": week_avg_customer_price,
            "month_avg_customer_price": month_avg_customer_price,
            "week_total_revenue": round(week_revenue, 2),
            "month_total_revenue": round(month_revenue, 2),
        },
    }


@router.get("/risk-mirror", response_model=AnyResponse)
async def get_risk_mirror(
    merchant_id: uuid.UUID = Depends(get_merchant_id),
    db: AsyncSession = Depends(get_db),
):
    """Risk radar chart data — all 6 dimensions calculated from real data."""
    today = date.today()

    # Environment risk from today's weather
    env_query = select(EnvironmentRecord).where(EnvironmentRecord.date == today)
    env_result = await db.execute(env_query)
    env_row = env_result.scalar_one_or_none()

    weather_risk = 0
    if env_row:
        rain = float(env_row.rainfall_prob or 0)
        temp = float(env_row.temp_high or 25)
        if rain > 50:
            weather_risk += min(80, int(rain))
        if temp > 35 or temp < 5:
            weather_risk += 30

    # Inventory risk: ratio of total inventory to 7-day avg sales
    seven_days_ago = local_days_ago(7)
    sales_query = select(func.sum(func.abs(InventoryRecord.quantity))).where(
        InventoryRecord.merchant_id == merchant_id,
        InventoryRecord.is_voided == False,  # noqa: E712
        InventoryRecord.event_type == "sale",
        InventoryRecord.event_time >= seven_days_ago,
    )
    sales_result = await db.execute(sales_query)
    total_sales_7d = float(sales_result.scalar() or 0)

    inv_query = select(func.sum(InventoryRecord.quantity)).where(
        InventoryRecord.merchant_id == merchant_id,
        InventoryRecord.is_voided == False,  # noqa: E712
    )
    inv_result = await db.execute(inv_query)
    current_inv = float(inv_result.scalar() or 0)

    if total_sales_7d > 0:
        days_of_stock = current_inv / (total_sales_7d / 7)
        inventory_risk = (
            min(90, max(0, int((1 - days_of_stock / 3) * 60))) if days_of_stock < 3 else 0
        )
    else:
        inventory_risk = 50

    # Waste risk: proportion of waste records in last 30 days
    thirty_days_ago = local_days_ago(30)
    waste_query = select(func.sum(func.abs(InventoryRecord.quantity))).where(
        InventoryRecord.merchant_id == merchant_id,
        InventoryRecord.is_voided == False,  # noqa: E712
        InventoryRecord.event_type == "waste",
        InventoryRecord.event_time >= thirty_days_ago,
    )
    waste_result = await db.execute(waste_query)
    total_waste = float(waste_result.scalar() or 0)

    purchased_query = select(func.sum(InventoryRecord.quantity)).where(
        InventoryRecord.merchant_id == merchant_id,
        InventoryRecord.is_voided == False,  # noqa: E712
        InventoryRecord.event_type == "purchase",
        InventoryRecord.event_time >= thirty_days_ago,
    )
    purchased_result = await db.execute(purchased_query)
    total_purchased = float(purchased_result.scalar() or 0)

    waste_risk = min(90, int(total_waste / total_purchased * 100)) if total_purchased > 0 else 0

    # Capital risk: based on cash flow imbalance (purchases >> sales = capital tied up)
    today_start = local_today_start()
    today_sales_amt = float(
        (
            await db.execute(
                select(func.coalesce(func.sum(InventoryRecord.total_amount), 0)).where(
                    InventoryRecord.merchant_id == merchant_id,
                    InventoryRecord.is_voided == False,  # noqa: E712
                    InventoryRecord.event_type == "sale",
                    InventoryRecord.event_time >= today_start,
                )
            )
        ).scalar()
        or 0
    )
    today_purchase_amt = float(
        (
            await db.execute(
                select(func.coalesce(func.sum(InventoryRecord.total_amount), 0)).where(
                    InventoryRecord.merchant_id == merchant_id,
                    InventoryRecord.is_voided == False,  # noqa: E712
                    InventoryRecord.event_type == "purchase",
                    InventoryRecord.event_time >= today_start,
                )
            )
        ).scalar()
        or 0
    )
    if today_purchase_amt > 0:
        # High capital risk when purchases far exceed sales (capital tied up in inventory)
        capital_ratio = today_purchase_amt / max(today_sales_amt, 1)
        capital_risk = (
            min(90, max(0, int((capital_ratio - 1) * 30)))
            if capital_ratio > 1
            else max(0, int((1 - capital_ratio) * 20))
        )
    else:
        capital_risk = 10  # Low risk if no purchases today

    # Category concentration risk: based on inventory distribution across categories
    cat_query = (
        select(
            ProductCategory.category_group,
            func.sum(InventoryRecord.quantity).label("qty"),
        )
        .join(ProductCategory, InventoryRecord.product_id == ProductCategory.id)
        .where(
            InventoryRecord.merchant_id == merchant_id,
            InventoryRecord.is_voided == False,  # noqa: E712
        )
        .group_by(ProductCategory.category_group)
    )
    cat_result = await db.execute(cat_query)
    cat_rows = cat_result.all()
    total_cat_qty = sum(abs(float(r.qty or 0)) for r in cat_rows)
    if total_cat_qty > 0 and len(cat_rows) > 0:
        # Herfindahl index: sum of squared market shares
        shares = [abs(float(r.qty or 0)) / total_cat_qty for r in cat_rows]
        hhi = sum(s * s for s in shares)
        # HHI ranges from 1/N (diversified) to 1 (concentrated)
        # Map to 0-90 risk scale
        category_concentration_risk = min(90, int(hhi * 70))
    else:
        category_concentration_risk = 0

    # Customer flow risk: based on number of distinct sale transactions
    sale_count_query = select(func.count(InventoryRecord.id)).where(
        InventoryRecord.merchant_id == merchant_id,
        InventoryRecord.is_voided == False,  # noqa: E712
        InventoryRecord.event_type == "sale",
        InventoryRecord.event_time >= seven_days_ago,
    )
    sale_count_result = await db.execute(sale_count_query)
    sale_count_7d = int(sale_count_result.scalar() or 0)
    # Low transaction count = high customer flow risk
    if sale_count_7d < 7:
        customer_flow_risk = min(80, max(20, 60 - sale_count_7d * 5))
    elif sale_count_7d < 21:
        customer_flow_risk = 30
    else:
        customer_flow_risk = 10

    return {
        "code": 0,
        "data": {
            "inventory_risk": round(inventory_risk, 1),
            "weather_risk": round(weather_risk, 1),
            "waste_risk": round(waste_risk, 1),
            "capital_risk": round(capital_risk, 1),
            "category_concentration_risk": round(category_concentration_risk, 1),
            "customer_flow_risk": round(customer_flow_risk, 1),
        },
    }


async def _daily_breakdown(db: AsyncSession, merchant_id: uuid.UUID, days: int) -> list[dict]:
    """Return daily revenue, cost, profit, volume and customer metrics."""
    start = local_today_start() - timedelta(days=days)

    q = (
        select(
            func.date(InventoryRecord.event_time).label("d"),
            func.sum(
                case(
                    (InventoryRecord.event_type == "sale", func.abs(InventoryRecord.total_amount)),
                    else_=0,
                )
            ).label("revenue"),
            func.sum(
                case(
                    (
                        InventoryRecord.event_type == "purchase",
                        func.abs(InventoryRecord.total_amount),
                    ),
                    else_=0,
                )
            ).label("cost"),
            func.sum(func.abs(InventoryRecord.quantity)).label("volume"),
            func.sum(
                case(
                    (InventoryRecord.event_type == "sale", 1),
                    else_=0,
                )
            ).label("sale_count"),
        )
        .where(
            InventoryRecord.merchant_id == merchant_id,
            InventoryRecord.is_voided == False,  # noqa: E712
            InventoryRecord.event_time >= start,
        )
        .group_by(func.date(InventoryRecord.event_time))
        .order_by(func.date(InventoryRecord.event_time))
    )
    result = await db.execute(q)
    rows = result.all()

    daily_data = []
    for row in rows:
        revenue = float(row.revenue or 0)
        cost = float(row.cost or 0)
        sale_count = int(row.sale_count or 0)
        daily_data.append(
            {
                "date": str(row.d),
                "revenue": round(revenue, 2),
                "cost": round(cost, 2),
                "profit": round(revenue - cost, 2),
                "volume": round(float(row.volume or 0), 1),
                "sale_count": sale_count,
                "customer_price": round(revenue / sale_count, 2) if sale_count > 0 else 0,
            }
        )

    # Fill missing dates with zeros for continuous timeline
    filled = []
    for i in range(days):
        d = (local_today_start() - timedelta(days=days - 1 - i)).date()
        d_str = str(d)
        existing = next((x for x in daily_data if x["date"] == d_str), None)
        if existing:
            filled.append(existing)
        else:
            filled.append(
                {
                    "date": d_str,
                    "revenue": 0,
                    "cost": 0,
                    "profit": 0,
                    "volume": 0,
                    "sale_count": 0,
                    "customer_price": 0,
                }
            )

    return filled


def _calc_risk_score(inventory_qty: float, today_profit: float) -> int:
    """Simple composite risk score 0-100."""
    score = 20  # Base
    if inventory_qty < 0:
        score += 30  # Negative inventory = data quality issue
    if today_profit < 0:
        score += 25  # Loss today
    elif today_profit < 50:
        score += 10
    return min(100, score)
