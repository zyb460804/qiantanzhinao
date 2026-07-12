"""Purchase list API — AI recommendation → acceptance → batch → payable → payment → statement.

阶段A 采购验收闭环:
  POST   /from-advice             AI建议生成采购单
  GET    /today                   今日采购清单
  PUT    /item/{id}               修改采购项
  DELETE /item/{id}               取消采购项
  POST   /{id}/acceptance         记录到货验收
  POST   /{id}/acceptance/confirm 确认验收 → 批次入库+库存+应付
  POST   /{id}/cancel             取消采购清单
  POST   /supplier-payment        向供应商付款
  POST   /items/{id}/return       退货给供应商
  GET    /supplier/{id}/statement 供应商对账单
"""

import uuid
from decimal import Decimal

from fastapi import APIRouter, Body, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import get_current_merchant
from app.core.timezone import utc_now, utc_today_start
from app.database import get_db
from app.models.accounts import SupplierPayable
from app.models.audit import AuditLog
from app.models.catalog import Supplier
from app.models.inventory import InventoryRecord
from app.models.merchant import Merchant
from app.models.product import ProductCategory
from app.models.purchase import PurchaseItem, PurchaseList
from app.models.recommendation import Recommendation
from app.schemas.common import AnyResponse
from app.schemas.purchase import (
    ConfirmAcceptanceRequest,
    PurchaseReturnRequest,
    RecordAcceptanceRequest,
    SupplierPaymentRequest,
)
from app.services.accounts_service import (
    get_supplier_balance,
    get_supplier_statement,
    record_supplier_payable_from_purchase,
    record_supplier_payment,
)
from app.services.batch import create_batch
from app.services.sku_service import resolve_sku_id


router = APIRouter(prefix="/api/v1/purchase", tags=["purchase"])

# 合法状态转换
VALID_TRANSITIONS: dict[str, set[str]] = {
    "draft": {"confirmed", "cancelled"},
    "confirmed": {"partial_arrival", "accepted", "cancelled"},
    "partial_arrival": {"partial_arrival", "accepted", "cancelled"},
    "accepted": {"stored", "cancelled"},
    "stored": {"completed", "returned"},
    "completed": set(),
    "returned": set(),
    "cancelled": set(),
}


def _validate_transition(current: str, target: str) -> None:
    allowed = VALID_TRANSITIONS.get(current, set())
    if target not in allowed:
        raise HTTPException(
            status_code=409,
            detail=f"不允许从 {current} 转换到 {target}，允许: {sorted(allowed)}",
        )


async def _get_product_map(db: AsyncSession, product_ids: set[int]) -> dict[int, ProductCategory]:
    if not product_ids:
        return {}
    products = (
        (await db.execute(select(ProductCategory).where(ProductCategory.id.in_(product_ids))))
        .scalars()
        .all()
    )
    return {p.id: p for p in products}


def _to_d(v) -> Decimal:
    if v is None:
        return Decimal("0")
    if isinstance(v, Decimal):
        return v
    return Decimal(str(v))


# ---------------------------------------------------------------------------
# AI建议 → 采购单
# ---------------------------------------------------------------------------


@router.post("/from-advice", response_model=AnyResponse)
async def create_from_advice(
    body: dict = Body(default={}),
    merchant: Merchant = Depends(get_current_merchant),
    db: AsyncSession = Depends(get_db),
):
    """Generate a purchase list from today's AI recommendations."""
    merchant_id = merchant.id
    recommendation_ids = body.get("recommendation_ids", [])

    today_start = utc_today_start()
    if recommendation_ids:
        rec_query = select(Recommendation).where(
            Recommendation.id.in_([uuid.UUID(rid) for rid in recommendation_ids]),
        )
    else:
        rec_query = select(Recommendation).where(
            Recommendation.merchant_id == merchant_id,
            Recommendation.created_at >= today_start,
        )
    rec_result = await db.execute(rec_query)
    recs = rec_result.scalars().all()

    if not recs:
        raise HTTPException(status_code=404, detail="未找到可用的采购建议")

    existing_query = select(PurchaseList).where(
        PurchaseList.merchant_id == merchant_id,
        PurchaseList.status.in_(["draft", "confirmed"]),
    )
    existing_result = await db.execute(existing_query)
    existing_list = existing_result.scalars().first()

    if existing_list:
        plist = existing_list
    else:
        plist = PurchaseList(merchant_id=merchant_id, status="draft")
        db.add(plist)
        await db.flush()

    product_ids = {r.product_id for r in recs}
    product_map = await _get_product_map(db, product_ids)

    total_estimated = Decimal("0")
    added_count = 0
    for rec in recs:
        product = product_map.get(rec.product_id)
        if not product:
            continue

        item_query = select(PurchaseItem).where(
            PurchaseItem.list_id == plist.id,
            PurchaseItem.product_id == rec.product_id,
            PurchaseItem.status == "pending",
        )
        item_result = await db.execute(item_query)
        if item_result.scalar_one_or_none():
            continue

        qty = rec.recommended_qty or Decimal("0")
        if qty <= 0:
            continue

        est_cost = product.default_price or Decimal("0")
        est_total = (qty * est_cost).quantize(Decimal("0.01"))
        total_estimated += est_total

        item = PurchaseItem(
            list_id=plist.id, merchant_id=merchant_id,
            recommendation_id=rec.id, product_id=rec.product_id,
            sku_id=rec.sku_id, recommended_qty=qty, actual_qty=qty,
            unit=product.unit, estimated_unit_cost=est_cost,
            estimated_cost=est_total, status="pending", reason=rec.suggestion,
        )
        db.add(item)
        added_count += 1

    plist.total_estimated_cost = total_estimated.quantize(Decimal("0.01"))
    plist.item_count = added_count
    await db.commit()

    return {
        "code": 0, "message": f"已生成采购清单，共{added_count}项",
        "data": {"list_id": str(plist.id), "item_count": added_count},
    }


# ---------------------------------------------------------------------------
# 今日清单
# ---------------------------------------------------------------------------


@router.get("/today", response_model=AnyResponse)
async def get_today_list(
    merchant: Merchant = Depends(get_current_merchant),
    db: AsyncSession = Depends(get_db),
):
    """Get today's active purchase list with all items."""
    merchant_id = merchant.id
    query = (
        select(PurchaseList)
        .where(
            PurchaseList.merchant_id == merchant_id,
            PurchaseList.status.in_(
                ["draft", "confirmed", "partial_arrival", "accepted", "stored"]
            ),
        )
        .order_by(PurchaseList.created_at.desc())
    )
    result = await db.execute(query)
    plist = result.scalars().first()

    if not plist:
        return {"code": 0, "data": None}

    items_query = (
        select(PurchaseItem).where(PurchaseItem.list_id == plist.id).order_by(PurchaseItem.created_at.asc())
    )
    items_result = await db.execute(items_query)
    items = items_result.scalars().all()

    product_ids = {i.product_id for i in items}
    product_names = {}
    if product_ids:
        prod_query = select(ProductCategory).where(ProductCategory.id.in_(product_ids))
        prod_result = await db.execute(prod_query)
        for p in prod_result.scalars().all():
            product_names[p.id] = p.name

    return {
        "code": 0,
        "data": {
            "list_id": str(plist.id),
            "status": plist.status,
            "payment_status": plist.payment_status,
            "paid_amount": float(plist.paid_amount),
            "total_estimated_cost": float(plist.total_estimated_cost) if plist.total_estimated_cost else 0,
            "total_actual_cost": float(plist.total_actual_cost) if plist.total_actual_cost else None,
            "item_count": plist.item_count,
            "created_at": plist.created_at.isoformat() if plist.created_at else None,
            "confirmed_at": plist.confirmed_at.isoformat() if plist.confirmed_at else None,
            "accepted_at": plist.accepted_at.isoformat() if plist.accepted_at else None,
            "items": [
                {
                    "item_id": str(item.id),
                    "product_id": item.product_id,
                    "product_name": product_names.get(item.product_id, f"商品{item.product_id}"),
                    "recommended_qty": float(item.recommended_qty) if item.recommended_qty else None,
                    "actual_qty": float(item.actual_qty),
                    "unit": item.unit,
                    "estimated_unit_cost": float(item.estimated_unit_cost) if item.estimated_unit_cost else None,
                    "actual_unit_cost": float(item.actual_unit_cost) if item.actual_unit_cost else None,
                    "estimated_cost": float(item.estimated_cost) if item.estimated_cost else None,
                    "actual_cost": float(item.actual_cost) if item.actual_cost else None,
                    "status": item.status,
                    "reason": item.reason,
                    # 验收字段
                    "arrival_qty": float(item.arrival_qty) if item.arrival_qty else None,
                    "accepted_qty": float(item.accepted_qty) if item.accepted_qty else None,
                    "shortage_qty": float(item.shortage_qty) if item.shortage_qty else None,
                    "damaged_qty": float(item.damaged_qty) if item.damaged_qty else None,
                    "rejected_qty": float(item.rejected_qty) if item.rejected_qty else None,
                    "returned_qty": float(item.returned_qty) if item.returned_qty else None,
                    "package_count": item.package_count,
                    "net_weight": float(item.net_weight) if item.net_weight else None,
                    "quality_ok": item.quality_ok,
                    "acceptance_notes": item.acceptance_notes,
                }
                for item in items
            ],
        },
    }


# ---------------------------------------------------------------------------
# 编辑 / 取消采购项
# ---------------------------------------------------------------------------


@router.put("/item/{item_id}", response_model=AnyResponse)
async def update_purchase_item(
    item_id: uuid.UUID,
    merchant: Merchant = Depends(get_current_merchant),
    body: dict = Body(...),
    db: AsyncSession = Depends(get_db),
):
    query = select(PurchaseItem).where(
        PurchaseItem.id == item_id,
        PurchaseItem.merchant_id == merchant.id,
    )
    result = await db.execute(query)
    item = result.scalar_one_or_none()
    if not item:
        raise HTTPException(status_code=404, detail="采购项不存在或无权访问")
    if item.status == "purchased":
        raise HTTPException(status_code=400, detail="已采购的项目不能修改")

    if "actual_qty" in body:
        item.actual_qty = Decimal(str(body["actual_qty"]))
    if "actual_unit_cost" in body:
        item.actual_unit_cost = Decimal(str(body["actual_unit_cost"]))
        item.actual_cost = (item.actual_qty * item.actual_unit_cost).quantize(Decimal("0.01"))
    elif item.actual_unit_cost:
        item.actual_cost = (item.actual_qty * item.actual_unit_cost).quantize(Decimal("0.01"))
    if "supplier_id" in body and body["supplier_id"]:
        item.supplier_id = uuid.UUID(body["supplier_id"])

    if item.recommended_qty and item.recommended_qty > 0:
        item.deviation_ratio = round(
            (item.actual_qty - item.recommended_qty) / item.recommended_qty * Decimal("100"), 1
        )

    await db.commit()
    return {
        "code": 0, "message": "采购项已更新",
        "data": {
            "item_id": str(item.id),
            "actual_qty": float(item.actual_qty),
            "actual_cost": float(item.actual_cost) if item.actual_cost else None,
            "deviation_ratio": float(item.deviation_ratio) if item.deviation_ratio else None,
        },
    }


@router.delete("/item/{item_id}", response_model=AnyResponse)
async def cancel_purchase_item(
    item_id: uuid.UUID,
    merchant: Merchant = Depends(get_current_merchant),
    db: AsyncSession = Depends(get_db),
):
    query = select(PurchaseItem).where(
        PurchaseItem.id == item_id,
        PurchaseItem.merchant_id == merchant.id,
    )
    result = await db.execute(query)
    item = result.scalar_one_or_none()
    if not item:
        raise HTTPException(status_code=404, detail="采购项不存在或无权访问")
    if item.status == "purchased":
        raise HTTPException(status_code=400, detail="已采购的项目不能取消")

    item.status = "cancelled"
    await db.commit()
    return {"code": 0, "message": "采购项已取消"}


# ---------------------------------------------------------------------------
# 到货验收（阶段A 核心新增）
# ---------------------------------------------------------------------------


@router.post("/{list_id}/acceptance", response_model=AnyResponse)
async def record_acceptance(
    list_id: uuid.UUID,
    body: RecordAcceptanceRequest,
    merchant: Merchant = Depends(get_current_merchant),
    db: AsyncSession = Depends(get_db),
):
    """Record arrival acceptance for purchase items.

    Saves package count, weights, quality check, shortage/damage/reject data.
    Does NOT create inventory — call /acceptance/confirm for that.
    """
    list_query = select(PurchaseList).where(PurchaseList.id == list_id)
    list_result = await db.execute(list_query)
    plist = list_result.scalar_one_or_none()
    if plist is None or plist.merchant_id != merchant.id:
        raise HTTPException(status_code=404, detail="采购清单不存在")
    if plist.status not in ("confirmed", "draft", "partial_arrival", "accepted"):
        raise HTTPException(status_code=409, detail=f"当前状态({plist.status})不支持验收")

    items_query = select(PurchaseItem).where(PurchaseItem.list_id == list_id)
    all_items = (await db.execute(items_query)).scalars().all()
    item_map = {item.id: item for item in all_items}

    total_accepted = Decimal("0")
    total_shortage = Decimal("0")
    total_damaged = Decimal("0")
    total_rejected = Decimal("0")
    processed = 0

    for spec in body.items:
        item = item_map.get(spec.item_id)
        if not item:
            raise HTTPException(status_code=404, detail=f"采购项 {spec.item_id} 不存在")
        if item.status == "cancelled":
            continue

        # Save acceptance data
        item.arrival_qty = Decimal(str(spec.arrival_qty))
        item.accepted_qty = Decimal(str(spec.accepted_qty))
        item.shortage_qty = Decimal(str(spec.shortage_qty))
        item.damaged_qty = Decimal(str(spec.damaged_qty))
        item.rejected_qty = Decimal(str(spec.rejected_qty))
        item.returned_qty = Decimal(str(spec.returned_qty))
        item.replenish_qty = Decimal(str(spec.replenish_qty))
        item.package_count = spec.package_count
        if spec.gross_weight is not None:
            item.gross_weight = Decimal(str(spec.gross_weight))
        if spec.tare_weight is not None:
            item.tare_weight = Decimal(str(spec.tare_weight))
        if spec.net_weight is not None:
            item.net_weight = Decimal(str(spec.net_weight))
        if spec.actual_unit_cost is not None and spec.actual_unit_cost > 0:
            item.actual_unit_cost = Decimal(str(spec.actual_unit_cost))
            item.actual_cost = (item.accepted_qty * item.actual_unit_cost).quantize(Decimal("0.01"))
        item.quality_ok = spec.quality_ok
        item.acceptance_photos = spec.acceptance_photos
        item.certificates = spec.certificates
        item.acceptance_notes = spec.acceptance_notes
        item.accepted_at = utc_now()

        total_accepted += item.accepted_qty or Decimal("0")
        total_shortage += item.shortage_qty or Decimal("0")
        total_damaged += item.damaged_qty or Decimal("0")
        total_rejected += item.rejected_qty or Decimal("0")
        processed += 1

    # Transition: confirmed/draft → accepted (all items accepted)
    if plist.status in ("confirmed", "draft"):
        plist.status = "accepted"
    plist.accepted_at = utc_now()
    if body.notes:
        plist.notes = (plist.notes or "") + "\n验收: " + body.notes

    db.add(AuditLog(
        merchant_id=merchant.id,
        action="purchase_acceptance",
        target_table="purchase_lists",
        target_id=str(plist.id),
        after_data={
            "items_processed": processed,
            "total_accepted": float(total_accepted),
            "total_shortage": float(total_shortage),
            "total_damaged": float(total_damaged),
            "total_rejected": float(total_rejected),
        },
        reason=body.notes or "到货验收",
        operator="merchant",
    ))

    await db.commit()

    return {
        "code": 0, "message": f"验收完成，{processed}项",
        "data": {
            "list_id": str(plist.id),
            "status": plist.status,
            "items_processed": processed,
            "total_accepted_qty": float(total_accepted),
            "total_shortage": float(total_shortage),
            "total_damaged": float(total_damaged),
            "total_rejected": float(total_rejected),
        },
    }


@router.post("/{list_id}/acceptance/confirm", response_model=AnyResponse)
async def confirm_acceptance(
    list_id: uuid.UUID,
    body: ConfirmAcceptanceRequest = ConfirmAcceptanceRequest(),
    merchant: Merchant = Depends(get_current_merchant),
    db: AsyncSession = Depends(get_db),
):
    """Confirm acceptance → create batches, inventory records, and supplier payables.

    Only accepted quantities are entered into inventory.
    Idempotent: items with inventory_record_id are skipped.
    State must be 'accepted'.
    """
    list_query = select(PurchaseList).where(PurchaseList.id == list_id)
    list_result = await db.execute(list_query)
    plist = list_result.scalar_one_or_none()
    if plist is None or plist.merchant_id != merchant.id:
        raise HTTPException(status_code=404, detail="采购清单不存在")
    if plist.status != "accepted":
        raise HTTPException(status_code=409, detail=f"必须先完成验收才能入库，当前状态: {plist.status}")

    all_items_query = select(PurchaseItem).where(
        PurchaseItem.list_id == list_id,
        PurchaseItem.status != "cancelled",
    )
    all_items = (await db.execute(all_items_query)).scalars().all()
    if not all_items:
        raise HTTPException(status_code=400, detail="采购清单为空")

    items = [i for i in all_items if not i.inventory_record_id]
    product_ids = {i.product_id for i in items}
    product_map = await _get_product_map(db, product_ids)

    total_actual_cost = Decimal("0")
    confirmed_count = 0
    created_records = []
    now = utc_now()

    for item in items:
        if item.inventory_record_id:
            continue

        product = product_map.get(item.product_id)
        product_name = product.name if product else f"商品{item.product_id}"
        sku_id = await resolve_sku_id(db, plist.merchant_id, product_id=item.product_id)

        # Use accepted_qty if available, otherwise actual_qty (for backward compat)
        qty_to_store = item.accepted_qty or item.actual_qty
        if qty_to_store <= 0:
            continue

        unit_cost = item.actual_unit_cost or item.estimated_unit_cost
        actual_cost = (qty_to_store * (unit_cost or Decimal("0"))).quantize(Decimal("0.01"))

        batch_label = f"{product_name}-{now.strftime('%m%d%H%M%S')}"
        record = InventoryRecord(
            merchant_id=plist.merchant_id,
            product_id=item.product_id,
            sku_id=sku_id,
            quantity=abs(qty_to_store),
            unit=item.unit,
            unit_cost=unit_cost,
            total_amount=actual_cost,
            event_type="purchase",
            event_time=now,
            source="purchase_list",
            batch_label=batch_label,
            notes=f"验收入库: 到货{_fmt_q(item.arrival_qty)} 合格{_fmt_q(item.accepted_qty)}",
        )
        db.add(record)
        await db.flush()

        await create_batch(
            db, merchant_id=plist.merchant_id, product_id=item.product_id,
            product_name=product_name, batch_label=batch_label,
            quantity=abs(qty_to_store), sku_id=sku_id,
        )

        item.inventory_record_id = record.id
        item.status = "purchased"
        item.purchased_at = now

        # Generate supplier payable
        idem_key = f"purchase-accept:{plist.id}:{item.id}"
        await record_supplier_payable_from_purchase(db, plist, item, idempotency_key=idem_key)

        if item.recommendation_id:
            rec = await db.get(Recommendation, item.recommendation_id)
            if rec:
                rec.was_adopted = True
                rec.actual_deviation = float(item.deviation_ratio) if item.deviation_ratio else 0

        total_actual_cost += actual_cost
        confirmed_count += 1
        created_records.append({
            "item_id": str(item.id), "product_id": item.product_id,
            "product_name": product_name, "qty": float(qty_to_store),
            "unit_cost": float(unit_cost) if unit_cost else None,
            "record_id": str(record.id),
        })

    plist.status = "stored"
    plist.total_actual_cost = total_actual_cost.quantize(Decimal("0.01"))
    plist.stored_at = now

    db.add(AuditLog(
        merchant_id=plist.merchant_id,
        action="purchase_accept_confirm",
        target_table="purchase_lists",
        target_id=str(plist.id),
        after_data={"confirmed_count": confirmed_count, "total_cost": float(total_actual_cost)},
        reason=body.notes or "确认验收入库",
        operator="merchant",
    ))
    await db.commit()

    return {
        "code": 0, "message": f"验收确认完成，共入库{confirmed_count}项",
        "data": {
            "list_id": str(plist.id),
            "status": plist.status,
            "confirmed_count": confirmed_count,
            "total_actual_cost": float(total_actual_cost),
            "records": created_records,
        },
    }


# ---------------------------------------------------------------------------
# 供应商付款
# ---------------------------------------------------------------------------


@router.post("/supplier-payment", response_model=AnyResponse)
async def pay_supplier(
    body: SupplierPaymentRequest,
    merchant: Merchant = Depends(get_current_merchant),
    db: AsyncSession = Depends(get_db),
):
    """Record a payment to a supplier."""
    supplier = await db.get(Supplier, body.supplier_id)
    if not supplier or supplier.merchant_id != merchant.id:
        raise HTTPException(status_code=404, detail="供应商不存在")

    payment = await record_supplier_payment(
        db,
        merchant_id=merchant.id,
        supplier_id=body.supplier_id,
        amount=Decimal(str(body.amount)),
        note=body.note or f"{body.method}付款",
        idempotency_key=body.idempotency_key,
    )

    db.add(AuditLog(
        merchant_id=merchant.id, action="supplier_payment",
        target_table="supplier_payables", target_id=str(payment.id),
        after_data={"supplier_id": str(body.supplier_id), "amount": body.amount, "method": body.method},
        reason=body.note, operator="merchant",
    ))
    await db.commit()

    new_balance = await get_supplier_balance(db, merchant.id, body.supplier_id)

    return {
        "code": 0, "message": f"已向供应商付款 ¥{body.amount}",
        "data": {
            "payment_id": str(payment.id),
            "supplier_id": str(body.supplier_id),
            "amount": body.amount,
            "method": body.method,
            "new_balance": float(new_balance),
        },
    }


# ---------------------------------------------------------------------------
# 供应商对账单
# ---------------------------------------------------------------------------


@router.get("/supplier/{supplier_id}/statement", response_model=AnyResponse)
async def supplier_statement(
    supplier_id: uuid.UUID,
    limit: int = 50,
    merchant: Merchant = Depends(get_current_merchant),
    db: AsyncSession = Depends(get_db),
):
    """Get supplier statement (ledger of all transactions)."""
    supplier = await db.get(Supplier, supplier_id)
    if not supplier or supplier.merchant_id != merchant.id:
        raise HTTPException(status_code=404, detail="供应商不存在")

    statement = await get_supplier_statement(db, merchant.id, supplier_id, limit=limit)
    return {"code": 0, "data": statement}


# ---------------------------------------------------------------------------
# 采购退货
# ---------------------------------------------------------------------------


@router.post("/items/{item_id}/return", response_model=AnyResponse)
async def return_purchase_item(
    item_id: uuid.UUID,
    body: PurchaseReturnRequest,
    merchant: Merchant = Depends(get_current_merchant),
    db: AsyncSession = Depends(get_db),
):
    """Return purchased goods to supplier, optionally offset payable."""
    item = await db.scalar(
        select(PurchaseItem).where(
            PurchaseItem.id == item_id,
            PurchaseItem.merchant_id == merchant.id,
        )
    )
    if not item:
        raise HTTPException(status_code=404, detail="采购项不存在")
    if item.status != "purchased":
        raise HTTPException(status_code=409, detail=f"只能退已采购的项目，当前状态: {item.status}")

    return_qty = Decimal(str(body.return_qty))
    available = (item.accepted_qty or item.actual_qty) - (item.returned_qty or Decimal("0"))
    if return_qty > available:
        raise HTTPException(status_code=400, detail=f"退货量{return_qty}超过可退量{available}")

    unit_cost = item.actual_unit_cost or item.estimated_unit_cost or Decimal("0")
    return_amount = (return_qty * unit_cost).quantize(Decimal("0.01"))

    # Reverse inventory
    db.add(InventoryRecord(
        merchant_id=merchant.id,
        product_id=item.product_id,
        sku_id=item.sku_id,
        quantity=-return_qty,  # negative = stock decrease
        unit=item.unit,
        unit_cost=unit_cost,
        total_amount=return_amount,
        event_type="purchase_return",
        event_time=utc_now(),
        source="purchase_list",
        notes=f"退货给供应商: {body.reason}",
        idempotency_key=f"purchase-return:{item.id}:{return_qty}",
    ))

    # Offset payable if requested
    if body.offset_payable and item.supplier_id:
        db.add(SupplierPayable(
            merchant_id=merchant.id,
            supplier_id=item.supplier_id,
            direction="payment",  # 退货 = 减少应付，等同于付款方向
            amount=return_amount,
            purchase_list_id=item.list_id,
            note=f"退货抵扣: {body.reason}",
            settled=True,
            idempotency_key=f"purchase-return-payable:{item.id}",
        ))

    item.returned_qty = (item.returned_qty or Decimal("0")) + return_qty
    remaining = (item.accepted_qty or item.actual_qty) - (item.returned_qty or Decimal("0"))
    if remaining <= 0:
        item.status = "returned"

    db.add(AuditLog(
        merchant_id=merchant.id, action="purchase_return",
        target_table="purchase_items", target_id=str(item.id),
        after_data={
            "return_qty": float(return_qty), "reason": body.reason,
            "return_amount": float(return_amount), "offset_payable": body.offset_payable,
        },
        reason=body.reason, operator="merchant",
    ))
    await db.commit()

    return {
        "code": 0, "message": f"已退货 {float(return_qty)}{item.unit}，抵扣 ¥{float(return_amount)}",
        "data": {
            "item_id": str(item.id),
            "return_qty": float(return_qty),
            "reason": body.reason,
            "offset_payable": body.offset_payable,
            "new_item_status": item.status,
        },
    }


# ---------------------------------------------------------------------------
# 取消采购单
# ---------------------------------------------------------------------------


@router.post("/{list_id}/cancel", response_model=AnyResponse)
async def cancel_purchase_list(
    list_id: uuid.UUID,
    merchant: Merchant = Depends(get_current_merchant),
    db: AsyncSession = Depends(get_db),
):
    query = select(PurchaseList).where(PurchaseList.id == list_id)
    result = await db.execute(query)
    plist = result.scalar_one_or_none()
    if plist is None or plist.merchant_id != merchant.id:
        raise HTTPException(status_code=404, detail="采购清单不存在")
    if plist.status in ("stored", "completed"):
        raise HTTPException(status_code=400, detail="已入库的清单不能取消")

    plist.status = "cancelled"
    items_query = select(PurchaseItem).where(
        PurchaseItem.list_id == list_id,
        PurchaseItem.status == "pending",
    )
    items_result = await db.execute(items_query)
    for item in items_result.scalars().all():
        item.status = "cancelled"

    await db.commit()
    return {"code": 0, "message": "采购清单已取消"}


# ---------------------------------------------------------------------------
# Legacy: 直接确认采购（跳过验收，兼容旧流程）
# ---------------------------------------------------------------------------


@router.post("/{list_id}/confirm", response_model=AnyResponse)
async def confirm_purchase(
    list_id: uuid.UUID,
    body: dict = Body(default={}),
    merchant: Merchant = Depends(get_current_merchant),
    db: AsyncSession = Depends(get_db),
):
    """Legacy: direct confirm without acceptance step. For new flow use /acceptance + /acceptance/confirm."""
    list_query = select(PurchaseList).where(PurchaseList.id == list_id)
    list_result = await db.execute(list_query)
    plist = list_result.scalar_one_or_none()
    if plist is None or plist.merchant_id != merchant.id:
        raise HTTPException(status_code=404, detail="采购清单不存在")

    all_items_query = select(PurchaseItem).where(PurchaseItem.list_id == list_id)
    all_items = (await db.execute(all_items_query)).scalars().all()
    if not all_items:
        raise HTTPException(status_code=400, detail="采购清单为空，无可确认项目")

    items = [i for i in all_items if i.status == "pending" and not i.inventory_record_id]
    product_ids = {i.product_id for i in items}
    product_map = await _get_product_map(db, product_ids)

    total_actual_cost = Decimal("0")
    confirmed_count = 0
    created_records = []
    now = utc_now()

    for item in items:
        if item.inventory_record_id:
            continue

        product = product_map.get(item.product_id)
        product_name = product.name if product else f"商品{item.product_id}"
        sku_id = await resolve_sku_id(db, plist.merchant_id, product_id=item.product_id)

        batch_label = f"{product_name}-{now.strftime('%m%d%H%M%S')}"
        record = InventoryRecord(
            merchant_id=plist.merchant_id, product_id=item.product_id, sku_id=sku_id,
            quantity=abs(item.actual_qty), unit=item.unit,
            unit_cost=item.actual_unit_cost or item.estimated_unit_cost,
            total_amount=item.actual_cost or item.estimated_cost,
            event_type="purchase", event_time=now, source="purchase_list",
            batch_label=batch_label,
        )
        db.add(record)
        await db.flush()

        await create_batch(
            db, merchant_id=plist.merchant_id, product_id=item.product_id,
            product_name=product_name, batch_label=batch_label,
            quantity=abs(item.actual_qty), sku_id=sku_id,
        )

        item.inventory_record_id = record.id
        item.status = "purchased"
        item.purchased_at = now

        idem_key = f"purchase:{plist.id}:{item.id}"
        await record_supplier_payable_from_purchase(db, plist, item, idempotency_key=idem_key)

        if item.recommendation_id:
            rec = await db.get(Recommendation, item.recommendation_id)
            if rec:
                rec.was_adopted = True
                rec.actual_deviation = float(item.deviation_ratio) if item.deviation_ratio else 0

        total_actual_cost += item.actual_cost or item.estimated_cost or Decimal("0")
        confirmed_count += 1
        created_records.append({
            "item_id": str(item.id), "product_id": item.product_id,
            "product_name": product_name, "qty": float(item.actual_qty),
            "record_id": str(record.id),
        })

    plist.status = "stored"
    plist.total_actual_cost = total_actual_cost.quantize(Decimal("0.01"))
    plist.stored_at = now

    db.add(AuditLog(
        merchant_id=plist.merchant_id, action="purchase_confirm",
        target_table="purchase_lists", target_id=str(plist.id),
        after_data={"confirmed_count": confirmed_count, "total_cost": float(total_actual_cost)},
        reason=body.get("notes", "批量确认采购"), operator="merchant",
    ))
    await db.commit()

    return {
        "code": 0, "message": f"采购完成，共入库{confirmed_count}项",
        "data": {
            "list_id": str(plist.id), "status": plist.status,
            "confirmed_count": confirmed_count,
            "total_actual_cost": float(total_actual_cost),
            "records": created_records,
        },
    }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _fmt_q(value) -> str:
    if value is None:
        return "?"
    return str(float(value))
