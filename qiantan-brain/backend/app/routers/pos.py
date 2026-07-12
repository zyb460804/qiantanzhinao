"""POS sale, payment, refund, hold, and daily reconciliation APIs.

P0 新增（2026-07-12）:
- 组合支付：一笔订单多个支付方式
- 退款/退货：整单退款 + 单品退款，反向流水，可选退货入库
- 挂单/取单：挂起订单 → 取回继续 → 取消
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, time
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import case, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import get_current_merchant
from app.core.timezone import utc_now
from app.database import get_db
from app.models.audit import AuditLog
from app.models.catalog import ProductSKU
from app.models.inventory import InventoryRecord
from app.models.merchant import Merchant
from app.models.pos import DailySettlement, Payment, Reconciliation, SaleOrder, SaleOrderItem
from app.models.product import ProductCategory
from app.schemas.common import AnyResponse
from app.schemas.pos import (
    CreateSaleOrderRequest,
    HoldOrderRequest,
    PaySaleOrderRequest,
    RefundOrderRequest,
)
from app.services.accounts_service import record_customer_receivable
from app.services.batch import consume_batches_fifo, return_to_batches
from app.services.sku_service import resolve_sku_id


router = APIRouter(prefix="/api/v1/pos", tags=["pos"])


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _generate_order_no() -> str:
    now = utc_now()
    return f"POS{now.strftime('%Y%m%d%H%M%S')}{now.microsecond // 1000:03d}"


def _order_data(order: SaleOrder, *, duplicate: bool = False) -> dict:
    return {
        "order_id": str(order.id),
        "order_no": order.order_no,
        "total_amount": float(order.total_amount),
        "paid_amount": float(order.paid_amount or 0),
        "refunded_amount": float(order.refunded_amount or 0),
        "discount_amount": float(order.discount_amount or 0),
        "status": order.status,
        "customer_name": order.customer_name,
        "duplicate": duplicate,
    }


async def _resolve_product_map(
    db: AsyncSession, product_ids: set[int]
) -> dict[int, ProductCategory]:
    if not product_ids:
        return {}
    products = (
        (await db.execute(select(ProductCategory).where(ProductCategory.id.in_(product_ids))))
        .scalars()
        .all()
    )
    return {p.id: p for p in products}


async def _resolve_sku_map(
    db: AsyncSession, merchant_id: uuid.UUID, sku_ids: set[uuid.UUID]
) -> dict[uuid.UUID, ProductSKU]:
    if not sku_ids:
        return {}
    skus = (
        (
            await db.execute(
                select(ProductSKU).where(
                    ProductSKU.merchant_id == merchant_id,
                    ProductSKU.id.in_(sku_ids),
                    ProductSKU.is_active == True,  # noqa: E712
                )
            )
        )
        .scalars()
        .all()
    )
    return {sku.id: sku for sku in skus}


def _resolve_unit_price(
    request_price: float | None, sku: ProductSKU | None, product_name: str
) -> Decimal:
    if request_price is not None:
        return Decimal(str(request_price)).quantize(Decimal("0.01"))
    if sku and sku.default_sale_price is not None:
        return Decimal(sku.default_sale_price).quantize(Decimal("0.01"))
    raise HTTPException(status_code=400, detail=f"{product_name}尚未设置售价")


async def _create_order_items_and_consume(
    db: AsyncSession,
    order: SaleOrder,
    merchant_id: uuid.UUID,
    items: list,
    product_map: dict[int, ProductCategory],
    sku_map: dict[uuid.UUID, ProductSKU],
) -> tuple[Decimal, list[SaleOrderItem]]:
    """Create order items, consume FIFO batches, write inventory records.

    Returns (gross_total, created_items).
    """
    gross_total = Decimal("0")
    created: list[SaleOrderItem] = []

    for request_item in items:
        product = product_map[request_item.product_id]
        sku_id = request_item.sku_id
        sku = sku_map.get(sku_id) if sku_id else None

        if sku_id is None:
            sku_id = await resolve_sku_id(db, merchant_id, product_id=request_item.product_id)
            if sku_id:
                sku = await db.get(ProductSKU, sku_id)

        quantity = Decimal(str(request_item.quantity)).quantize(Decimal("0.01"))
        unit_price = _resolve_unit_price(request_item.unit_price, sku, product.name)
        line_total = (quantity * unit_price).quantize(Decimal("0.01"))

        consumed = await consume_batches_fifo(
            db, merchant_id, request_item.product_id, quantity, sku_id=sku_id,
        )
        if consumed < quantity:
            raise HTTPException(
                status_code=409,
                detail=f"{product.name}库存不足，需要{quantity}{request_item.unit}，可售{consumed}{request_item.unit}",
            )

        order_item = SaleOrderItem(
            id=uuid.uuid4(),
            order_id=order.id,
            merchant_id=merchant_id,
            sku_id=sku_id,
            product_id=request_item.product_id,
            quantity=quantity,
            unit=request_item.unit,
            unit_price=unit_price,
            total_amount=line_total,
        )
        db.add(order_item)
        db.add(
            InventoryRecord(
                merchant_id=merchant_id,
                product_id=request_item.product_id,
                sku_id=sku_id,
                quantity=-quantity,
                unit=request_item.unit,
                unit_price=unit_price,
                total_amount=line_total,
                event_type="sale",
                event_time=utc_now(),
                source="pos",
                notes=f"订单 {order.order_no}",
                idempotency_key=f"sale:{order.id}:{order_item.id}",
                client_id=order.client_id,
                client_reference=order.order_no,
            )
        )
        gross_total += line_total
        created.append(order_item)

    return gross_total, created


async def _apply_payments(
    db: AsyncSession,
    order: SaleOrder,
    merchant_id: uuid.UUID,
    payable: Decimal,
    payment_method: str | None,
    payments: list | None,
    customer_name: str | None,
) -> None:
    """Apply single or combined payments to an order."""
    now = utc_now()

    if payments:
        # 组合支付
        total_paid = Decimal("0")
        for p in payments:
            amt = Decimal(str(p.amount)).quantize(Decimal("0.01"))
            total_paid += amt
            payment = Payment(
                merchant_id=merchant_id,
                order_id=order.id,
                amount=amt,
                method=p.method,
                status="success",
                note=f"订单 {order.order_no} 组合支付",
            )
            db.add(payment)
            if p.method == "credit":
                await record_customer_receivable(
                    db,
                    merchant_id=merchant_id,
                    customer_name=customer_name or "",
                    amount=amt,
                    direction="charge",
                    sale_order_id=order.id,
                    note=f"订单 {order.order_no} 赊账（组合支付）",
                    idempotency_key=f"sale-credit:{order.id}:{p.method}",
                )
        if abs(total_paid - payable) > Decimal("0.01"):
            raise HTTPException(
                status_code=400,
                detail=f"支付金额合计 {total_paid} 与应收 {payable} 不匹配",
            )
        order.paid_amount = total_paid
        order.status = "paid"
        order.paid_at = now
    elif payment_method == "credit":
        order.status = "credit"
        await record_customer_receivable(
            db,
            merchant_id=merchant_id,
            customer_name=customer_name or "",
            amount=payable,
            direction="charge",
            sale_order_id=order.id,
            note=f"订单 {order.order_no} 赊账",
            idempotency_key=f"sale-credit:{order.id}",
        )
    else:
        order.status = "paid"
        order.paid_amount = payable
        order.paid_at = now
        db.add(
            Payment(
                merchant_id=merchant_id,
                order_id=order.id,
                amount=payable,
                method=payment_method or "cash",
                status="success",
                note=f"订单 {order.order_no} 支付",
            )
        )


# ---------------------------------------------------------------------------
# 创建订单（含组合支付）
# ---------------------------------------------------------------------------


@router.post("/orders", response_model=AnyResponse)
async def create_sale_order(
    body: CreateSaleOrderRequest,
    merchant: Merchant = Depends(get_current_merchant),
    db: AsyncSession = Depends(get_db),
):
    """Create an idempotent POS order with single or combined payment."""
    await _check_settlement_locked(db, merchant.id)
    if body.client_id:
        existing = await db.scalar(
            select(SaleOrder).where(
                SaleOrder.merchant_id == merchant.id,
                SaleOrder.client_id == body.client_id,
            )
        )
        if existing:
            return {"code": 0, "data": _order_data(existing, duplicate=True)}

    product_ids = {item.product_id for item in body.items}
    product_map = await _resolve_product_map(db, product_ids)
    missing = product_ids - set(product_map)
    if missing:
        raise HTTPException(status_code=400, detail=f"商品不存在: {sorted(missing)}")

    supplied_sku_ids = {item.sku_id for item in body.items if item.sku_id}
    sku_map = await _resolve_sku_map(db, merchant.id, supplied_sku_ids)
    if supplied_sku_ids - set(sku_map):
        raise HTTPException(status_code=400, detail="SKU不存在、已停用或不属于当前商户")

    order = SaleOrder(
        merchant_id=merchant.id,
        order_no=_generate_order_no(),
        status="pending",
        client_id=body.client_id,
        customer_name=(body.customer_name or "").strip() or None,
        discount_amount=Decimal(str(body.discount_amount)).quantize(Decimal("0.01")),
        note=body.note,
    )
    db.add(order)
    await db.flush()

    gross_total, _ = await _create_order_items_and_consume(
        db, order, merchant.id, body.items, product_map, sku_map,
    )

    if order.discount_amount > gross_total:
        raise HTTPException(status_code=400, detail="优惠金额不能大于商品总额")
    order.total_amount = (gross_total - order.discount_amount).quantize(Decimal("0.01"))

    await _apply_payments(
        db, order, merchant.id, order.total_amount,
        payment_method=body.payment_method if not body.payments else None,
        payments=[
            type("P", (), {"method": p.method, "amount": p.amount})()
            for p in body.payments
        ] if body.payments else None,
        customer_name=body.customer_name,
    )

    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        if body.client_id:
            existing = await db.scalar(
                select(SaleOrder).where(
                    SaleOrder.merchant_id == merchant.id,
                    SaleOrder.client_id == body.client_id,
                )
            )
            if existing:
                return {"code": 0, "data": _order_data(existing, duplicate=True)}
        raise
    await db.refresh(order)
    return {"code": 0, "data": _order_data(order)}


# ---------------------------------------------------------------------------
# 订单列表 / 收款
# ---------------------------------------------------------------------------


@router.get("/orders", response_model=AnyResponse)
async def list_sale_orders(
    page: int = 1,
    limit: int = 20,
    status: str | None = None,
    merchant: Merchant = Depends(get_current_merchant),
    db: AsyncSession = Depends(get_db),
):
    offset = (page - 1) * limit
    filters = [SaleOrder.merchant_id == merchant.id]
    if status:
        filters.append(SaleOrder.status == status)
    orders = (
        (
            await db.execute(
                select(SaleOrder)
                .where(*filters)
                .order_by(SaleOrder.created_at.desc())
                .offset(offset)
                .limit(min(limit, 100))
            )
        )
        .scalars()
        .all()
    )
    return {
        "code": 0,
        "data": [
            {
                **_order_data(order),
                "created_at": order.created_at.isoformat() if order.created_at else None,
            }
            for order in orders
        ],
        "meta": {"page": page, "limit": min(limit, 100)},
    }


@router.get("/orders/held", response_model=AnyResponse)
async def list_held_orders(
    merchant: Merchant = Depends(get_current_merchant),
    db: AsyncSession = Depends(get_db),
):
    """List all currently held (parked) orders for this merchant."""
    orders = (
        (await db.execute(
            select(SaleOrder)
            .where(
                SaleOrder.merchant_id == merchant.id,
                SaleOrder.status == "held",
            )
            .order_by(SaleOrder.held_at.desc())
        ))
        .scalars()
        .all()
    )

    result = []
    for order in orders:
        item_count_result = await db.scalar(
            select(func.count(SaleOrderItem.id)).where(
                SaleOrderItem.order_id == order.id
            )
        )
        result.append({
            "order_id": str(order.id),
            "order_no": order.order_no,
            "item_count": int(item_count_result or 0),
            "total_amount": float(order.total_amount),
            "customer_name": order.customer_name,
            "held_at": order.held_at.isoformat() if order.held_at else None,
        })

    return {"code": 0, "data": result}


@router.get("/orders/{order_id}", response_model=AnyResponse)
async def get_sale_order(
    order_id: uuid.UUID,
    merchant: Merchant = Depends(get_current_merchant),
    db: AsyncSession = Depends(get_db),
):
    """Get a single order with its items and payments."""
    order = await db.scalar(
        select(SaleOrder).where(
            SaleOrder.id == order_id,
            SaleOrder.merchant_id == merchant.id,
        )
    )
    if not order:
        raise HTTPException(status_code=404, detail="订单不存在")

    items = (
        (await db.execute(
            select(SaleOrderItem).where(SaleOrderItem.order_id == order.id)
        ))
        .scalars()
        .all()
    )
    payments = (
        (await db.execute(
            select(Payment).where(Payment.order_id == order.id)
        ))
        .scalars()
        .all()
    )

    product_ids = {i.product_id for i in items}
    product_map = await _resolve_product_map(db, product_ids)

    return {
        "code": 0,
        "data": {
            **_order_data(order),
            "created_at": order.created_at.isoformat() if order.created_at else None,
            "held_at": order.held_at.isoformat() if order.held_at else None,
            "refunded_at": order.refunded_at.isoformat() if order.refunded_at else None,
            "refund_reason": order.refund_reason,
            "note": order.note,
            "items": [
                {
                    "item_id": str(item.id),
                    "product_id": item.product_id,
                    "product_name": (product_map.get(item.product_id)).name
                    if product_map.get(item.product_id) else f"商品{item.product_id}",
                    "quantity": float(item.quantity),
                    "refund_quantity": float(item.refund_quantity or 0),
                    "unit": item.unit,
                    "unit_price": float(item.unit_price) if item.unit_price else None,
                    "total_amount": float(item.total_amount) if item.total_amount else None,
                    "return_to_stock": item.return_to_stock,
                }
                for item in items
            ],
            "payments": [
                {
                    "payment_id": str(p.id),
                    "amount": float(p.amount),
                    "method": p.method,
                    "status": p.status,
                    "created_at": p.created_at.isoformat() if p.created_at else None,
                }
                for p in payments
            ],
        },
    }


@router.post("/orders/{order_id}/pay", response_model=AnyResponse)
async def pay_sale_order(
    order_id: uuid.UUID,
    body: PaySaleOrderRequest,
    merchant: Merchant = Depends(get_current_merchant),
    db: AsyncSession = Depends(get_db),
):
    order = await db.scalar(
        select(SaleOrder).where(
            SaleOrder.id == order_id,
            SaleOrder.merchant_id == merchant.id,
        )
    )
    if not order:
        raise HTTPException(status_code=404, detail="订单不存在")
    if order.status in {"cancelled", "refunded", "partial_refund"}:
        raise HTTPException(status_code=409, detail="当前订单状态不可收款")

    if body.transaction_id:
        existing_payment = await db.scalar(
            select(Payment).where(
                Payment.merchant_id == merchant.id,
                Payment.transaction_id == body.transaction_id,
            )
        )
        if existing_payment:
            return {
                "code": 0,
                "data": {
                    "payment_id": str(existing_payment.id),
                    "order_id": str(order.id),
                    "paid_amount": float(order.paid_amount or 0),
                    "status": order.status,
                    "duplicate": True,
                },
            }

    amount = Decimal(str(body.amount)).quantize(Decimal("0.01"))
    remaining = (order.total_amount - (order.paid_amount or Decimal("0"))).quantize(Decimal("0.01"))
    if remaining <= 0:
        raise HTTPException(status_code=409, detail="订单已付清")
    if amount > remaining:
        raise HTTPException(status_code=400, detail=f"支付金额超过待收金额 {remaining}")

    previous_status = order.status
    payment = Payment(
        merchant_id=merchant.id,
        order_id=order.id,
        amount=amount,
        method=body.method,
        status="success",
        transaction_id=body.transaction_id,
        note=body.note,
    )
    db.add(payment)
    await db.flush()

    order.paid_amount = (order.paid_amount or Decimal("0")) + amount
    if order.paid_amount >= order.total_amount:
        order.status = "paid"
        order.paid_at = utc_now()
    else:
        order.status = "partial"

    if order.customer_name and previous_status in {"credit", "partial"}:
        await record_customer_receivable(
            db,
            merchant_id=merchant.id,
            customer_name=order.customer_name,
            amount=amount,
            direction="repay",
            sale_order_id=order.id,
            note=body.note or f"订单 {order.order_no} 回款",
            idempotency_key=f"sale-repay:{payment.id}",
        )

    await db.commit()
    return {
        "code": 0,
        "data": {
            "payment_id": str(payment.id),
            "order_id": str(order.id),
            "paid_amount": float(order.paid_amount),
            "remaining_amount": float(order.total_amount - order.paid_amount),
            "status": order.status,
            "duplicate": False,
        },
    }


# ---------------------------------------------------------------------------
# 退款 / 退货（P0）
# ---------------------------------------------------------------------------


async def _refund_single_item(
    db: AsyncSession,
    order: SaleOrder,
    item: SaleOrderItem,
    refund_qty: Decimal,
    return_to_stock: bool,
    reason: str,
    merchant_id: uuid.UUID,
    product_name: str,
) -> dict:
    """Refund one line item: reverse inventory, optionally restock batch, write audit."""
    unit_price = item.unit_price or Decimal("0")
    refund_amount = (refund_qty * unit_price).quantize(Decimal("0.01"))

    # Record refunded quantity on the item
    item.refund_quantity = (item.refund_quantity or Decimal("0")) + refund_qty
    item.return_to_stock = return_to_stock

    # Reverse inventory: positive quantity = stock returned
    inv_record = InventoryRecord(
        merchant_id=merchant_id,
        product_id=item.product_id,
        sku_id=item.sku_id,
        quantity=refund_qty,  # positive = stock increase
        unit=item.unit,
        unit_price=unit_price,
        total_amount=refund_amount,
        event_type="refund",
        event_time=utc_now(),
        source="pos",
        notes=f"退款退货 订单 {order.order_no}: {reason}",
        idempotency_key=f"refund:{order.id}:{item.id}",
        client_id=order.client_id,
        client_reference=order.order_no,
    )
    db.add(inv_record)

    # If returning to sellable stock, add back to batches
    if return_to_stock:
        await return_to_batches(
            db, merchant_id, item.product_id, refund_qty, sku_id=item.sku_id,
        )

    return {
        "item_id": str(item.id),
        "product_name": product_name,
        "original_qty": float(item.quantity),
        "refund_qty": float(refund_qty),
        "refund_amount": float(refund_amount),
        "returned_to_stock": return_to_stock,
    }


@router.post("/orders/{order_id}/refund", response_model=AnyResponse)
async def refund_order(
    order_id: uuid.UUID,
    body: RefundOrderRequest,
    merchant: Merchant = Depends(get_current_merchant),
    db: AsyncSession = Depends(get_db),
):
    """Refund an entire order or specific items. Generates reverse ledger entries."""
    order = await db.scalar(
        select(SaleOrder).where(
            SaleOrder.id == order_id,
            SaleOrder.merchant_id == merchant.id,
        )
    )
    if not order:
        raise HTTPException(status_code=404, detail="订单不存在")
    if order.status in {"cancelled", "refunded", "pending", "held"}:
        raise HTTPException(status_code=409, detail=f"当前订单状态({order.status})不可退款")

    # Fetch all items
    items = (
        (await db.execute(
            select(SaleOrderItem).where(SaleOrderItem.order_id == order.id)
        ))
        .scalars()
        .all()
    )
    item_map = {item.id: item for item in items}

    product_ids = {i.product_id for i in items}
    product_map = await _resolve_product_map(db, product_ids)

    results: list[dict] = []
    total_refund = Decimal("0")

    if body.items:
        # Partial refund: refund specified items
        refund_spec = {(uuid.UUID(r.item_id) if isinstance(r.item_id, str) else r.item_id): r
                       for r in body.items}
        for item_id, spec in refund_spec.items():
            item = item_map.get(item_id)
            if not item:
                raise HTTPException(status_code=404, detail=f"订单行项目 {item_id} 不存在")
            refund_qty = Decimal(str(spec.quantity)).quantize(Decimal("0.01"))
            already_refunded = item.refund_quantity or Decimal("0")
            available = item.quantity - already_refunded
            if refund_qty > available:
                raise HTTPException(
                    status_code=400,
                    detail=f"退款数量{refund_qty}超过可退数量{available}",
                )
            product = product_map.get(item.product_id)
            product_name = product.name if product else f"商品{item.product_id}"
            result = await _refund_single_item(
                db, order, item, refund_qty, spec.return_to_stock,
                body.reason, merchant.id, product_name,
            )
            results.append(result)
            total_refund += Decimal(str(result["refund_amount"])).quantize(Decimal("0.01"))

        # Determine new status: check if ALL items are fully refunded
        all_items_refunded = all(
            (item.refund_quantity or Decimal("0")) >= item.quantity
            for item in items
        )
        if all_items_refunded:
            order.status = "refunded"
        else:
            order.status = "partial_refund"
    else:
        # Full refund
        for item in items:
            remaining = item.quantity - (item.refund_quantity or Decimal("0"))
            if remaining <= 0:
                continue
            product = product_map.get(item.product_id)
            product_name = product.name if product else f"商品{item.product_id}"
            result = await _refund_single_item(
                db, order, item, remaining, body.return_to_stock,
                body.reason, merchant.id, product_name,
            )
            results.append(result)
            total_refund += Decimal(str(result["refund_amount"])).quantize(Decimal("0.01"))
        order.status = "refunded"

    order.refunded_amount = (order.refunded_amount or Decimal("0")) + total_refund
    order.refund_reason = body.reason
    order.refunded_at = utc_now()

    # Create reverse payment records
    refund_methods: dict[str, Decimal] = {}
    payments = (
        (await db.execute(
            select(Payment).where(
                Payment.order_id == order.id,
                Payment.status == "success",
            )
        ))
        .scalars()
        .all()
    )
    for p in payments:
        refund_methods[p.method] = refund_methods.get(p.method, Decimal("0")) + p.amount

    # Refund proportionally across original payment methods
    if refund_methods:
        for method, original_amt in refund_methods.items():
            # Scale: refund same proportion from each method
            if total_refund <= 0:
                break
            amt = min(original_amt, total_refund)
            db.add(Payment(
                merchant_id=merchant.id,
                order_id=order.id,
                amount=-amt,  # negative = refund
                method=method,
                status="refunded",
                note=f"退款 订单 {order.order_no}: {body.reason}",
            ))
            # If refund is credit, reduce receivable
            if method == "credit":
                await record_customer_receivable(
                    db,
                    merchant_id=merchant.id,
                    customer_name=order.customer_name or "",
                    amount=amt,
                    direction="repay",
                    sale_order_id=order.id,
                    note=f"退款 订单 {order.order_no}: {body.reason}",
                    idempotency_key=f"sale-refund:{order.id}:{method}",
                )
            total_refund -= amt

    # Audit
    db.add(AuditLog(
        merchant_id=merchant.id,
        action="pos_refund",
        target_table="sale_orders",
        target_id=str(order.id),
        after_data={
            "refund_reason": body.reason,
            "refund_amount": float(order.refunded_amount),
            "new_status": order.status,
            "items": results,
        },
        reason=body.reason,
        operator="merchant",
    ))

    await db.commit()
    await db.refresh(order)

    return {
        "code": 0,
        "message": f"退款完成，共退{len(results)}项，合计¥{float(order.refunded_amount)}",
        "data": {
            "order_id": str(order.id),
            "order_no": order.order_no,
            "refunded_amount": float(order.refunded_amount),
            "remaining_amount": float(order.total_amount - (order.refunded_amount or 0)),
            "new_status": order.status,
            "items": results,
        },
    }


# ---------------------------------------------------------------------------
# 挂单 / 取单（P0）
# ---------------------------------------------------------------------------


@router.post("/orders/hold", response_model=AnyResponse)
async def hold_order(
    body: HoldOrderRequest,
    merchant: Merchant = Depends(get_current_merchant),
    db: AsyncSession = Depends(get_db),
):
    """Hold (park) an order for later checkout. No payment or inventory deduction yet."""
    order = SaleOrder(
        merchant_id=merchant.id,
        order_no=_generate_order_no(),
        status="held",
        client_id=body.client_id,
        customer_name=(body.customer_name or "").strip() or None,
        discount_amount=Decimal(str(body.discount_amount)).quantize(Decimal("0.01")),
        note=body.note,
        held_at=utc_now(),
    )
    db.add(order)
    await db.flush()

    product_ids = {item.product_id for item in body.items}
    product_map = await _resolve_product_map(db, product_ids)
    missing = product_ids - set(product_map)
    if missing:
        raise HTTPException(status_code=400, detail=f"商品不存在: {sorted(missing)}")

    supplied_sku_ids = {item.sku_id for item in body.items if item.sku_id}
    sku_map = await _resolve_sku_map(db, merchant.id, supplied_sku_ids)

    gross_total = Decimal("0")
    for request_item in body.items:
        product = product_map[request_item.product_id]
        sku_id = request_item.sku_id
        sku = sku_map.get(sku_id) if sku_id else None
        if sku_id is None:
            sku_id = await resolve_sku_id(db, merchant.id, product_id=request_item.product_id)
            if sku_id:
                sku = await db.get(ProductSKU, sku_id)

        quantity = Decimal(str(request_item.quantity)).quantize(Decimal("0.01"))
        unit_price = _resolve_unit_price(request_item.unit_price, sku, product.name)
        line_total = (quantity * unit_price).quantize(Decimal("0.01"))

        order_item = SaleOrderItem(
            id=uuid.uuid4(),
            order_id=order.id,
            merchant_id=merchant.id,
            sku_id=sku_id,
            product_id=request_item.product_id,
            quantity=quantity,
            unit=request_item.unit,
            unit_price=unit_price,
            total_amount=line_total,
        )
        db.add(order_item)
        gross_total += line_total

    if order.discount_amount > gross_total:
        raise HTTPException(status_code=400, detail="优惠金额不能大于商品总额")
    order.total_amount = (gross_total - order.discount_amount).quantize(Decimal("0.01"))

    await db.commit()
    await db.refresh(order)

    return {
        "code": 0,
        "message": "订单已挂起",
        "data": {
            "order_id": str(order.id),
            "order_no": order.order_no,
            "status": order.status,
            "total_amount": float(order.total_amount),
            "held_at": order.held_at.isoformat() if order.held_at else None,
        },
    }


@router.post("/orders/{order_id}/resume", response_model=AnyResponse)
async def resume_held_order(
    order_id: uuid.UUID,
    body: dict | None = None,
    merchant: Merchant = Depends(get_current_merchant),
    db: AsyncSession = Depends(get_db),
):
    """Resume a held order: deduct inventory, process payment, finalize.

    Body may contain:
      - payment_method: str (single payment)
      - payments: list[{method, amount}] (combined payment)
      - customer_name: str (for credit)
      - discount_amount: float (updated discount)
      - note: str
    """
    body = body or {}
    order = await db.scalar(
        select(SaleOrder).where(
            SaleOrder.id == order_id,
            SaleOrder.merchant_id == merchant.id,
        )
    )
    if not order:
        raise HTTPException(status_code=404, detail="订单不存在")
    if order.status != "held":
        raise HTTPException(status_code=409, detail=f"只能取回挂单状态的订单，当前状态: {order.status}")

    # Fetch items
    items = (
        (await db.execute(
            select(SaleOrderItem).where(SaleOrderItem.order_id == order.id)
        ))
        .scalars()
        .all()
    )
    if not items:
        raise HTTPException(status_code=400, detail="挂单内无商品，请取消后重新开单")

    # Optionally update discount
    if "discount_amount" in body:
        new_discount = Decimal(str(body["discount_amount"])).quantize(Decimal("0.01"))
        gross = order.total_amount + order.discount_amount  # reverse-engineer gross
        if new_discount > gross:
            raise HTTPException(status_code=400, detail="优惠金额不能大于商品总额")
        order.discount_amount = new_discount
        order.total_amount = (gross - new_discount).quantize(Decimal("0.01"))

    if "note" in body:
        order.note = body["note"]
    if "customer_name" in body:
        order.customer_name = (body["customer_name"] or "").strip() or None

    # Deduct inventory FIFO
    product_ids = {i.product_id for i in items}
    product_map = await _resolve_product_map(db, product_ids)
    for item in items:
        product = product_map.get(item.product_id)
        product_name = product.name if product else f"商品{item.product_id}"
        consumed = await consume_batches_fifo(
            db, merchant.id, item.product_id, item.quantity, sku_id=item.sku_id,
        )
        if consumed < item.quantity:
            raise HTTPException(
                status_code=409,
                detail=f"{product_name}库存不足，需要{item.quantity}{item.unit}，可售{consumed}{item.unit}",
            )
        db.add(
            InventoryRecord(
                merchant_id=merchant.id,
                product_id=item.product_id,
                sku_id=item.sku_id,
                quantity=-item.quantity,
                unit=item.unit,
                unit_price=item.unit_price,
                total_amount=item.total_amount,
                event_type="sale",
                event_time=utc_now(),
                source="pos",
                notes=f"订单 {order.order_no}（取回挂单）",
                idempotency_key=f"sale:{order.id}:{item.id}",
                client_id=order.client_id,
                client_reference=order.order_no,
            )
        )

    # Apply payment
    payments_raw = body.get("payments")
    payment_method = body.get("payment_method", "cash")
    await _apply_payments(
        db, order, merchant.id, order.total_amount,
        payment_method=payment_method if not payments_raw else None,
        payments=[
            type("P", (), {"method": p["method"], "amount": p["amount"]})()
            for p in payments_raw
        ] if payments_raw else None,
        customer_name=order.customer_name,
    )

    order.held_at = None  # clear hold timestamp

    await db.commit()
    await db.refresh(order)

    return {
        "code": 0,
        "message": "挂单已取回并完成收款",
        "data": _order_data(order),
    }


@router.delete("/orders/{order_id}", response_model=AnyResponse)
async def cancel_held_order(
    order_id: uuid.UUID,
    merchant: Merchant = Depends(get_current_merchant),
    db: AsyncSession = Depends(get_db),
):
    """Cancel a held order (only held orders can be cancelled without refund)."""
    order = await db.scalar(
        select(SaleOrder).where(
            SaleOrder.id == order_id,
            SaleOrder.merchant_id == merchant.id,
        )
    )
    if not order:
        raise HTTPException(status_code=404, detail="订单不存在")
    if order.status != "held":
        raise HTTPException(
            status_code=409,
            detail=f"只能取消挂单状态的订单，当前状态: {order.status}。已支付订单请使用退款功能。",
        )

    order.status = "cancelled"
    await db.commit()

    return {"code": 0, "message": "挂单已取消", "data": {"order_id": str(order.id), "status": "cancelled"}}


# ---------------------------------------------------------------------------
# 日结对账
# ---------------------------------------------------------------------------


async def _check_settlement_locked(
    db: AsyncSession, merchant_id: uuid.UUID, action_date: date | None = None,
) -> None:
    """如果当天日结已关闭，禁止业务操作（section 4.10 日结锁定）。"""
    from datetime import date as date_type
    target_date = action_date or utc_now().date()
    settlement = await db.scalar(
        select(DailySettlement).where(
            DailySettlement.merchant_id == merchant_id,
            DailySettlement.date == target_date,
            DailySettlement.status == "closed",
        )
    )
    if settlement:
        raise HTTPException(
            status_code=409,
            detail=f"日结已关闭({target_date})，不允许新增或修改业务数据",
        )


async def _settlement_numbers(
    db: AsyncSession, merchant_id: uuid.UUID, settle_date: date
) -> dict[str, Decimal | int]:
    day_start = datetime.combine(settle_date, time.min, tzinfo=UTC)
    day_end = datetime.combine(settle_date, time.max, tzinfo=UTC)
    order_filters = (
        SaleOrder.merchant_id == merchant_id,
        SaleOrder.created_at >= day_start,
        SaleOrder.created_at <= day_end,
        SaleOrder.status.not_in(("cancelled", "held")),
    )
    total_sales, order_count, credit_amount, refund_amount = (
        await db.execute(
            select(
                func.coalesce(func.sum(SaleOrder.total_amount), Decimal("0")),
                func.count(SaleOrder.id),
                func.coalesce(
                    func.sum(
                        case(
                            (
                                SaleOrder.status.in_(("credit", "partial")),
                                SaleOrder.total_amount - SaleOrder.paid_amount,
                            ),
                            else_=Decimal("0"),
                        )
                    ),
                    Decimal("0"),
                ),
                func.coalesce(func.sum(SaleOrder.refunded_amount), Decimal("0")),
            ).where(*order_filters)
        )
    ).one()

    payment_rows = (
        await db.execute(
            select(Payment.method, func.coalesce(func.sum(Payment.amount), Decimal("0")))
            .join(SaleOrder, SaleOrder.id == Payment.order_id)
            .where(
                Payment.merchant_id == merchant_id,
                Payment.status == "success",
                Payment.created_at >= day_start,
                Payment.created_at <= day_end,
                SaleOrder.created_at >= day_start,
                SaleOrder.created_at <= day_end,
                SaleOrder.status.not_in(("cancelled", "held")),
            )
            .group_by(Payment.method)
        )
    ).all()
    by_method = {method: amount for method, amount in payment_rows}
    cash = by_method.get("cash", Decimal("0"))
    wechat = by_method.get("wechat", Decimal("0"))
    alipay = by_method.get("alipay", Decimal("0"))
    card = by_method.get("card", Decimal("0"))
    payments = cash + wechat + alipay + card

    # 采购付款（当日 supplier payments）
    from app.models.accounts import SupplierPayable
    purchase_paid_row = await db.execute(
        select(func.coalesce(func.sum(SupplierPayable.amount), Decimal("0"))).where(
            SupplierPayable.merchant_id == merchant_id,
            SupplierPayable.direction == "payment",
            SupplierPayable.created_at >= day_start,
            SupplierPayable.created_at <= day_end,
        )
    )
    purchase_paid = purchase_paid_row.scalar() or Decimal("0")

    # 新增供应商欠款（当日产生的应付）
    purchase_new_debt_row = await db.execute(
        select(func.coalesce(func.sum(SupplierPayable.amount), Decimal("0"))).where(
            SupplierPayable.merchant_id == merchant_id,
            SupplierPayable.direction == "purchase",
            SupplierPayable.created_at >= day_start,
            SupplierPayable.created_at <= day_end,
        )
    )
    purchase_new_debt = purchase_new_debt_row.scalar() or Decimal("0")

    # 客户回款
    from app.models.accounts import CustomerReceivable
    customer_repay_row = await db.execute(
        select(func.coalesce(func.sum(CustomerReceivable.amount), Decimal("0"))).where(
            CustomerReceivable.merchant_id == merchant_id,
            CustomerReceivable.direction == "repay",
            CustomerReceivable.created_at >= day_start,
            CustomerReceivable.created_at <= day_end,
        )
    )
    customer_repay = customer_repay_row.scalar() or Decimal("0")

    # 报损成本
    waste_cost_row = await db.execute(
        select(func.coalesce(func.sum(InventoryRecord.total_amount), Decimal("0"))).where(
            InventoryRecord.merchant_id == merchant_id,
            InventoryRecord.event_type == "waste",
            InventoryRecord.event_time >= day_start,
            InventoryRecord.event_time <= day_end,
        )
    )
    waste_cost = abs(waste_cost_row.scalar() or Decimal("0"))

    # Returns the number dict
    net_cash_flow = payments + customer_repay - purchase_paid - (refund_amount or Decimal("0"))
    estimated_gross_profit = total_sales - waste_cost - (
        # rough cost = purchase payments / sales ratio — placeholder
        purchase_paid if purchase_paid > 0 else Decimal("0")
    )

    return {
        "total_sales": total_sales,
        "order_count": int(order_count),
        "total_payments": payments,
        "cash_amount": cash,
        "wechat_amount": wechat,
        "alipay_amount": alipay,
        "card_amount": card,
        "credit_amount": credit_amount,
        "refund_amount": refund_amount or Decimal("0"),
        "purchase_paid": purchase_paid,
        "purchase_new_debt": purchase_new_debt,
        "customer_repay": customer_repay,
        "waste_cost": waste_cost,
        "net_cash_flow": net_cash_flow,
        "estimated_gross_profit": estimated_gross_profit,
        "diff_amount": total_sales - payments - credit_amount - (refund_amount or Decimal("0")),
    }


@router.post("/daily-settlement/{settle_date}/close", response_model=AnyResponse)
async def close_daily_settlement(
    settle_date: date,
    merchant: Merchant = Depends(get_current_merchant),
    db: AsyncSession = Depends(get_db),
):
    numbers = await _settlement_numbers(db, merchant.id, settle_date)
    settlement = await db.scalar(
        select(DailySettlement).where(
            DailySettlement.merchant_id == merchant.id,
            DailySettlement.date == settle_date,
        )
    )
    if settlement is None:
        settlement = DailySettlement(merchant_id=merchant.id, date=settle_date)
        db.add(settlement)
    for field in (
        "total_sales",
        "total_payments",
        "cash_amount",
        "wechat_amount",
        "alipay_amount",
        "card_amount",
        "credit_amount",
        "diff_amount",
    ):
        setattr(settlement, field, numbers.get(field, Decimal("0")))
    settlement.status = "closed"
    settlement.closed_at = utc_now()

    reconciliation = await db.scalar(
        select(Reconciliation).where(
            Reconciliation.merchant_id == merchant.id,
            Reconciliation.date == settle_date,
        )
    )
    if reconciliation is None:
        reconciliation = Reconciliation(merchant_id=merchant.id, date=settle_date)
        db.add(reconciliation)
    reconciliation.sale_total = numbers["total_sales"]
    reconciliation.payment_total = numbers["total_payments"]
    reconciliation.diff_amount = numbers["diff_amount"]
    reconciliation.status = "balanced" if numbers["diff_amount"] == 0 else "exception"

    db.add(AuditLog(
        merchant_id=merchant.id,
        action="daily_settlement_close",
        target_table="daily_settlements",
        target_id=str(settlement.id),
        after_data={k: float(v) if isinstance(v, Decimal) else v for k, v in numbers.items()},
        reason=f"日结 {settle_date}",
        operator="merchant",
    ))

    await db.commit()
    return {
        "code": 0,
        "data": {
            "date": settle_date.isoformat(),
            **{key: float(value) if isinstance(value, Decimal) else value
               for key, value in numbers.items()},
            "status": settlement.status,
        },
    }


@router.get("/daily-settlement/{settle_date}", response_model=AnyResponse)
async def get_daily_settlement(
    settle_date: date,
    merchant: Merchant = Depends(get_current_merchant),
    db: AsyncSession = Depends(get_db),
):
    settlement = await db.scalar(
        select(DailySettlement).where(
            DailySettlement.merchant_id == merchant.id,
            DailySettlement.date == settle_date,
        )
    )
    if not settlement:
        # Return live numbers if not yet closed
        numbers = await _settlement_numbers(db, merchant.id, settle_date)
        return {
            "code": 0,
            "data": {
                "date": settle_date.isoformat(),
                **{key: float(value) if isinstance(value, Decimal) else value
                   for key, value in numbers.items()},
                "status": "open",
            },
        }
    return {
        "code": 0,
        "data": {
            "date": settlement.date.isoformat(),
            "total_sales": float(settlement.total_sales),
            "total_payments": float(settlement.total_payments),
            "cash_amount": float(settlement.cash_amount),
            "wechat_amount": float(settlement.wechat_amount),
            "alipay_amount": float(settlement.alipay_amount),
            "card_amount": float(settlement.card_amount),
            "credit_amount": float(settlement.credit_amount),
            "diff_amount": float(settlement.diff_amount),
            "status": settlement.status,
        },
    }
