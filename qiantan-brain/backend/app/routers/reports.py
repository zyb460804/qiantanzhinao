"""Business reports API — daily/weekly reports, trends, rankings.

Consolidates revenue, cost, profit, waste, and AI insights into
merchant-facing reports with clear calculation logic.
"""

import uuid
from collections.abc import Sequence
from datetime import date, timedelta
from typing import Literal, TypedDict

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import get_merchant_id
from app.core.timezone import (
    cst_day_bounds_utc,
    cst_days_ago_bounds_utc,
    cst_today,
)
from app.database import get_db
from app.models.batch import BatchLifecycle
from app.models.catalog import ProductSKU
from app.models.inventory import InventoryRecord
from app.models.product import ProductCategory
from app.models.recommendation import Recommendation
from app.models.voice import VoiceLog
from app.routers.staff import require_permission
from app.schemas.common import AnyResponse


router = APIRouter(prefix="/api/v1/reports", tags=["reports"])


class SalesRankingRow(TypedDict):
    product_id: int
    product_name: str
    qty: float
    revenue: float


class WasteRankingRow(TypedDict):
    product_id: int
    product_name: str
    qty: float
    amount: float


class ProductRankingRow(TypedDict):
    product_id: int
    product_name: str
    sale_qty: float
    sale_revenue: float
    waste_qty: float
    waste_amount: float


def _date_range(days: int):
    """Return (start, end) for the last N CST business days, as naive UTC.

    event_time 在 DB 中为 naive UTC，日界按 CST 业务日切（审计 C4），
    否则 CST 凌晨 0-8 点的销售会被归入前一天。
    """
    return cst_days_ago_bounds_utc(days)


async def _estimate_cogs(
    db: AsyncSession,
    merchant_id: uuid.UUID,
    records: Sequence,
    cutoff_days: int = 30,
) -> float:
    """Estimate cost of goods sold, preferring actual FIFO costs from sale records.

    Sale records populated with unit_cost via FIFO batch consumption are used
    directly. Records without unit_cost fall back to the 30-day purchase average
    for the product.

    QA-01：退款回库（event_type='refund' 且 quantity>0）按 qty×unit_cost 从
    成本中冲减，与日结 _estimate_daily_cogs 口径对齐——此前只算 sale，
    回库退款不冲回成本，同日报表/看板与日结的「已售成本」打架；
    quantity=0 的不回库退款无成本影响。unit_cost 缺失的回库退款在兜底
    路径按净量冲减（与日结同口径）。
    """
    cogs = 0.0
    unknown_products: dict[int, float] = {}

    for r in records:
        if r.event_type == "sale":
            if r.unit_cost is not None:
                cogs += abs(float(r.quantity)) * float(r.unit_cost)
            else:
                pid = r.product_id
                unknown_products[pid] = unknown_products.get(pid, 0) + abs(float(r.quantity))
        elif r.event_type == "refund":
            # QA-01：回库退款冲减成本（quantity=0 的不回库退款无影响）
            refund_qty = float(r.quantity or 0)
            if refund_qty > 0:
                if r.unit_cost is not None:
                    cogs -= refund_qty * float(r.unit_cost)
                else:
                    pid = r.product_id
                    unknown_products[pid] = unknown_products.get(pid, 0) - refund_qty

    if unknown_products:
        cutoff = cst_days_ago_bounds_utc(cutoff_days)[0]
        # QA-15：兜底成本口径改为加权平均 sum(qty×unit_cost)/sum(qty)（按采购
        # 量加权）——原简单平均 func.avg(unit_cost) 在多批次不同进价时系统性
        # 偏离实际成本（实测偏差可达 25%）。
        cost_query = (
            select(
                InventoryRecord.product_id,
                func.coalesce(
                    func.sum(InventoryRecord.unit_cost * InventoryRecord.quantity), 0
                ).label("cost_sum"),
                func.coalesce(func.sum(InventoryRecord.quantity), 0).label("qty_sum"),
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
        avg_costs: dict[int, float] = {}
        for row in cost_result:
            qty_sum = float(row.qty_sum or 0)
            if qty_sum > 0:
                avg_costs[row.product_id] = float(row.cost_sum or 0) / qty_sum
        for pid, qty in unknown_products.items():
            avg_cost = avg_costs.get(pid, 0)
            cogs += qty * avg_cost

    return round(cogs, 2)


def _product_display_name(
    product_id: int,
    sku_id: uuid.UUID | None,
    sku_names: dict[uuid.UUID, str],
    product_names: dict[int, str],
) -> str:
    """商品展示名，与 /inventory/current 命名口径统一（QA 对齐项）。

    同一字段来源：优先 ProductSKU.name（inventory/current 的 sku_name），
    其次 ProductCategory.name（其 product_name），兜底同样为「商品{id}」。
    修复：slow_moving 此前只查品类表，SKU 商品落兜底名「商品13」，
    与库存页同一商品显示的 SKU 标准名不一致。
    """
    if sku_id is not None:
        sku_name = sku_names.get(sku_id)
        if sku_name:
            return sku_name
    return product_names.get(product_id, f"商品{product_id}")


@router.get("/daily", response_model=AnyResponse)
async def daily_report(
    date: date | None = None,
    merchant_id: uuid.UUID = Depends(get_merchant_id),
    # QA-03：利润族报表挂 view_profit 权限（此前 cashier 等员工也可查看老板利润）
    _perm=Depends(require_permission("view_profit")),
    db: AsyncSession = Depends(get_db),
):
    """Daily business report — revenue, cost, profit, top products, AI summary.

    date 不传（None）= CST 业务「今天」；传历史日期返回该业务日快照
    （语音数/建议采纳/临期参考点同样随目标日平移到该日的 CST 日界内）。
    非法日期由 FastAPI 参数解析自动 422。
    """
    target_day = date or cst_today()
    day_label = "今日" if target_day == cst_today() else f"{target_day.month}月{target_day.day}日"
    today_start, today_end = cst_day_bounds_utc(target_day)
    yesterday_start = cst_day_bounds_utc(target_day - timedelta(days=1))[0]

    # --- Target day's records (event_time is naive UTC; day boundary is CST) ---
    today_query = select(InventoryRecord).where(
        InventoryRecord.merchant_id == merchant_id,
        InventoryRecord.is_voided == False,  # noqa: E712
        InventoryRecord.event_time >= today_start,
        InventoryRecord.event_time < today_end,
    )
    today_result = await db.execute(today_query)
    today_records = today_result.scalars().all()

    revenue = sum(float(r.total_amount or 0) for r in today_records if r.event_type == "sale")
    # P2-5 口径统一：退款（event_type='refund'）从营收中扣除 —— 与 POS 日结
    # total_sales（订单 total - refunded）一致；此前日报不扣退款，两页数字打架。
    revenue -= sum(float(r.total_amount or 0) for r in today_records if r.event_type == "refund")
    cost = sum(float(r.total_amount or 0) for r in today_records if r.event_type == "purchase")
    estimated_cogs = await _estimate_cogs(db, merchant_id, today_records)
    estimated_gross_profit = revenue - estimated_cogs
    cash_balance = revenue - cost  # 现金结余 = 收款 - 采购付款
    profit = cash_balance  # 向后兼容
    sale_qty = sum(abs(float(r.quantity)) for r in today_records if r.event_type == "sale")
    waste_amount = sum(
        abs(float(r.total_amount or 0)) if r.total_amount else 0
        for r in today_records
        if r.event_type == "waste"
    )

    # --- Yesterday for comparison ---
    yesterday_query = select(InventoryRecord).where(
        InventoryRecord.merchant_id == merchant_id,
        InventoryRecord.is_voided == False,  # noqa: E712
        InventoryRecord.event_time >= yesterday_start,
        InventoryRecord.event_time < today_start,
    )
    yesterday_result = await db.execute(yesterday_query)
    yesterday_records = yesterday_result.scalars().all()
    yesterday_revenue = sum(
        float(r.total_amount or 0) for r in yesterday_records if r.event_type == "sale"
    )

    revenue_change = None
    if yesterday_revenue > 0:
        revenue_change = round((revenue - yesterday_revenue) / yesterday_revenue * 100, 1)

    # --- Voice count for the target CST business day (created_at is naive UTC) ---
    # QA-32：口径与 /voice/today-count 对齐，只计已入账（confirmed）——此前
    # pending/parsed 草稿与 voided 也计数，「今日已记 N 笔」与日报数字打架。
    voice_query = select(func.count(VoiceLog.id)).where(
        VoiceLog.merchant_id == merchant_id,
        VoiceLog.created_at >= today_start,
        VoiceLog.created_at < today_end,
        VoiceLog.status == "confirmed",
    )
    voice_result = await db.execute(voice_query)
    voice_count = int(voice_result.scalar() or 0)

    # --- Expiring count（参考点 = 目标业务日结束后 24h，随 date 参数平移；
    #     不传 date 时等价于原 utc_now()+24h 的日界化版本）---
    expiring_query = select(func.count(BatchLifecycle.id)).where(
        BatchLifecycle.merchant_id == merchant_id,
        BatchLifecycle.remaining_qty > 0,
        BatchLifecycle.status != "spoiled",
        BatchLifecycle.expiry_date.isnot(None),
        BatchLifecycle.expiry_date <= today_end + timedelta(hours=24),
    )
    expiring_result = await db.execute(expiring_query)
    expiring_count = int(expiring_result.scalar() or 0)

    # --- Top 3 products by sales（净额口径，QA-14）---
    product_sales: dict[int, dict[str, float]] = {}
    product_ids: set[int] = set()
    for r in today_records:
        if r.event_type == "sale":
            pid = r.product_id
            product_ids.add(pid)
            if pid not in product_sales:
                product_sales[pid] = {"qty": 0.0, "revenue": 0.0}
            product_sales[pid]["qty"] += abs(float(r.quantity))
            product_sales[pid]["revenue"] += float(r.total_amount or 0) if r.total_amount else 0
        elif r.event_type == "refund":
            # QA-14：退款从销量/营收中冲减——同响应 revenue 已是净额（销售-退款），
            # 此前 top_products 按原始流水统计，退款订单可永久污染排名。
            # qty 只冲减回库退款（quantity>0）；不回库退款 quantity=0，仅冲营收。
            pid = r.product_id
            product_ids.add(pid)
            if pid not in product_sales:
                product_sales[pid] = {"qty": 0.0, "revenue": 0.0}
            product_sales[pid]["qty"] -= float(r.quantity or 0)
            product_sales[pid]["revenue"] -= float(r.total_amount or 0) if r.total_amount else 0

    product_names = {}
    if product_ids:
        name_query = select(ProductCategory).where(ProductCategory.id.in_(product_ids))
        name_result = await db.execute(name_query)
        for p in name_result.scalars().all():
            product_names[p.id] = p.name

    top_product_rows: list[SalesRankingRow] = [
        {
            "product_id": pid,
            "product_name": product_names.get(pid, f"商品{pid}"),
            "qty": round(data["qty"], 1),
            "revenue": round(data["revenue"], 2),
        }
        for pid, data in product_sales.items()
    ]
    top_products = sorted(
        top_product_rows,
        key=lambda row: row["revenue"],
        reverse=True,
    )[:3]

    # --- Slow-moving products (in stock but no sales today) ---
    # 命名口径与 /inventory/current 统一：SKU 标准名优先（sku_id → ProductSKU.name），
    # 退化品类名，兜底「商品{id}」——见 _product_display_name。
    slow_moving = []
    stock_map: dict[int, float] = {}
    stock_sku: dict[int, uuid.UUID | None] = {}
    sku_names: dict[uuid.UUID, str] = {}
    record_sku_ids = {r.sku_id for r in today_records if r.sku_id is not None}
    if record_sku_ids:
        sku_name_result = await db.execute(
            select(ProductSKU).where(ProductSKU.id.in_(record_sku_ids))
        )
        sku_names = {s.id: s.name for s in sku_name_result.scalars().all()}
    for r in today_records:
        pid = r.product_id
        stock_map[pid] = stock_map.get(pid, 0) + float(r.quantity)
        # 同一商品多行流水时保留任一非空 sku_id（当前 category:sku 一对一）
        if r.sku_id is not None or pid not in stock_sku:
            stock_sku[pid] = r.sku_id
    for pid, qty in stock_map.items():
        if qty > 0 and pid not in product_sales:
            slow_moving.append(
                {
                    "product_id": pid,
                    "sku_id": str(stock_sku.get(pid)) if stock_sku.get(pid) else None,
                    "product_name": _product_display_name(
                        pid, stock_sku.get(pid), sku_names, product_names
                    ),
                    "stock_qty": round(qty, 1),
                }
            )

    # --- Recommendation adoption (target CST business day) ---
    rec_query = select(Recommendation).where(
        Recommendation.merchant_id == merchant_id,
        Recommendation.created_at >= today_start,
        Recommendation.created_at < today_end,
    )
    rec_result = await db.execute(rec_query)
    recs = rec_result.scalars().all()
    total_recs = len(recs)
    adopted_recs = sum(1 for r in recs if bool(r.was_adopted))

    # --- AI summary ---
    summary_parts = []
    if revenue > 0:
        summary_parts.append(f"{day_label}营业额{round(revenue, 1)}元")
    if estimated_gross_profit > 0:
        summary_parts.append(f"估算毛利{round(estimated_gross_profit, 1)}元")
    elif cash_balance > 0:
        summary_parts.append(f"现金结余{round(cash_balance, 1)}元")
    if waste_amount > 0:
        summary_parts.append(f"损耗{round(waste_amount, 1)}元")
    if revenue_change is not None:
        if revenue_change > 0:
            summary_parts.append(f"较昨日增长{revenue_change}%")
        elif revenue_change < 0:
            summary_parts.append(f"较昨日下降{abs(revenue_change)}%")

    ai_summary = "，".join(summary_parts) + "。" if summary_parts else f"{day_label}暂无经营数据。"

    # --- Action items for tomorrow ---
    action_items = []
    if expiring_count > 0:
        action_items.append(f"{expiring_count}个商品即将临期，建议尽快处理")
    for item in slow_moving[:2]:
        action_items.append(f"{item['product_name']}库存{item['stock_qty']}斤未售出，建议促销")
    if waste_amount > revenue * 0.1 and revenue > 0:
        action_items.append(
            f"{day_label}损耗率较高({round(waste_amount / revenue * 100, 1)}%)，建议减少进货量"
        )

    return {
        "code": 0,
        "data": {
            "date": target_day.isoformat(),
            "revenue": round(revenue, 2),
            "cost": round(cost, 2),
            "profit": round(profit, 2),
            "estimated_gross_profit": round(estimated_gross_profit, 2),
            "cash_balance": round(cash_balance, 2),
            "purchase_cost": round(cost, 2),
            "estimated_cogs": round(estimated_cogs, 2),
            "sale_qty": round(sale_qty, 1),
            "waste_amount": round(waste_amount, 2),
            "voice_count": voice_count,
            "expiring_count": expiring_count,
            "revenue_change_pct": revenue_change,
            "yesterday_revenue": round(yesterday_revenue, 2),
            "top_products": top_products,
            "slow_moving": slow_moving[:5],
            "recommendation_total": total_recs,
            "recommendation_adopted": adopted_recs,
            "ai_summary": ai_summary,
            "action_items": action_items,
        },
    }


@router.get("/weekly", response_model=AnyResponse)
async def weekly_report(
    end_date: date | None = None,
    merchant_id: uuid.UUID = Depends(get_merchant_id),
    # QA-03：同 /daily，挂 view_profit 权限
    _perm=Depends(require_permission("view_profit")),
    db: AsyncSession = Depends(get_db),
):
    """Weekly report — 7-day trends, rankings, weather impact, health score.

    end_date 不传 = 以 CST 今天为窗口最后一天；传历史日期则统计
    [end_date-6, end_date] 共 7 个完整 CST 业务日，对比期为再往前 7 天。
    """
    # 未来日期统计的是不存在的账期，此前静默返回空数据
    if end_date and end_date > cst_today():
        raise HTTPException(status_code=422, detail="end_date 不能晚于今天")
    anchor = end_date or cst_today()
    start_7d = cst_day_bounds_utc(anchor - timedelta(days=6))[0]
    # 窗口上界（含 anchor 全天）。默认 anchor=今天时与旧「无上界」等价；
    # 历史 anchor 若不封上界，窗口之后的新记录会被错误计入。
    end_7d = cst_day_bounds_utc(anchor)[1]
    start_14d = cst_day_bounds_utc(anchor - timedelta(days=13))[0]

    # This week's records
    week_query = select(InventoryRecord).where(
        InventoryRecord.merchant_id == merchant_id,
        InventoryRecord.is_voided == False,  # noqa: E712
        InventoryRecord.event_time >= start_7d,
        InventoryRecord.event_time < end_7d,
    )
    week_result = await db.execute(week_query)
    week_records = week_result.scalars().all()

    # Last week's records for comparison
    last_week_query = select(InventoryRecord).where(
        InventoryRecord.merchant_id == merchant_id,
        InventoryRecord.is_voided == False,  # noqa: E712
        InventoryRecord.event_time >= start_14d,
        InventoryRecord.event_time < start_7d,
    )
    last_week_result = await db.execute(last_week_query)
    last_week_records = last_week_result.scalars().all()

    week_revenue = sum(float(r.total_amount or 0) for r in week_records if r.event_type == "sale")
    last_week_revenue = sum(
        float(r.total_amount or 0) for r in last_week_records if r.event_type == "sale"
    )
    week_purchase_cost = sum(
        float(r.total_amount or 0) for r in week_records if r.event_type == "purchase"
    )
    week_estimated_cogs = await _estimate_cogs(db, merchant_id, week_records)
    week_gross_profit = week_revenue - week_estimated_cogs
    week_profit = week_revenue - week_purchase_cost  # 现金结余，向后兼容

    revenue_change = None
    if last_week_revenue > 0:
        revenue_change = round((week_revenue - last_week_revenue) / last_week_revenue * 100, 1)

    # Daily trends (per CST business day, anchored on end_date)
    daily_trends = []
    for i in range(7):
        d = anchor - timedelta(days=6 - i)
        day_start, day_end = cst_day_bounds_utc(d)
        day_sale_records = [
            r
            for r in week_records
            if r.event_type == "sale" and day_start <= r.event_time < day_end
        ]
        day_revenue = sum(float(r.total_amount or 0) for r in day_sale_records)
        day_cost = sum(
            float(r.total_amount or 0)
            for r in week_records
            if r.event_type == "purchase" and day_start <= r.event_time < day_end
        )
        # 客单价 = 当日营业额 / 当日销售笔数(与 /reports/trends 口径保持一致)
        day_sale_count = len(day_sale_records)
        day_customer_price = round(day_revenue / day_sale_count, 2) if day_sale_count > 0 else 0
        daily_trends.append(
            {
                "date": d.isoformat(),
                "revenue": round(day_revenue, 2),
                "cost": round(day_cost, 2),
                "profit": round(day_revenue - day_cost, 2),
                "customer_price": day_customer_price,
            }
        )

    # Product ranking
    product_sales: dict[int, dict[str, float]] = {}
    product_waste: dict[int, dict[str, float]] = {}
    product_ids: set[int] = set()
    for r in week_records:
        pid = r.product_id
        product_ids.add(pid)
        if r.event_type == "sale":
            if pid not in product_sales:
                product_sales[pid] = {"qty": 0.0, "revenue": 0.0}
            product_sales[pid]["qty"] += abs(float(r.quantity))
            product_sales[pid]["revenue"] += float(r.total_amount or 0) if r.total_amount else 0
        elif r.event_type == "waste":
            if pid not in product_waste:
                product_waste[pid] = {"qty": 0.0, "amount": 0.0}
            product_waste[pid]["qty"] += abs(float(r.quantity))
            product_waste[pid]["amount"] += abs(float(r.total_amount or 0)) if r.total_amount else 0

    product_names = {}
    if product_ids:
        name_query = select(ProductCategory).where(ProductCategory.id.in_(product_ids))
        name_result = await db.execute(name_query)
        for p in name_result.scalars().all():
            product_names[p.id] = p.name

    sales_rows: list[SalesRankingRow] = [
        {
            "product_id": pid,
            "product_name": product_names.get(pid, f"商品{pid}"),
            "qty": round(data["qty"], 1),
            "revenue": round(data["revenue"], 2),
        }
        for pid, data in product_sales.items()
    ]
    sales_ranking = sorted(sales_rows, key=lambda row: row["revenue"], reverse=True)[:10]

    waste_rows: list[WasteRankingRow] = [
        {
            "product_id": pid,
            "product_name": product_names.get(pid, f"商品{pid}"),
            "qty": round(data["qty"], 1),
            "amount": round(data["amount"], 2),
        }
        for pid, data in product_waste.items()
    ]
    waste_ranking = sorted(waste_rows, key=lambda row: row["amount"], reverse=True)[:10]

    # Recommendation adoption rate (within the anchored 7-day window)
    rec_query = select(Recommendation).where(
        Recommendation.merchant_id == merchant_id,
        Recommendation.created_at >= start_7d,
        Recommendation.created_at < end_7d,
    )
    rec_result = await db.execute(rec_query)
    recs = rec_result.scalars().all()
    total_recs = len(recs)
    adopted_recs = sum(1 for r in recs if bool(r.was_adopted))
    adoption_rate = round(adopted_recs / total_recs * 100, 1) if total_recs > 0 else 0

    # Health score (0-100)
    health = 50.0
    if week_revenue > 0:
        profit_rate = week_gross_profit / week_revenue
        health += min(20, profit_rate * 40)
    week_waste = sum(d["amount"] for d in waste_ranking)
    if week_revenue > 0 and week_waste < week_revenue * 0.1:
        health += 15
    if adoption_rate > 50:
        health += 15
    health = max(0, min(100, round(health)))

    # Weekly summary
    period_label = "本周" if anchor == cst_today() else "近7日"
    summary = f"{period_label}营业额{round(week_revenue, 1)}元"
    if revenue_change is not None:
        if revenue_change > 0:
            summary += f"，较上周增长{revenue_change}%"
        elif revenue_change < 0:
            summary += f"，较上周下降{abs(revenue_change)}%"
    if sales_ranking:
        summary += f"。销量最高的是{sales_ranking[0]['product_name']}"
    if waste_ranking and week_revenue > 0:
        top_waste = waste_ranking[0]
        summary += f"，{top_waste['product_name']}损耗最高({top_waste['amount']}元)"
    summary += "。"

    return {
        "code": 0,
        "data": {
            "period": "7d",
            "week_revenue": round(week_revenue, 2),
            "week_profit": round(week_profit, 2),
            "week_gross_profit": round(week_gross_profit, 2),
            "week_purchase_cost": round(week_purchase_cost, 2),
            "week_estimated_cogs": round(week_estimated_cogs, 2),
            "last_week_revenue": round(last_week_revenue, 2),
            "revenue_change_pct": revenue_change,
            "daily_trends": daily_trends,
            "sales_ranking": sales_ranking,
            "waste_ranking": waste_ranking,
            "adoption_rate": adoption_rate,
            "recommendation_total": total_recs,
            "recommendation_adopted": adopted_recs,
            "health_score": health,
            "ai_summary": summary,
        },
    }


@router.get("/trends", response_model=AnyResponse)
async def trends_report(
    merchant_id: uuid.UUID = Depends(get_merchant_id),
    days: int = Query(default=7, ge=1, le=365),
    # QA-03：同 /daily，挂 view_profit 权限
    _perm=Depends(require_permission("view_profit")),
    db: AsyncSession = Depends(get_db),
):
    """Revenue and profit trends over N days."""
    start, _end = _date_range(days)

    query = select(InventoryRecord).where(
        InventoryRecord.merchant_id == merchant_id,
        InventoryRecord.is_voided == False,  # noqa: E712
        InventoryRecord.event_time >= start,
    )
    result = await db.execute(query)
    records = result.scalars().all()

    # Pre-compute average purchase cost per product for COGS estimation
    sale_products = {r.product_id for r in records if r.event_type == "sale"}
    avg_costs = {}
    if sale_products:
        cutoff = cst_days_ago_bounds_utc(30)[0]
        # QA-15：与 _estimate_cogs 同步改加权平均 sum(qty×uc)/sum(qty)（按采购量加权）
        cost_query = (
            select(
                InventoryRecord.product_id,
                func.coalesce(
                    func.sum(InventoryRecord.unit_cost * InventoryRecord.quantity), 0
                ).label("cost_sum"),
                func.coalesce(func.sum(InventoryRecord.quantity), 0).label("qty_sum"),
            )
            .where(
                InventoryRecord.merchant_id == merchant_id,
                InventoryRecord.is_voided == False,  # noqa: E712
                InventoryRecord.event_type == "purchase",
                InventoryRecord.unit_cost.isnot(None),
                InventoryRecord.event_time >= cutoff,
            )
            .group_by(InventoryRecord.product_id)
        )
        cost_result = await db.execute(cost_query)
        for row in cost_result:
            qty_sum = float(row.qty_sum or 0)
            if qty_sum > 0:
                avg_costs[row.product_id] = float(row.cost_sum or 0) / qty_sum

    trends = []
    today = cst_today()
    for i in range(days):
        d = today - timedelta(days=days - 1 - i)
        day_start, day_end = cst_day_bounds_utc(d)
        day_records = [r for r in records if day_start <= r.event_time < day_end]
        revenue = sum(float(r.total_amount or 0) for r in day_records if r.event_type == "sale")
        cost = sum(float(r.total_amount or 0) for r in day_records if r.event_type == "purchase")
        sale_count = sum(1 for r in day_records if r.event_type == "sale")
        customer_price = round(revenue / sale_count, 2) if sale_count > 0 else 0
        day_cogs = sum(
            abs(float(r.quantity)) * avg_costs.get(r.product_id, 0)
            for r in day_records
            if r.event_type == "sale"
        )
        trends.append(
            {
                "date": d.isoformat(),
                "revenue": round(revenue, 2),
                "cost": round(cost, 2),
                "profit": round(revenue - cost, 2),
                "estimated_gross_profit": round(revenue - day_cogs, 2),
                "sale_count": sale_count,
                "customer_price": customer_price,
            }
        )

    return {"code": 0, "data": trends}


@router.get("/product-ranking", response_model=AnyResponse)
async def product_ranking(
    merchant_id: uuid.UUID = Depends(get_merchant_id),
    days: int = Query(default=7, ge=1, le=365),
    # limit/metric 白名单校验：此前非法值（limit=-5、乱传 metric）静默回退 200
    limit: int = Query(default=20, ge=1, le=100),
    metric: str = Query(default="revenue", pattern="^(revenue|sales|waste)$"),
    db: AsyncSession = Depends(get_db),
):
    """Product ranking by revenue, sales volume, or waste."""
    start, _end = _date_range(days)

    query = select(InventoryRecord).where(
        InventoryRecord.merchant_id == merchant_id,
        InventoryRecord.is_voided == False,  # noqa: E712
        InventoryRecord.event_time >= start,
    )
    result = await db.execute(query)
    records = result.scalars().all()

    product_data: dict[int, dict[str, float]] = {}
    product_ids: set[int] = set()
    for r in records:
        pid = r.product_id
        product_ids.add(pid)
        if pid not in product_data:
            product_data[pid] = {
                "sale_qty": 0.0,
                "sale_revenue": 0.0,
                "waste_qty": 0.0,
                "waste_amount": 0.0,
            }
        if r.event_type == "sale":
            product_data[pid]["sale_qty"] += abs(float(r.quantity))
            product_data[pid]["sale_revenue"] += float(r.total_amount or 0) if r.total_amount else 0
        elif r.event_type == "waste":
            product_data[pid]["waste_qty"] += abs(float(r.quantity))
            product_data[pid]["waste_amount"] += (
                abs(float(r.total_amount or 0)) if r.total_amount else 0
            )

    product_names = {}
    if product_ids:
        name_query = select(ProductCategory).where(ProductCategory.id.in_(product_ids))
        name_result = await db.execute(name_query)
        for p in name_result.scalars().all():
            product_names[p.id] = p.name

    sort_key: Literal["sale_revenue", "waste_amount", "sale_qty"] = (
        "sale_revenue"
        if metric == "revenue"
        else "waste_amount"
        if metric == "waste"
        else "sale_qty"
    )
    ranking_rows: list[ProductRankingRow] = [
        {
            "product_id": pid,
            "product_name": product_names.get(pid, f"商品{pid}"),
            "sale_qty": round(data["sale_qty"], 1),
            "sale_revenue": round(data["sale_revenue"], 2),
            "waste_qty": round(data["waste_qty"], 1),
            "waste_amount": round(data["waste_amount"], 2),
        }
        for pid, data in product_data.items()
    ]
    ranking = sorted(
        ranking_rows,
        key=lambda row: row[sort_key],
        reverse=True,
    )[:limit]

    return {"code": 0, "data": ranking}


@router.get("/monthly", response_model=AnyResponse)
async def monthly_report(
    end_date: date | None = None,
    merchant_id: uuid.UUID = Depends(get_merchant_id),
    # QA-03：同 /daily，挂 view_profit 权限
    _perm=Depends(require_permission("view_profit")),
    db: AsyncSession = Depends(get_db),
):
    """Monthly report — 30-day trends, rankings, waste, health score, AI summary.

    end_date 不传 = 以 CST 今天为 30 天滚动窗口最后一天；传历史日期则统计
    [end_date-29, end_date] 共 30 个完整 CST 业务日，对比期为再往前 30 天。

    Unlike the frontend client-side aggregation fallback, this endpoint
    computes sales ranking, waste ranking, health score, and a data-driven
    AI summary server-side, giving the monthly tab the same depth as weekly.
    """
    # 与 weekly 同口径：未来日期 422，不静默返回空数据
    if end_date and end_date > cst_today():
        raise HTTPException(status_code=422, detail="end_date 不能晚于今天")
    anchor = end_date or cst_today()
    days = 30
    start = cst_day_bounds_utc(anchor - timedelta(days=29))[0]
    # 窗口上界（含 anchor 全天）：历史 anchor 不封上界会错计窗口后的新记录。
    end_30d = cst_day_bounds_utc(anchor)[1]
    start_60d = cst_day_bounds_utc(anchor - timedelta(days=59))[0]

    # This month's records
    month_query = select(InventoryRecord).where(
        InventoryRecord.merchant_id == merchant_id,
        InventoryRecord.is_voided == False,  # noqa: E712
        InventoryRecord.event_time >= start,
        InventoryRecord.event_time < end_30d,
    )
    month_result = await db.execute(month_query)
    month_records = month_result.scalars().all()

    # Previous 30 days for comparison
    prev_query = select(InventoryRecord).where(
        InventoryRecord.merchant_id == merchant_id,
        InventoryRecord.is_voided == False,  # noqa: E712
        InventoryRecord.event_time >= start_60d,
        InventoryRecord.event_time < start,
    )
    prev_result = await db.execute(prev_query)
    prev_records = prev_result.scalars().all()

    month_revenue = sum(float(r.total_amount or 0) for r in month_records if r.event_type == "sale")
    prev_revenue = sum(float(r.total_amount or 0) for r in prev_records if r.event_type == "sale")
    month_purchase_cost = sum(
        float(r.total_amount or 0) for r in month_records if r.event_type == "purchase"
    )
    month_estimated_cogs = await _estimate_cogs(db, merchant_id, month_records, cutoff_days=60)
    month_gross_profit = month_revenue - month_estimated_cogs
    month_profit = month_revenue - month_purchase_cost

    revenue_change = None
    if prev_revenue > 0:
        revenue_change = round((month_revenue - prev_revenue) / prev_revenue * 100, 1)

    # Pre-compute average purchase cost per product for daily COGS estimation.
    # Uses 60-day window to match the aggregate _estimate_cogs cutoff above.
    sale_products = {r.product_id for r in month_records if r.event_type == "sale"}
    avg_costs = {}
    if sale_products:
        cutoff = cst_days_ago_bounds_utc(60)[0]
        # QA-15：与 _estimate_cogs 同步改加权平均 sum(qty×uc)/sum(qty)（按采购量加权）
        cost_query = (
            select(
                InventoryRecord.product_id,
                func.coalesce(
                    func.sum(InventoryRecord.unit_cost * InventoryRecord.quantity), 0
                ).label("cost_sum"),
                func.coalesce(func.sum(InventoryRecord.quantity), 0).label("qty_sum"),
            )
            .where(
                InventoryRecord.merchant_id == merchant_id,
                InventoryRecord.is_voided == False,  # noqa: E712
                InventoryRecord.event_type == "purchase",
                InventoryRecord.unit_cost.isnot(None),
                InventoryRecord.product_id.in_(sale_products),
                InventoryRecord.event_time >= cutoff,
            )
            .group_by(InventoryRecord.product_id)
        )
        cost_result = await db.execute(cost_query)
        for row in cost_result:
            qty_sum = float(row.qty_sum or 0)
            if qty_sum > 0:
                avg_costs[row.product_id] = float(row.cost_sum or 0) / qty_sum

    # Daily trends (per CST business day, anchored on end_date)
    daily_trends = []
    for i in range(days):
        d = anchor - timedelta(days=days - 1 - i)
        day_start, day_end = cst_day_bounds_utc(d)
        day_records = [r for r in month_records if day_start <= r.event_time < day_end]
        day_sale_records = [r for r in day_records if r.event_type == "sale"]
        day_revenue = sum(float(r.total_amount or 0) for r in day_sale_records)
        day_cost = sum(
            float(r.total_amount or 0) for r in day_records if r.event_type == "purchase"
        )
        # Per-day COGS: prefer actual FIFO unit_cost, fall back to 60-day avg.
        # Matches _estimate_cogs semantics so daily gross profit is real, not zero.
        day_cogs = sum(
            abs(float(r.quantity)) * float(r.unit_cost)
            if r.unit_cost is not None
            else abs(float(r.quantity)) * avg_costs.get(r.product_id, 0)
            for r in day_sale_records
        )
        # 客单价 = 当日营业额 / 当日销售笔数(与 /reports/trends 口径保持一致)
        day_sale_count = len(day_sale_records)
        day_customer_price = round(day_revenue / day_sale_count, 2) if day_sale_count > 0 else 0
        daily_trends.append(
            {
                "date": d.isoformat(),
                "revenue": round(day_revenue, 2),
                "cost": round(day_cost, 2),
                "profit": round(day_revenue - day_cost, 2),
                "estimated_gross_profit": round(day_revenue - day_cogs, 2),
                "customer_price": day_customer_price,
            }
        )

    # Product ranking (sales + waste)
    product_sales: dict[int, dict[str, float]] = {}
    product_waste: dict[int, dict[str, float]] = {}
    product_ids: set[int] = set()
    for r in month_records:
        pid = r.product_id
        product_ids.add(pid)
        if r.event_type == "sale":
            if pid not in product_sales:
                product_sales[pid] = {"qty": 0.0, "revenue": 0.0}
            product_sales[pid]["qty"] += abs(float(r.quantity))
            product_sales[pid]["revenue"] += float(r.total_amount or 0) if r.total_amount else 0
        elif r.event_type == "waste":
            if pid not in product_waste:
                product_waste[pid] = {"qty": 0.0, "amount": 0.0}
            product_waste[pid]["qty"] += abs(float(r.quantity))
            product_waste[pid]["amount"] += abs(float(r.total_amount or 0)) if r.total_amount else 0

    product_names = {}
    if product_ids:
        name_query = select(ProductCategory).where(ProductCategory.id.in_(product_ids))
        name_result = await db.execute(name_query)
        for p in name_result.scalars().all():
            product_names[p.id] = p.name

    sales_rows: list[SalesRankingRow] = [
        {
            "product_id": pid,
            "product_name": product_names.get(pid, f"商品{pid}"),
            "qty": round(data["qty"], 1),
            "revenue": round(data["revenue"], 2),
        }
        for pid, data in product_sales.items()
    ]
    sales_ranking = sorted(sales_rows, key=lambda row: row["revenue"], reverse=True)[:10]

    waste_rows: list[WasteRankingRow] = [
        {
            "product_id": pid,
            "product_name": product_names.get(pid, f"商品{pid}"),
            "qty": round(data["qty"], 1),
            "amount": round(data["amount"], 2),
        }
        for pid, data in product_waste.items()
    ]
    waste_ranking = sorted(waste_rows, key=lambda row: row["amount"], reverse=True)[:10]

    # Recommendation adoption (within the anchored 30-day window)
    rec_query = select(Recommendation).where(
        Recommendation.merchant_id == merchant_id,
        Recommendation.created_at >= start,
        Recommendation.created_at < end_30d,
    )
    rec_result = await db.execute(rec_query)
    recs = rec_result.scalars().all()
    total_recs = len(recs)
    adopted_recs = sum(1 for r in recs if bool(r.was_adopted))
    adoption_rate = round(adopted_recs / total_recs * 100, 1) if total_recs > 0 else 0

    # Health score (0-100)
    health = 50.0
    if month_revenue > 0:
        profit_rate = month_gross_profit / month_revenue
        health += min(20, profit_rate * 40)
    month_waste = sum(d["amount"] for d in waste_ranking)
    if month_revenue > 0 and month_waste < month_revenue * 0.1:
        health += 15
    if adoption_rate > 50:
        health += 15
    health = max(0, min(100, round(health)))

    # Data-driven AI summary (not a template)
    summary_parts = [f"近30日累计营业额{round(month_revenue, 1)}元"]
    if revenue_change is not None:
        if revenue_change > 0:
            summary_parts.append(f"较上期增长{revenue_change}%")
        elif revenue_change < 0:
            summary_parts.append(f"较上期下降{abs(revenue_change)}%")
    if month_gross_profit > 0:
        margin = round(month_gross_profit / month_revenue * 100, 1) if month_revenue > 0 else 0
        summary_parts.append(f"估算毛利{round(month_gross_profit, 1)}元(毛利率{margin}%)")
    if sales_ranking:
        summary_parts.append(f"销量最高的是{sales_ranking[0]['product_name']}")
    if waste_ranking and month_revenue > 0:
        top_waste = waste_ranking[0]
        waste_rate = round(top_waste["amount"] / month_revenue * 100, 1)
        summary_parts.append(
            f"{top_waste['product_name']}损耗最高({top_waste['amount']}元,占营收{waste_rate}%)"
        )
    if month_waste > 0 and month_revenue > 0:
        overall_waste_rate = round(month_waste / month_revenue * 100, 1)
        if overall_waste_rate > 10:
            summary_parts.append(f"整体损耗率{overall_waste_rate}%偏高,建议检查冷链和库存周转")

    ai_summary = "，".join(summary_parts) + "。"

    return {
        "code": 0,
        "data": {
            "period": "30d",
            "week_revenue": round(month_revenue, 2),
            "week_profit": round(month_profit, 2),
            "week_gross_profit": round(month_gross_profit, 2),
            "week_purchase_cost": round(month_purchase_cost, 2),
            "week_estimated_cogs": round(month_estimated_cogs, 2),
            "last_week_revenue": round(prev_revenue, 2),
            "revenue_change_pct": revenue_change,
            "daily_trends": daily_trends,
            "sales_ranking": sales_ranking,
            "waste_ranking": waste_ranking,
            "adoption_rate": adoption_rate,
            "recommendation_total": total_recs,
            "recommendation_adopted": adopted_recs,
            "health_score": health,
            "ai_summary": ai_summary,
        },
    }
