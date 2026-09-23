"""Inventory management API router.

TODO(schema): app/schemas/inventory.py 中的响应模型字段（diff / total_book /
total_diff / waste_amount 等）与本路由实际返回的字段（variance / total_book_qty /
total_variance / total_loss_amount 等）不一致。因路由使用 response_model=AnyResponse
绕过 Pydantic 校验，运行无影响，但建议后续对齐 schema 文件（超出本次修复文件边界）。
"""

import uuid
from decimal import ROUND_HALF_UP, Decimal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import case, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import get_current_merchant, get_merchant_id
from app.core.tenant_context import QuotaCheck
from app.core.timezone import utc_now
from app.database import get_db
from app.models.audit import AuditLog
from app.models.batch import BatchLifecycle
from app.models.catalog import ProductSKU
from app.models.inventory import InventoryRecord
from app.models.merchant import Merchant
from app.models.product import ProductCategory
from app.models.stocktake import StocktakeItem, StocktakeSession
from app.models.voice import VoiceLog
from app.routers.staff import require_permission
from app.schemas import inventory as inventory_schemas
from app.schemas.common import AnyResponse
from app.services.batch import (
    consume_batches_fifo_costed,
    create_batch,
    get_active_batches,
    rollback_batch_on_void,
)
from app.services.lifecycle import calc_batch_status
from app.services.offline_sync import upsert_offline_items
from app.services.sku_service import resolve_sku_id
from app.services.voice_ledger import void_voice_confirmed_record


router = APIRouter(prefix="/api/v1/inventory", tags=["inventory"])


# 本路由内部使用的批量提交请求模型（避免跨文件改 schema）。
class _StocktakeBatchSubmitItem(BaseModel):
    """批量提交的单条盘点项。"""

    product_id: int
    actual_qty: float = Field(ge=0)
    variance_reason: str | None = None


class _StocktakeBatchSubmitRequest(BaseModel):
    """批量提交盘点项的请求体。"""

    items: list[_StocktakeBatchSubmitItem]


def _low_stock_threshold_for_unit(unit: str | None) -> float:
    """按单位给出经验性"余量较少"阈值。

    不同单位/业态的"余量较少"含义不同：卖菜按斤、卖水果按箱、卖调料按瓶。
    写死阈值=10 对所有商品都不合适，此处按单位给出默认值。

    TODO 迁移：建议在 product_categories 表新增 low_stock_threshold 字段，
    由摊主自定义；catalog 路由可承载按商品维度的下发。
    """
    u = (unit or "").strip()
    if u in ("两",):
        return 50.0
    if u in ("斤", "公斤", "kg", "千克"):
        return 5.0
    if u in ("箱", "件", "包", "袋", "盒"):
        return 2.0
    if u in ("个", "瓶", "只", "本", "份"):
        return 3.0
    return 10.0  # 默认阈值


@router.get("/current", response_model=AnyResponse)
async def get_current_inventory(
    merchant_id: uuid.UUID = Depends(get_merchant_id),
    db: AsyncSession = Depends(get_db),
    _quota=Depends(QuotaCheck("api_calls")),
):
    """当前库存（真实账本口径）。

    修复前缺陷：
      ① .limit(200) 截断 —— 商品多于 200 条时直接漏算；
      ② 未排除 is_voided —— 已撤销的纠错/冲正记录仍参与累加；
      ③ Python 循环累加 —— 全量拉取后在内存汇总，慢且不可靠。

    修复：单条 SQL 做 SUM/GROUP BY，排除已作废，以「标准单位」为真相。
    avg_cost 采用加权均价（成本×入库量 / 入库量），比简单 AVG 更接近真实成本；
    仅有出库（无采购）的商品均价记为 0。
    """
    agg_query = (
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
                        ),  # noqa: E501
                        else_=0,
                    )
                )
                / func.nullif(
                    func.sum(
                        case((InventoryRecord.quantity > 0, InventoryRecord.quantity), else_=0)
                    ),  # noqa: E501
                    0,
                ),
                0,
            ).label("avg_cost"),
            func.max(InventoryRecord.unit).label("unit"),
        )
        .where(
            InventoryRecord.merchant_id == merchant_id,
            InventoryRecord.is_voided == False,  # noqa: E712
        )
        .group_by(InventoryRecord.product_id)
    )
    agg_result = await db.execute(agg_query)
    rows = agg_result.all()

    # 解析商品名（product_id 维持 int 外键指向 product_categories，向后兼容）
    product_ids = {row.product_id for row in rows}
    product_names: dict[int, str] = {}
    sku_names: dict[uuid.UUID, str] = {}
    sku_prices: dict[uuid.UUID, float | None] = {}
    if product_ids:
        name_query = select(ProductCategory).where(ProductCategory.id.in_(product_ids))
        name_result = await db.execute(name_query)
        for p in name_result.scalars().all():
            product_names[p.id] = p.name
    sku_ids = {row.sku_id for row in rows if row.sku_id}
    if sku_ids:
        sku_query = select(ProductSKU).where(ProductSKU.id.in_(sku_ids))
        sku_result = await db.execute(sku_query)
        for s in sku_result.scalars().all():
            sku_names[s.id] = s.name
            sku_prices[s.id] = (
                round(float(s.default_sale_price), 2) if s.default_sale_price is not None else None
            )

    # QA-05（目录读侧）：POS 可售列表（即本接口，pos.js loadData 以 current_qty>0
    # 过滤后作商品网格）此前对 sku_id 为空的种子品类批次只能回退品类名，前端再以
    # avg_cost*1.3 估算售价（实证「白菜 ¥3.09」），商户自建同名 SKU 设的价格完全
    # 不生效。这里按名称加载商户自有活跃 SKU 作优先映射：品类名与自有 SKU 名对齐
    # 时，随行返回自有 SKU 的 id/名称/价格（新增 own_sku_* 字段，不改既有字段语义，
    # 向后兼容；种子品类仅在无自有 SKU 时兜底）。采购写入侧（from-advice
    # manual_by_name）归 purchase.py 修复，不在本接口范围。
    own_sku_by_name: dict[str, ProductSKU] = {}
    own_skus = (
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
    for s in own_skus:
        own_sku_by_name.setdefault(s.name, s)

    # Batch promotions are temporary and only apply while the batch is sellable,
    # has stock, and falls inside its explicit validity window.
    now = utc_now().replace(tzinfo=None)
    promo_query = select(BatchLifecycle).where(
        BatchLifecycle.merchant_id == merchant_id,
        BatchLifecycle.remaining_qty > 0,
        BatchLifecycle.status.in_(["sellable", "near_expiry"]),
        BatchLifecycle.promotion_price.isnot(None),
        or_(BatchLifecycle.promotion_start_at.is_(None), BatchLifecycle.promotion_start_at <= now),
        or_(BatchLifecycle.promotion_end_at.is_(None), BatchLifecycle.promotion_end_at > now),
    )
    promo_result = await db.execute(promo_query)
    promotions = promo_result.scalars().all()
    promotion_prices_by_sku: dict[uuid.UUID, float] = {}
    promotion_prices_by_product: dict[int, float] = {}
    for batch in promotions:
        if batch.promotion_price is None:
            continue
        price = round(float(batch.promotion_price), 2)
        if batch.sku_id:
            old = promotion_prices_by_sku.get(batch.sku_id)
            promotion_prices_by_sku[batch.sku_id] = price if old is None else min(old, price)
        else:
            old = promotion_prices_by_product.get(batch.product_id)
            promotion_prices_by_product[batch.product_id] = (
                price if old is None else min(old, price)
            )

    items = []
    for row in rows:
        # QA-05：商户自有 SKU（名称对齐种子品类时）优先下发，供 POS 可售列表
        # 优先展示自有名称/价格；无对齐自有 SKU 时为 None（种子品类兜底）。
        category_name = product_names.get(row.product_id)
        own = own_sku_by_name.get(category_name) if category_name else None
        own_price = (
            round(float(own.default_sale_price), 2)
            if own is not None and own.default_sale_price is not None
            else None
        )
        items.append(
            {
                "product_id": row.product_id,
                "sku_id": str(row.sku_id) if row.sku_id else None,
                "sku_name": sku_names.get(row.sku_id) if row.sku_id else None,
                "product_name": category_name or f"商品{row.product_id}",
                "current_qty": round(float(row.qty), 1),
                "avg_cost": round(float(row.avg_cost), 2) if row.avg_cost is not None else None,
                "default_sale_price": sku_prices.get(row.sku_id) if row.sku_id else None,
                "own_sku_id": str(own.id) if own is not None else None,
                "own_sku_name": own.name if own is not None else None,
                "own_sku_price": own_price,
                "promotion_price": (
                    promotion_prices_by_sku.get(row.sku_id)
                    if row.sku_id
                    else promotion_prices_by_product.get(row.product_id)
                ),
                "sale_price": (
                    promotion_prices_by_sku.get(row.sku_id)
                    if row.sku_id and row.sku_id in promotion_prices_by_sku
                    else promotion_prices_by_product.get(row.product_id)
                    or (sku_prices.get(row.sku_id) if row.sku_id else None)
                ),
                "unit": row.unit or "斤",
                # 按单位下发的低库存阈值，修复 P2：原前端写死=10 跨业态误报严重。
                "low_stock_threshold": _low_stock_threshold_for_unit(row.unit),
            }
        )

    return {"code": 0, "data": items}


@router.get("/history", response_model=AnyResponse)
async def get_inventory_history(
    merchant_id: uuid.UUID = Depends(get_merchant_id),
    page: int = 1,
    limit: int = 20,
    include_voided: bool = True,
    db: AsyncSession = Depends(get_db),
):
    """Get inventory change history.

    QA2-08：响应行补 is_voided 及撤销元数据（voided_at/voided_by/void_reason），
    已冲正行与原行可区分；默认仍全量返回（include_voided=true 向后兼容），
    需要时可用 include_voided=false 过滤掉已撤销行。
    """
    # RA-12：limit≤0 → 空列表（对齐 QA2-13 pos 语义）。SQLite 把 LIMIT -N
    # 视作无上限，负 limit 会泄全量。
    safe_limit = limit
    if safe_limit <= 0:
        return {"code": 0, "data": [], "meta": {"page": page, "limit": 0}}
    offset = (page - 1) * safe_limit
    stmt = select(InventoryRecord).where(InventoryRecord.merchant_id == merchant_id)
    # QA2-08：可选过滤已撤销行（默认不过滤，保持既有全量语义）
    if not include_voided:
        stmt = stmt.where(InventoryRecord.is_voided.is_(False))
    query = stmt.order_by(InventoryRecord.event_time.desc()).offset(offset).limit(safe_limit)
    result = await db.execute(query)
    records = result.scalars().all()

    return {
        "code": 0,
        "data": [
            {
                "id": str(r.id),
                "product_id": r.product_id,
                "sku_id": str(r.sku_id) if r.sku_id else None,
                "quantity": float(r.quantity),
                "unit": r.unit,
                "unit_cost": float(r.unit_cost) if r.unit_cost else None,
                "unit_price": float(r.unit_price) if r.unit_price else None,
                "total_amount": float(r.total_amount) if r.total_amount else None,
                "event_type": r.event_type,
                "event_time": r.event_time.isoformat() if r.event_time else None,
                "source": r.source,
                # QA2-08：撤销标记与元数据
                "is_voided": bool(r.is_voided),
                "voided_at": r.voided_at.isoformat() if r.voided_at else None,
                "voided_by": r.voided_by,
                "void_reason": r.void_reason,
            }
            for r in records
        ],
        "meta": {"page": page, "limit": limit},
    }


@router.get("/alerts", response_model=AnyResponse)
async def get_inventory_alerts(
    merchant_id: uuid.UUID = Depends(get_merchant_id),
    db: AsyncSession = Depends(get_db),
):
    """Get expiry alerts driven by batch lifecycle tracking.

    Returns active batches whose lifecycle stage is ``attention`` or
    ``expiring``, sorted by urgency (soonest to expire first).
    """
    batches = await get_active_batches(db, merchant_id)

    # Resolve product names in a single query.
    product_ids = {b.product_id for b in batches}
    product_names: dict[int, str] = {}
    if product_ids:
        name_query = select(ProductCategory).where(ProductCategory.id.in_(product_ids))
        name_result = await db.execute(name_query)
        for p in name_result.scalars().all():
            product_names[p.id] = p.name

    expiry_alerts = []
    for batch in batches:
        name = product_names.get(batch.product_id, f"商品{batch.product_id}")
        status = calc_batch_status(
            product_name=name,
            purchase_date=batch.purchase_date,
            remaining_qty=float(batch.remaining_qty),
            purchase_qty=float(batch.purchase_qty),
        )
        stage = status.get("status")
        if stage in ("attention", "expiring"):
            expiry_alerts.append(
                {
                    "batch_id": str(batch.id),
                    "product_id": batch.product_id,
                    "product_name": name,
                    "batch_label": batch.batch_label,
                    "remaining_qty": round(float(batch.remaining_qty), 1),
                    "status": stage,
                    "color": status.get("color"),
                    "hours_remaining": status.get("hours_remaining"),
                    "discount": status.get("discount", 0),
                    "message": status.get("message"),
                }
            )

    # Most urgent first; entries without hours_remaining sort last.
    expiry_alerts.sort(
        key=lambda x: (x.get("hours_remaining") is None, x.get("hours_remaining") or 0)
    )

    return {
        "code": 0,
        "data": {
            "expiry_alerts": expiry_alerts,
            "expiring_count": len(expiry_alerts),
        },
    }


# ============================================================
# P0: 记录撤销 — 直接通过 record_id 撤销库存记录
# ============================================================


@router.post("/{record_id}/void", response_model=AnyResponse)
async def void_inventory_record(
    record_id: uuid.UUID,
    req: inventory_schemas.VoidRequest,
    merchant: Merchant = Depends(get_current_merchant),
    db: AsyncSession = Depends(get_db),
    _perm=Depends(require_permission("void_record")),
):
    """Void an inventory record by ID — rolls back batches, creates audit log.

    按 source 分发（第五轮 V2-H1）：
      - "pos"   → 409，引导走订单退款链路（pos.py refund 有完整核销逻辑）；
      - "voice" → 复用 voice 侧共享撤销核心（先锁 VoiceLog 再锁流水行，
                  与 voice.py 的 void/edit 维持统一加锁次序，消除跨路径
                  双撤销竞态：双批次回滚 / 双往来账冲销）；
      - 其他    → 原有手动撤销路径（流水行锁锚点 + 批次回滚 + 审计）。
    """
    # 第一跳（无锁读，仅定位与分发）：source 是不可变字段，无 TOCTOU 风险；
    # 真正的加锁在分支内按既定次序进行。
    probe = (
        (await db.execute(select(InventoryRecord).where(InventoryRecord.id == record_id)))
        .scalars()
        .first()
    )
    if not probe or probe.merchant_id != merchant.id:
        raise HTTPException(status_code=404, detail="库存记录不存在")

    # RA-06：日结锁 —— 撤销会回滚流水与批次，等价于改写该业务日台账。
    # 按被撤流水 event_time 所属 CST 业务日判定（离线补账可跨日），口径与
    # pos._check_settlement_locked / offline-sync 锁完全一致（QA2-04 同款），
    # closed → 409，reopen 后放行。覆盖 voice 与手动两条撤销分支。
    from app.core.timezone import cst_date_of_utc_naive
    from app.routers.pos import _check_settlement_locked

    await _check_settlement_locked(
        db, merchant.id, cst_date_of_utc_naive(probe.event_time or utc_now())
    )

    if probe.source == "pos":
        # 订单体系另有完整退款链路（pos.py refund），直接撤销会绕过其核销逻辑。
        raise HTTPException(
            status_code=409,
            detail="POS订单流水不支持直接撤销，请通过订单退款链路处理",
        )

    if probe.source == "voice" and probe.voice_log_id is not None:
        # 语音链路记录：必须先锁 VoiceLog 再锁流水行（与 voice.py void/edit
        # 的加锁次序一致），否则与语音侧并发撤销构成 ABBA。
        log = (
            (
                await db.execute(
                    select(VoiceLog).where(VoiceLog.id == probe.voice_log_id).with_for_update()
                )
            )
            .scalars()
            .first()
        )
        if log is None or log.merchant_id != merchant.id:
            raise HTTPException(status_code=404, detail="库存记录不存在")

        record, batch_summary = await void_voice_confirmed_record(
            db, log, req.reason or "", voided_by="manual"
        )
        if record is None:
            # 目标流水已在别处被撤销（历史数据不一致）：回滚本次事务并按
            # 幂等语义 409，不再重复落第二套回滚/审计。
            raise HTTPException(status_code=409, detail="该记录已撤销")
        await db.commit()
        return {
            "code": 0,
            "message": "记录已撤销，库存和批次已回滚",
            "data": {"record_id": str(record.id), "batch_summary": batch_summary},
        }

    # ── 手动路径（manual / purchase_list / stocktake / food_safety / offline …）──
    # 锚点行锁：串行化同一记录的并发撤销，消除 is_voided 检查的 TOCTOU 竞态
    # （PG 生效；SQLite 静默忽略 FOR UPDATE）。
    query = select(InventoryRecord).where(InventoryRecord.id == record_id).with_for_update()
    result = await db.execute(query)
    record = result.scalar_one_or_none()
    if not record:
        raise HTTPException(status_code=404, detail="库存记录不存在")
    if record.merchant_id != merchant.id:
        raise HTTPException(status_code=404, detail="库存记录不存在")
    if record.is_voided:
        raise HTTPException(status_code=409, detail="该记录已撤销")

    before_data = {
        "quantity": float(record.quantity),
        "event_type": record.event_type,
        "product_id": record.product_id,
    }

    batch_summary = await rollback_batch_on_void(db, record.merchant_id, record.product_id, record)

    record.is_voided = True
    record.voided_at = utc_now()
    record.void_reason = req.reason or ""
    record.voided_by = "manual"

    # sa.JSON 列默认走 json.dumps，不支持 Decimal——落库前转为 float
    # （服务层返回值保持 Decimal 不变，仅在此 JSON 边界转换）。
    audit_summary = {**batch_summary, "qty_adjusted": float(batch_summary["qty_adjusted"])}
    audit = AuditLog(
        merchant_id=record.merchant_id,
        action="void",
        target_table="inventory_records",
        target_id=str(record.id),
        before_data=before_data,
        after_data={"is_voided": True, "batch_summary": audit_summary},
        reason=req.reason or "",
        operator="merchant",
    )
    db.add(audit)
    await db.commit()

    return {
        "code": 0,
        "message": "记录已撤销，库存和批次已回滚",
        "data": {"record_id": str(record.id), "batch_summary": batch_summary},
    }


# ============================================================
# P0: 库存盘点 — 账面对比实际，生成调整记录
# ============================================================


def _stocktake_item_data(item: StocktakeItem, product_name: str, avg_cost: float = 0.0) -> dict:
    """Serialize one persisted stocktake snapshot line."""
    actual_qty = float(item.actual_qty) if item.actual_qty is not None else None
    variance = float(item.variance) if item.variance is not None else None
    return {
        "item_id": str(item.id),
        "product_id": item.product_id,
        "product_name": product_name,
        "unit": item.unit,
        "book_qty": float(item.book_qty),
        "actual_qty": actual_qty,
        "variance": variance,
        "variance_reason": item.variance_reason,
        "submitted": actual_qty is not None,
        # 返回加权均价，供前端实时预估损耗金额（修复 P1：原缺失导致预估损耗恒为 0）。
        "avg_cost": round(float(avg_cost or 0), 2),
    }


async def _stocktake_session_data(db: AsyncSession, session: StocktakeSession) -> dict:
    item_result = await db.execute(
        select(StocktakeItem)
        .where(StocktakeItem.session_id == session.id)
        .order_by(StocktakeItem.product_id)
    )
    items = item_result.scalars().all()
    product_ids = {item.product_id for item in items}
    product_names: dict[int, str] = {}
    avg_costs: dict[int, float] = {}
    if product_ids:
        product_result = await db.execute(
            select(ProductCategory).where(ProductCategory.id.in_(product_ids))
        )
        product_names = {p.id: p.name for p in product_result.scalars().all()}

        # 计算每个商品的加权均价（与 /current 接口同口径），用于盘点过程中预估损耗金额。
        # QA2-07：补 merchant_id 过滤 —— 此前仅按 product_id 聚合，多租户下把
        # 别家商户同商品采购混入均价（实测白菜盘点页 1.30 vs 库存页 2.10）。
        cost_result = await db.execute(
            select(
                InventoryRecord.product_id,
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
                                (
                                    InventoryRecord.quantity > 0,
                                    InventoryRecord.quantity,
                                ),
                                else_=0,
                            )
                        ),
                        0,
                    ),
                    0,
                ).label("avg_cost"),
            )
            .where(
                InventoryRecord.merchant_id == session.merchant_id,
                InventoryRecord.product_id.in_(product_ids),
                InventoryRecord.is_voided == False,  # noqa: E712
            )
            .group_by(InventoryRecord.product_id)
        )
        for row in cost_result:
            avg_costs[row.product_id] = round(float(row.avg_cost), 2)

    return {
        "session_id": str(session.id),
        "status": session.status,
        "started_at": session.started_at.isoformat() if session.started_at else None,
        "notes": session.notes or "",
        "items": [
            _stocktake_item_data(
                item,
                product_names.get(item.product_id, f"商品{item.product_id}"),
                avg_costs.get(item.product_id, 0.0),
            )
            for item in items
        ],
    }


@router.get("/stocktake/current", response_model=AnyResponse)
async def current_stocktake(
    merchant: Merchant = Depends(get_current_merchant),
    db: AsyncSession = Depends(get_db),
):
    """Return the merchant's in-progress session and all persisted snapshot lines."""
    result = await db.execute(
        select(StocktakeSession)
        .where(
            StocktakeSession.merchant_id == merchant.id,
            StocktakeSession.status == "in_progress",
        )
        .order_by(StocktakeSession.started_at.desc())
        .limit(1)
    )
    session = result.scalar_one_or_none()
    if session is None:
        return {"code": 0, "data": None}
    return {"code": 0, "data": await _stocktake_session_data(db, session)}


@router.post("/stocktake/start", response_model=AnyResponse)
async def start_stocktake(
    merchant: Merchant = Depends(get_current_merchant),
    db: AsyncSession = Depends(get_db),
):
    """Start a session and persist the complete book-inventory snapshot."""
    merchant_id = merchant.id
    existing_result = await db.execute(
        select(StocktakeSession).where(
            StocktakeSession.merchant_id == merchant_id,
            StocktakeSession.status == "in_progress",
        )
    )
    existing = existing_result.scalar_one_or_none()
    if existing:
        raise HTTPException(
            status_code=400,
            detail="已有进行中的盘点，请继续完成或取消后再开始新盘点",
        )

    agg_result = await db.execute(
        select(
            InventoryRecord.product_id,
            func.sum(InventoryRecord.quantity).label("book_qty"),
        )
        .where(
            InventoryRecord.merchant_id == merchant_id,
            InventoryRecord.is_voided == False,  # noqa: E712
        )
        .group_by(InventoryRecord.product_id)
    )
    book_qty_by_product = {row.product_id: round(float(row.book_qty), 2) for row in agg_result}

    products_result = await db.execute(
        select(ProductCategory).where(ProductCategory.is_active == True)  # noqa: E712
    )
    products = products_result.scalars().all()
    products_by_id = {p.id: p for p in products}

    # Keep ledger products in the snapshot even when their category was later deactivated.
    missing_ids = set(book_qty_by_product) - set(products_by_id)
    if missing_ids:
        missing_result = await db.execute(
            select(ProductCategory).where(ProductCategory.id.in_(missing_ids))
        )
        for product in missing_result.scalars().all():
            products_by_id[product.id] = product

    # P2-2 修复：只为「有账面流水」的品项生成待盘项（账面 0 但有历史流水
    # 的仍保留——可能漏记）。此前全品类生成（12 项里 11 项从未交易过），
    # 摊主被迫逐项录 0 才能完成盘点。
    ledger_product_ids = sorted(pid for pid in book_qty_by_product if pid in products_by_id)
    if not ledger_product_ids:
        raise HTTPException(
            status_code=400,
            detail="还没有任何库存流水，先记一笔进货再盘点",
        )

    session = StocktakeSession(merchant_id=merchant_id, status="in_progress")
    db.add(session)
    await db.flush()

    for product_id in ledger_product_ids:
        product = products_by_id[product_id]
        db.add(
            StocktakeItem(
                session_id=session.id,
                merchant_id=merchant_id,
                product_id=product_id,
                book_qty=book_qty_by_product.get(product_id, 0.0),
                actual_qty=None,
                variance=None,
                unit=product.unit or "斤",
            )
        )

    await db.commit()
    await db.refresh(session)
    return {
        "code": 0,
        "message": "盘点已开始，账面库存快照已锁定",
        "data": await _stocktake_session_data(db, session),
    }


@router.post("/stocktake/{session_id}/submit", response_model=AnyResponse)
async def submit_stocktake_item(
    session_id: uuid.UUID,
    req: inventory_schemas.StocktakeSubmitRequest,
    merchant: Merchant = Depends(get_current_merchant),
    db: AsyncSession = Depends(get_db),
):
    """Submit one actual count against the immutable start-time snapshot."""
    # 锚点行锁（V2-H3）：与 complete 同款——先锁会话行再做状态检查，
    # 串行化 submit 与 complete/cancel 的并发交错（否则 complete 提交后，
    # 在途的 submit 仍能把已结束会话的条目改写）。
    session_result = await db.execute(
        select(StocktakeSession).where(StocktakeSession.id == session_id).with_for_update()
    )
    session = session_result.scalar_one_or_none()
    if not session or session.merchant_id != merchant.id:
        raise HTTPException(status_code=404, detail="盘点会话不存在")
    if session.status != "in_progress":
        raise HTTPException(status_code=400, detail="该盘点已结束")

    item_result = await db.execute(
        select(StocktakeItem).where(
            StocktakeItem.session_id == session_id,
            StocktakeItem.product_id == req.product_id,
        )
    )
    item = item_result.scalar_one_or_none()
    if item is None:
        raise HTTPException(status_code=400, detail="该商品不在本次盘点快照中")

    actual_qty = req.actual_qty
    book_qty = float(item.book_qty)
    variance = float(
        (Decimal(str(actual_qty)) - Decimal(str(book_qty))).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )
    )
    item.actual_qty = actual_qty
    item.variance = variance
    item.variance_reason = req.variance_reason or req.diff_reason or ""
    await db.commit()

    return {
        "code": 0,
        "message": "盘点项已保存",
        "data": {
            "item_id": str(item.id),
            "product_id": item.product_id,
            "book_qty": round(book_qty, 2),
            "actual_qty": float(actual_qty),
            "variance": variance,
            "unit": item.unit,
        },
    }


@router.post("/stocktake/{session_id}/submit-batch", response_model=AnyResponse)
async def submit_stocktake_batch(
    session_id: uuid.UUID,
    req: _StocktakeBatchSubmitRequest,
    merchant: Merchant = Depends(get_current_merchant),
    db: AsyncSession = Depends(get_db),
):
    """批量提交盘点项，弱网场景下用一次请求替代逐项串行提交。

    返回每个 product_id 的处理结果（ok / error），调用方按结果更新本地状态。
    """
    # 锚点行锁（V2-H3）：与 complete 同款，先锁会话行再做状态检查。
    session_result = await db.execute(
        select(StocktakeSession).where(StocktakeSession.id == session_id).with_for_update()
    )
    session = session_result.scalar_one_or_none()
    if not session or session.merchant_id != merchant.id:
        raise HTTPException(status_code=404, detail="盘点会话不存在")
    if session.status != "in_progress":
        raise HTTPException(status_code=400, detail="该盘点已结束")
    if not req.items:
        raise HTTPException(status_code=400, detail="未提供盘点数据")

    # 预加载本会话所有 items，避免逐条查询造成 N+1
    item_result = await db.execute(
        select(StocktakeItem).where(StocktakeItem.session_id == session_id)
    )
    items_by_pid = {it.product_id: it for it in item_result.scalars().all()}

    results = []
    for entry in req.items:
        pid = entry.product_id
        item = items_by_pid.get(pid)
        if item is None:
            results.append({"product_id": pid, "status": "error", "message": "不在快照中"})
            continue
        actual_qty = float(entry.actual_qty)
        book_qty = float(item.book_qty)
        variance = float(
            (Decimal(str(actual_qty)) - Decimal(str(book_qty))).quantize(
                Decimal("0.01"), rounding=ROUND_HALF_UP
            )
        )
        item.actual_qty = actual_qty
        item.variance = variance
        item.variance_reason = entry.variance_reason or ""
        results.append(
            {
                "product_id": pid,
                "status": "ok",
                "item_id": str(item.id),
                "book_qty": round(book_qty, 2),
                "actual_qty": actual_qty,
                "variance": variance,
                "unit": item.unit,
            }
        )
    await db.commit()

    ok_count = sum(1 for r in results if r["status"] == "ok")
    return {
        "code": 0,
        "message": f"批量保存完成：成功 {ok_count} 项，失败 {len(results) - ok_count} 项",
        "data": {"results": results},
    }


@router.post("/stocktake/{session_id}/cancel", response_model=AnyResponse)
async def cancel_stocktake(
    session_id: uuid.UUID,
    merchant: Merchant = Depends(get_current_merchant),
    db: AsyncSession = Depends(get_db),
):
    """Cancel an in-progress stocktake. Completed sessions remain immutable."""
    # 锚点行锁（V2-H3）：与 complete 同款——先锁会话行再做状态检查，
    # 串行化 cancel 与 submit/complete 的并发交错。completed → 守卫拒绝，
    # 不会被覆写为 cancelled。
    session_result = await db.execute(
        select(StocktakeSession).where(StocktakeSession.id == session_id).with_for_update()
    )
    session = session_result.scalar_one_or_none()
    if not session or session.merchant_id != merchant.id:
        raise HTTPException(status_code=404, detail="盘点会话不存在")
    if session.status == "completed":
        raise HTTPException(status_code=400, detail="已完成的盘点不能取消")
    if session.status == "cancelled":
        return {
            "code": 0,
            "message": "该盘点已取消",
            "data": {"session_id": str(session.id), "status": "cancelled"},
        }

    session.status = "cancelled"
    session.completed_at = utc_now()
    await db.commit()
    return {
        "code": 0,
        "message": "盘点已取消",
        "data": {"session_id": str(session.id), "status": "cancelled"},
    }


@router.post("/stocktake/{session_id}/complete", response_model=AnyResponse)
async def complete_stocktake(
    session_id: uuid.UUID,
    req: inventory_schemas.StocktakeCompleteRequest,
    merchant: Merchant = Depends(get_current_merchant),
    db: AsyncSession = Depends(get_db),
):
    """Complete a stocktake after every persisted snapshot line is counted."""
    # RA-08：日结锁 —— complete 是落账动作（盘亏落 waste 流水、盘盈落 adjustment
    # 与批次），已 close 的当日日结不得再被校准。盘点是当日操作，按 CST 今日
    # 业务日加锁，口径与 operations.record_waste 一致（QA2-04 同款，reopen 放行）。
    from app.routers.pos import _check_settlement_locked

    await _check_settlement_locked(db, merchant.id)
    # 锚点行锁：串行化同一会话的并发 complete，防止双并发各自 INSERT 调整记录与
    # 盘盈批次（PG 生效；SQLite 静默忽略 FOR UPDATE）。
    session_result = await db.execute(
        select(StocktakeSession).where(StocktakeSession.id == session_id).with_for_update()
    )
    session = session_result.scalar_one_or_none()
    if not session or session.merchant_id != merchant.id:
        raise HTTPException(status_code=404, detail="盘点会话不存在")
    if session.status == "cancelled":
        raise HTTPException(status_code=400, detail="该盘点已取消")
    if session.status == "completed":
        return {
            "code": 0,
            "message": "该盘点已完成",
            "data": {
                "session_id": str(session.id),
                "total_book_qty": float(session.total_book_qty or 0),
                "total_actual_qty": float(session.total_actual_qty or 0),
                "total_variance": float(session.total_variance or 0),
                "total_loss_amount": float(session.total_loss_amount or 0),
                "adjustments": [],
            },
        }

    items_result = await db.execute(
        select(StocktakeItem).where(StocktakeItem.session_id == session_id)
    )
    items = items_result.scalars().all()
    if not items:
        raise HTTPException(status_code=400, detail="本次盘点没有可盘商品")

    pending_count = sum(item.actual_qty is None for item in items)
    if pending_count:
        raise HTTPException(
            status_code=400,
            detail=f"仍有 {pending_count} 项商品未录入实盘数量",
        )

    total_book = 0.0
    total_actual = 0.0
    total_variance = 0.0
    total_loss_amount = 0.0
    adjustments = []

    # 修复 F3：批量预加载本会话涉及的商品，避免循环内逐条查 ProductCategory 造成 N+1。
    product_ids = {item.product_id for item in items}
    product_map: dict[int, ProductCategory] = {}
    if product_ids:
        prod_result = await db.execute(
            select(ProductCategory).where(ProductCategory.id.in_(product_ids))
        )
        product_map = {p.id: p for p in prod_result.scalars().all()}

    # QA2-09：损耗汇总口径统一 —— complete 阶段不再按 avg_cost 估算损耗金额
    # （与落账 waste 的 FIFO 成本两口径，实测 9.46 vs 10.00），改为在落账分支
    # 直接累加 waste 流水的 total_amount（FIFO 实扣成本），汇总与台账同源。
    # avg_cost 预计算已随之下线（展示侧 avg_cost 见 _stocktake_session_data，
    # 已按 QA2-07 补 merchant 过滤与 /current 同口径）。

    for item in items:
        if item.actual_qty is None:
            raise HTTPException(status_code=409, detail="盘点数据不完整，请重新录入")
        actual_qty = float(item.actual_qty)
        book_qty = float(item.book_qty)
        variance = (
            float(item.variance)
            if item.variance is not None
            else float(
                (Decimal(str(actual_qty)) - Decimal(str(book_qty))).quantize(
                    Decimal("0.01"), rounding=ROUND_HALF_UP
                )
            )
        )
        if item.variance is None:
            item.variance = variance
        total_book += book_qty
        total_actual += actual_qty
        total_variance += variance
        if abs(variance) < 0.01 or item.adjustment_record_id:
            continue

        product = product_map.get(item.product_id)
        product_name = product.name if product else f"商品{item.product_id}"
        # 修复 F2：调整记录需解析并填充 sku_id，保证账本与 SKU 体系对齐。
        sku_id = await resolve_sku_id(db, session.merchant_id, product_id=item.product_id)

        if variance < 0:
            # QA-20：盘亏按报损口径落账 —— event_type='waste'，成本按 FIFO
            # 从批次实扣（参照 operations.record_waste 的成本化做法），批次
            # remaining 同步扣减，消除「流水净和 ≠ 批次余量」的台账偏差；
            # 日报 waste_amount / 日结 waste_cost 因此能计入盘亏金额。
            # 批次缺失或批次无成本时不猜成本（unit_cost/total_amount 置空），
            # 但流水仍按完整差异落账，保证账面库存校准到实盘（原语义不变）。
            loss_qty = Decimal(str(abs(variance)))
            consumption = await consume_batches_fifo_costed(
                db,
                session.merchant_id,
                item.product_id,
                loss_qty,
                sku_id=sku_id,
                fallback_to_unowned=True,  # QA-04 家族：语音/POS 兜底批次 sku_id=NULL
            )
            fully_costed = (
                consumption["quantity"] >= loss_qty
                and consumption["quantity"] > 0
                and consumption["missing_cost_quantity"] == 0
            )
            waste_unit_cost = (
                (consumption["total_cost"] / consumption["quantity"]).quantize(Decimal("0.01"))
                if fully_costed
                else None
            )
            record = InventoryRecord(
                merchant_id=session.merchant_id,
                product_id=item.product_id,
                sku_id=sku_id,
                quantity=variance,
                unit=item.unit,
                unit_cost=waste_unit_cost,
                total_amount=(
                    consumption["total_cost"].quantize(Decimal("0.01"))
                    if waste_unit_cost is not None
                    else None
                ),
                event_type="waste",
                event_time=utc_now(),
                source="stocktake",
                notes=(
                    f"盘点盘亏: 账面{book_qty:.2f}, 实盘{actual_qty:.2f}, "
                    f"原因: {item.variance_reason or '未说明'}"
                ),
            )
            # QA2-09：汇总与落账同源 —— total_loss_amount 按实际落账 waste 的
            # FIFO total_amount 求和；批次缺成本时落账 total_amount 为空，同样
            # 不计入汇总（两口径严格一致）。
            if fully_costed:
                total_loss_amount += float(consumption["total_cost"].quantize(Decimal("0.01")))
        else:
            # 盘盈（正差异）：保持 adjustment 语义 + 生成盘盈批次（原行为不变）
            record = InventoryRecord(
                merchant_id=session.merchant_id,
                product_id=item.product_id,
                sku_id=sku_id,
                quantity=variance,
                unit=item.unit,
                event_type="adjustment",
                event_time=utc_now(),
                source="stocktake",
                notes=(
                    f"盘点调整: 账面{book_qty:.2f}, 实盘{actual_qty:.2f}, "
                    f"原因: {item.variance_reason or '未说明'}"
                ),
            )
        db.add(record)
        await db.flush()
        item.adjustment_record_id = record.id

        if variance > 0:
            await create_batch(
                db,
                merchant_id=session.merchant_id,
                product_id=item.product_id,
                product_name=product_name,
                batch_label=f"盘点盘盈-{utc_now().strftime('%m%d%H%M')}",
                quantity=Decimal(str(variance)),
                unit_cost=None,
                sku_id=sku_id,
            )

        adjustments.append(
            {
                "product_id": item.product_id,
                "product_name": product_name,
                "book_qty": float(item.book_qty),
                "actual_qty": actual_qty,
                "variance": variance,
                "unit": item.unit,
                "adjustment_record_id": str(record.id),
            }
        )

    session.status = "completed"
    session.total_book_qty = round(total_book, 2)
    session.total_actual_qty = round(total_actual, 2)
    session.total_variance = round(total_variance, 2)
    session.total_loss_amount = round(total_loss_amount, 2)
    session.completed_at = utc_now()
    session.notes = req.notes or ""
    db.add(
        AuditLog(
            merchant_id=session.merchant_id,
            action="stocktake",
            target_table="stocktake_sessions",
            target_id=str(session.id),
            before_data=None,
            after_data={
                "total_book": round(total_book, 2),
                "total_actual": round(total_actual, 2),
                "total_variance": round(total_variance, 2),
                "total_loss_amount": round(total_loss_amount, 2),
                "adjustments_count": len(adjustments),
            },
            reason=req.notes or "库存盘点完成",
            operator="merchant",
        )
    )
    await db.commit()
    return {
        "code": 0,
        "message": "盘点完成，库存已校准",
        "data": {
            "session_id": str(session.id),
            "total_book_qty": round(total_book, 2),
            "total_actual_qty": round(total_actual, 2),
            "total_variance": round(total_variance, 2),
            "total_loss_amount": round(total_loss_amount, 2),
            "adjustments": adjustments,
        },
    }


@router.get("/stocktake/history", response_model=AnyResponse)
async def stocktake_history(
    merchant_id: uuid.UUID = Depends(get_merchant_id),
    page: int = 1,
    limit: int = 10,
    db: AsyncSession = Depends(get_db),
):
    """Get past stocktake sessions for a merchant."""
    # RA-12：limit≤0 → 空列表（对齐 QA2-13 pos 语义，防 LIMIT -N 泄全量）。
    if limit <= 0:
        return {"code": 0, "data": [], "meta": {"page": page, "limit": 0}}
    offset = (page - 1) * limit
    query = (
        select(StocktakeSession)
        .where(StocktakeSession.merchant_id == merchant_id)
        .order_by(StocktakeSession.started_at.desc())
        .offset(offset)
        .limit(limit)
    )
    result = await db.execute(query)
    sessions = result.scalars().all()

    return {
        "code": 0,
        "data": [
            {
                "id": str(s.id),
                "status": s.status,
                "total_book_qty": float(s.total_book_qty) if s.total_book_qty else None,
                "total_actual_qty": float(s.total_actual_qty) if s.total_actual_qty else None,
                "total_variance": float(s.total_variance) if s.total_variance else None,
                "total_loss_amount": float(s.total_loss_amount) if s.total_loss_amount else None,
                "notes": s.notes,
                "started_at": s.started_at.isoformat() if s.started_at else None,
                "completed_at": s.completed_at.isoformat() if s.completed_at else None,
            }
            for s in sessions
        ],
        "meta": {"page": page, "limit": limit},
    }


@router.get("/stocktake/history/{session_id}", response_model=AnyResponse)
async def stocktake_history_detail(
    session_id: uuid.UUID,
    merchant: Merchant = Depends(get_current_merchant),
    db: AsyncSession = Depends(get_db),
):
    """查看某次盘点记录的逐项明细，供历史卡片展开使用。"""
    session_result = await db.execute(
        select(StocktakeSession).where(StocktakeSession.id == session_id)
    )
    session = session_result.scalar_one_or_none()
    if not session or session.merchant_id != merchant.id:
        raise HTTPException(status_code=404, detail="盘点记录不存在")
    data = await _stocktake_session_data(db, session)
    return {"code": 0, "data": data}


# =====================================================================
# P0: 离线记账 / 断网同步 —— 幂等批量入账
# =====================================================================


@router.post("/offline-sync", response_model=AnyResponse)
async def sync_offline_items(
    req: inventory_schemas.OfflineSyncRequest,
    merchant: Merchant = Depends(get_current_merchant),
    db: AsyncSession = Depends(get_db),
):
    """Sync a batch of offline-cached business events idempotently.

    Each item must carry a client-generated `idempotency_key`. The server
    guarantees the same action is booked exactly once via the unique
    constraint on `InventoryRecord.idempotency_key`.

    The caller receives per-item results (`created` / `duplicate` / `error`)
    so the client can clean up successfully synced items.
    """
    # QA2-04：日结锁 —— 落库前按每条记录 event_time 所属 CST 业务日判定，
    # 任一记录落入已日结（closed）的业务日即整单 409（含日期，语义清楚、
    # 实现最简：离线队列本就按天缓存，重开后整批重放即可）。口径与
    # pos._check_settlement_locked 完全一致（直接复用，reopen 后放行）。
    from app.core.timezone import cst_date_of_utc_naive, parse_iso_datetime, utc_now
    from app.routers.pos import _check_settlement_locked

    checked_days: set = set()
    for item in req.items:
        parsed = parse_iso_datetime(item.event_time) if item.event_time else None
        business_day = cst_date_of_utc_naive(parsed or utc_now())
        if business_day not in checked_days:
            checked_days.add(business_day)
            await _check_settlement_locked(db, merchant.id, business_day)

    results = await upsert_offline_items(db, merchant.id, req.items)
    # The service only flushes inside per-item savepoints; the endpoint owns the
    # outer transaction and commits all successful items exactly once.
    await db.commit()

    # Aggregate summary for the client to update its queue.
    created = sum(1 for r in results if r["status"] == "created")
    duplicate = sum(1 for r in results if r["status"] == "duplicate")
    errors = [r for r in results if r["status"] == "error"]

    return {
        "code": 0,
        "message": f"离线同步完成：新建 {created} 条，重复 {duplicate} 条，失败 {len(errors)} 条",
        "data": {
            "created": created,
            "duplicate": duplicate,
            "failed": len(errors),
            "results": results,
            "errors": errors,
        },
    }


# =====================================================================
# §4.4: 库存统一流水报告 — 按状态分类的库存全景
# =====================================================================


@router.get("/ledger/summary", response_model=AnyResponse)
async def stock_ledger_summary(
    merchant: Merchant = Depends(get_current_merchant),
    db: AsyncSession = Depends(get_db),
):
    """§4.4 库存统一流水汇总报告。

    按库存状态分类：
    - 账面库存: SUM(all non-voided inventory records)
    - 可售库存: 账面库存 - locked batches
    - 锁定库存: sum of locked batch remaining_qty
    - 报损库存: sum of waste records (current period)
    - 预占库存: held POS orders (挂单未支付)

    Returns breakdown by event_type and inventory state.
    """
    # Book inventory by event type
    book_query = (
        select(
            InventoryRecord.event_type,
            func.sum(InventoryRecord.quantity).label("total_qty"),
            func.sum(InventoryRecord.total_amount).label("total_amount"),
            func.count(InventoryRecord.id).label("record_count"),
        )
        .where(
            InventoryRecord.merchant_id == merchant.id,
            InventoryRecord.is_voided == False,  # noqa: E712
        )
        .group_by(InventoryRecord.event_type)
    )
    book_result = await db.execute(book_query)

    by_event_type = {}
    total_book_qty = 0.0
    total_book_amount = 0.0
    for row in book_result:
        qty = float(row.total_qty or 0)
        amt = float(row.total_amount or 0)
        by_event_type[row.event_type] = {
            "quantity": round(qty, 2),
            "amount": round(amt, 2),
            "records": row.record_count,
        }
        total_book_qty += qty
        total_book_amount += amt

    # Locked inventory (from batch_lifecycles)
    from app.models.batch import BatchLifecycle

    locked_qty = float(
        (
            await db.execute(
                select(func.sum(BatchLifecycle.remaining_qty)).where(
                    BatchLifecycle.merchant_id == merchant.id,
                    BatchLifecycle.status == "locked",
                )
            )
        ).scalar()
        or 0
    )

    # Held (pre-allocated) inventory from held POS orders
    from app.models.pos import SaleOrder, SaleOrderItem

    held_orders = (
        (
            await db.execute(
                select(SaleOrder.id).where(
                    SaleOrder.merchant_id == merchant.id,
                    SaleOrder.status == "held",
                )
            )
        )
        .scalars()
        .all()
    )
    held_qty = 0.0
    if held_orders:
        held_qty = float(
            (
                await db.execute(
                    select(func.sum(SaleOrderItem.quantity)).where(
                        SaleOrderItem.order_id.in_(held_orders),
                    )
                )
            ).scalar()
            or 0
        )

    # Waste this month
    from app.core.timezone import utc_now

    month_start = utc_now().replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    waste_qty = float(
        (
            await db.execute(
                select(func.sum(func.abs(InventoryRecord.quantity))).where(
                    InventoryRecord.merchant_id == merchant.id,
                    InventoryRecord.is_voided == False,  # noqa: E712
                    InventoryRecord.event_type == "waste",
                    InventoryRecord.event_time >= month_start,
                )
            )
        ).scalar()
        or 0
    )

    # Sellable = book - locked - held
    sellable_qty = total_book_qty - locked_qty - held_qty

    # Get product count
    from app.models.product import ProductCategory

    active_product_count = (
        await db.execute(
            select(func.count(ProductCategory.id)).where(
                ProductCategory.is_active == True,  # noqa: E712
            )
        )
    ).scalar() or 0

    return {
        "code": 0,
        "data": {
            "inventory_states": {
                "book": {
                    "quantity": round(total_book_qty, 2),
                    "amount": round(total_book_amount, 2),
                    "label": "账面库存",
                },
                "sellable": {
                    "quantity": round(sellable_qty, 2),
                    "label": "可售库存",
                },
                "locked": {
                    "quantity": round(locked_qty, 2),
                    "label": "锁定库存",
                },
                "held": {
                    "quantity": round(held_qty, 2),
                    "label": "预占库存（挂单）",
                },
                "waste_this_month": {
                    "quantity": round(waste_qty, 2),
                    "label": "本月报损",
                },
            },
            "by_event_type": by_event_type,
            "active_products": active_product_count,
        },
    }
