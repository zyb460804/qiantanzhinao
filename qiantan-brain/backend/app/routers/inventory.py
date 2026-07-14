"""Inventory management API router."""

import uuid
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException
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
from app.schemas import inventory as inventory_schemas
from app.schemas.common import AnyResponse
from app.services.batch import create_batch, get_active_batches, rollback_batch_on_void
from app.services.lifecycle import calc_batch_status
from app.services.offline_sync import upsert_offline_items


router = APIRouter(prefix="/api/v1/inventory", tags=["inventory"])


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

    items = [
        {
            "product_id": row.product_id,
            "sku_id": str(row.sku_id) if row.sku_id else None,
            "sku_name": sku_names.get(row.sku_id) if row.sku_id else None,
            "product_name": product_names.get(row.product_id, f"商品{row.product_id}"),
            "current_qty": round(float(row.qty), 1),
            "avg_cost": round(float(row.avg_cost), 2) if row.avg_cost is not None else None,
            "default_sale_price": sku_prices.get(row.sku_id) if row.sku_id else None,
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
        }
        for row in rows
    ]

    return {"code": 0, "data": items}


@router.get("/history", response_model=AnyResponse)
async def get_inventory_history(
    merchant_id: uuid.UUID = Depends(get_merchant_id),
    page: int = 1,
    limit: int = 20,
    db: AsyncSession = Depends(get_db),
):
    """Get inventory change history."""
    offset = (page - 1) * limit
    query = (
        select(InventoryRecord)
        .where(InventoryRecord.merchant_id == merchant_id)
        .order_by(InventoryRecord.event_time.desc())
        .offset(offset)
        .limit(limit)
    )
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
):
    """Void an inventory record by ID — rolls back batches, creates audit log."""
    query = select(InventoryRecord).where(InventoryRecord.id == record_id)
    result = await db.execute(query)
    record = result.scalar_one_or_none()
    if not record:
        raise HTTPException(status_code=404, detail="库存记录不存在")
    if record.merchant_id != merchant.id:
        raise HTTPException(status_code=404, detail="库存记录不存在")
    if record.is_voided:
        raise HTTPException(status_code=400, detail="该记录已撤销")

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

    audit = AuditLog(
        merchant_id=record.merchant_id,
        action="void",
        target_table="inventory_records",
        target_id=str(record.id),
        before_data=before_data,
        after_data={"is_voided": True, "batch_summary": batch_summary},
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


def _stocktake_item_data(item: StocktakeItem, product_name: str) -> dict:
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
    if product_ids:
        product_result = await db.execute(
            select(ProductCategory).where(ProductCategory.id.in_(product_ids))
        )
        product_names = {p.id: p.name for p in product_result.scalars().all()}

    return {
        "session_id": str(session.id),
        "status": session.status,
        "started_at": session.started_at.isoformat() if session.started_at else None,
        "notes": session.notes or "",
        "items": [
            _stocktake_item_data(item, product_names.get(item.product_id, f"商品{item.product_id}"))
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

    session = StocktakeSession(merchant_id=merchant_id, status="in_progress")
    db.add(session)
    await db.flush()

    for product_id in sorted(products_by_id):
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
    session_result = await db.execute(
        select(StocktakeSession).where(StocktakeSession.id == session_id)
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
    variance = round(actual_qty - book_qty, 2)
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
            "actual_qty": actual_qty,
            "variance": variance,
            "unit": item.unit,
        },
    }


@router.post("/stocktake/{session_id}/cancel", response_model=AnyResponse)
async def cancel_stocktake(
    session_id: uuid.UUID,
    merchant: Merchant = Depends(get_current_merchant),
    db: AsyncSession = Depends(get_db),
):
    """Cancel an in-progress stocktake. Completed sessions remain immutable."""
    session_result = await db.execute(
        select(StocktakeSession).where(StocktakeSession.id == session_id)
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
    session_result = await db.execute(
        select(StocktakeSession).where(StocktakeSession.id == session_id)
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

    for item in items:
        if item.actual_qty is None:
            raise HTTPException(status_code=409, detail="盘点数据不完整，请重新录入")
        actual_qty = float(item.actual_qty)
        book_qty = float(item.book_qty)
        variance = (
            float(item.variance) if item.variance is not None else round(actual_qty - book_qty, 2)
        )
        if item.variance is None:
            item.variance = variance
        total_book += book_qty
        total_actual += actual_qty
        total_variance += variance
        if abs(variance) < 0.01 or item.adjustment_record_id:
            continue

        prod_result = await db.execute(
            select(ProductCategory).where(ProductCategory.id == item.product_id)
        )
        product = prod_result.scalar_one_or_none()
        product_name = product.name if product else f"商品{item.product_id}"
        record = InventoryRecord(
            merchant_id=session.merchant_id,
            product_id=item.product_id,
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

        if variance < 0:
            total_loss_amount += abs(variance) * float(product.default_price or 0) if product else 0
        else:
            await create_batch(
                db,
                merchant_id=session.merchant_id,
                product_id=item.product_id,
                product_name=product_name,
                batch_label=f"盘点盘盈-{utc_now().strftime('%m%d%H%M')}",
                quantity=Decimal(str(variance)),
                unit_cost=None,
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
