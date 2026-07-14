"""POS sale, payment, refund, hold, and daily reconciliation APIs.

P0 新增（2026-07-12）:
- 组合支付：一笔订单多个支付方式
- 退款/退货：整单退款 + 单品退款，反向流水，可选退货入库
- 挂单/取单：挂起订单 → 取回继续 → 取消
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from typing import TypedDict

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import case, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import get_current_merchant
from app.core.tenant_context import QuotaCheck
from app.core.timezone import utc_now
from app.database import get_db
from app.models.audit import AuditLog
from app.models.catalog import ProductSKU
from app.models.inventory import InventoryRecord
from app.models.merchant import Merchant
from app.models.payment import ChannelBillImport
from app.models.pos import DailySettlement, Payment, Reconciliation, SaleOrder, SaleOrderItem
from app.models.product import ProductCategory
from app.routers.staff import require_permission
from app.schemas.common import AnyResponse
from app.schemas.pos import (
    CreateSaleOrderRequest,
    HoldOrderRequest,
    PaySaleOrderRequest,
    RefundOrderRequest,
    ResumeHeldOrderRequest,
)
from app.services.accounts_service import record_customer_receivable
from app.services.batch import (
    consume_batches_fifo_costed,
    return_to_batches,
)
from app.services.reconciliation import get_or_create_task, reconcile_task
from app.services.sku_service import resolve_sku_id


router = APIRouter(prefix="/api/v1/pos", tags=["pos"])


class SettlementNumbers(TypedDict):
    total_sales: Decimal
    order_count: int
    total_payments: Decimal
    cash_amount: Decimal
    wechat_amount: Decimal
    alipay_amount: Decimal
    card_amount: Decimal
    credit_amount: Decimal
    refund_amount: Decimal
    purchase_paid: Decimal
    purchase_new_debt: Decimal
    customer_repay: Decimal
    waste_cost: Decimal
    net_cash_flow: Decimal
    estimated_cogs: Decimal
    estimated_gross_profit: Decimal
    diff_amount: Decimal


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _generate_order_no() -> str:
    now = utc_now()
    return f"POS{now.strftime('%Y%m%d%H%M%S')}{now.microsecond // 1000:03d}"


def _decimal_value(value: object | None) -> Decimal:
    """Normalize SQL aggregate values returned by different DB drivers."""
    if value is None:
        return Decimal("0")
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value))


def _product_label(
    product_map: dict[int, ProductCategory], product_id: int | None
) -> str:
    if product_id is None:
        return "未知商品（历史订单行缺少商品关联）"
    product = product_map.get(product_id)
    return product.name if product else f"商品{product_id}"


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


def _require_product_id(item: SaleOrderItem, *, action: str = "处理订单") -> int:
    """Reject legacy/corrupt rows before an operation writes inventory."""
    if item.product_id is None:
        raise HTTPException(
            status_code=409,
            detail=f"订单行 {item.id} 缺少商品关联，无法{action}，请联系管理员修复历史数据",
        )
    return item.product_id


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
                    ProductSKU.is_active.is_(True),
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

        consumption = await consume_batches_fifo_costed(
            db,
            merchant_id,
            request_item.product_id,
            quantity,
            sku_id=sku_id,
        )
        consumed = consumption["quantity"]
        if consumed < quantity:
            raise HTTPException(
                status_code=409,
                detail=f"{product.name}库存不足，需要{quantity}{request_item.unit}，可售{consumed}{request_item.unit}",
            )

        unit_cost = (
            (consumption["total_cost"] / consumed).quantize(Decimal("0.01"))
            if consumed > 0 and consumption["missing_cost_quantity"] == 0
            else None
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
            unit_cost=unit_cost,
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
                unit_cost=unit_cost,
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
        # 组合支付：先完整校验，再写支付与应收，避免半处理状态。
        normalized_payments = []
        total_paid = Decimal("0")
        allowed_methods = {"cash", "wechat", "alipay", "card", "credit"}
        for p in payments:
            amt = Decimal(str(p.amount)).quantize(Decimal("0.01"))
            if amt <= 0:
                raise HTTPException(status_code=400, detail="每笔支付金额必须大于0")
            if p.method not in allowed_methods:
                raise HTTPException(status_code=400, detail=f"不支持的支付方式: {p.method}")
            if p.method == "credit" and not (customer_name or "").strip():
                raise HTTPException(status_code=400, detail="赊账订单必须填写客户名称")
            normalized_payments.append((p.method, amt))
            total_paid += amt
        if abs(total_paid - payable) > Decimal("0.01"):
            raise HTTPException(
                status_code=400,
                detail=f"支付金额合计 {total_paid} 与应收 {payable} 不匹配",
            )
        for method, amt in normalized_payments:
            payment = Payment(
                merchant_id=merchant_id,
                order_id=order.id,
                amount=amt,
                method=method,
                status="success",
                note=f"订单 {order.order_no} 组合支付",
            )
            db.add(payment)
            if method == "credit":
                await record_customer_receivable(
                    db,
                    merchant_id=merchant_id,
                    customer_name=(customer_name or "").strip(),
                    amount=amt,
                    direction="charge",
                    sale_order_id=order.id,
                    note=f"订单 {order.order_no} 赊账（组合支付）",
                    idempotency_key=f"sale-credit:{order.id}:{method}",
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
    _quota=Depends(QuotaCheck("api_calls")),
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
        db,
        order,
        merchant.id,
        body.items,
        product_map,
        sku_map,
    )

    if order.discount_amount > gross_total:
        raise HTTPException(status_code=400, detail="优惠金额不能大于商品总额")
    order.total_amount = (gross_total - order.discount_amount).quantize(Decimal("0.01"))

    await _apply_payments(
        db,
        order,
        merchant.id,
        order.total_amount,
        payment_method=body.payment_method if not body.payments else None,
        payments=[type("P", (), {"method": p.method, "amount": p.amount})() for p in body.payments]
        if body.payments
        else None,
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
    await _auto_reconcile_after_payment(db, merchant.id, order)
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
        (
            await db.execute(
                select(SaleOrder)
                .where(
                    SaleOrder.merchant_id == merchant.id,
                    SaleOrder.status == "held",
                )
                .order_by(SaleOrder.held_at.desc())
            )
        )
        .scalars()
        .all()
    )

    result = []
    for order in orders:
        item_count_result = await db.scalar(
            select(func.count(SaleOrderItem.id)).where(SaleOrderItem.order_id == order.id)
        )
        result.append(
            {
                "order_id": str(order.id),
                "order_no": order.order_no,
                "item_count": int(item_count_result or 0),
                "total_amount": float(order.total_amount),
                "customer_name": order.customer_name,
                "held_at": order.held_at.isoformat() if order.held_at else None,
            }
        )

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
        (await db.execute(select(SaleOrderItem).where(SaleOrderItem.order_id == order.id)))
        .scalars()
        .all()
    )
    payments = (
        (await db.execute(select(Payment).where(Payment.order_id == order.id))).scalars().all()
    )

    product_ids = {item.product_id for item in items if item.product_id is not None}
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
                    "product_name": _product_label(product_map, item.product_id),
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

    # Combo payment: if body.payments is provided, use it; otherwise single method
    if body.payments:
        total_pay = sum(
            (Decimal(str(p.amount)) for p in body.payments), start=Decimal("0")
        ).quantize(Decimal("0.01"))
        if total_pay > Decimal(str(remaining)):
            raise HTTPException(status_code=400, detail=f"组合支付总额超过待收金额 {remaining}")
        created_payments = []
        for p in body.payments:
            p_amt = Decimal(str(p.amount)).quantize(Decimal("0.01"))
            if p_amt <= 0:
                continue
            pay = Payment(
                merchant_id=merchant.id,
                order_id=order.id,
                amount=p_amt,
                method=p.method,
                status="success",
                note=body.note,
            )
            db.add(pay)
            created_payments.append(pay)
        await db.flush()
        order.paid_amount = (order.paid_amount or Decimal("0")) + total_pay
        if order.paid_amount >= order.total_amount:
            order.status = "paid"
            order.paid_at = utc_now()
        else:
            order.status = "partial"
        await db.commit()
        await _auto_reconcile_after_payment(db, merchant.id, order)
        return {
            "code": 0,
            "data": {
                "payment_id": str(created_payments[0].id) if created_payments else None,
                "order_id": str(order.id),
                "paid_amount": float(order.paid_amount),
                "remaining_amount": float(order.total_amount - order.paid_amount),
                "status": order.status,
                "payments": [
                    {"payment_id": str(p.id), "amount": float(p.amount), "method": p.method}
                    for p in created_payments
                ],
                "duplicate": False,
            },
        }

    # Single payment (original logic)
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
    await _auto_reconcile_after_payment(db, merchant.id, order)
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
    product_id = _require_product_id(item, action="执行库存退款")
    unit_price = item.unit_price or Decimal("0")
    refund_amount = (refund_qty * unit_price).quantize(Decimal("0.01"))

    # Record refunded quantity on the item
    item.refund_quantity = (item.refund_quantity or Decimal("0")) + refund_qty
    item.return_to_stock = return_to_stock

    # Reverse inventory: positive quantity = stock returned
    inv_record = InventoryRecord(
        merchant_id=merchant_id,
        product_id=product_id,
        sku_id=item.sku_id,
        quantity=refund_qty if return_to_stock else Decimal("0"),
        unit=item.unit,
        unit_price=unit_price,
        unit_cost=item.unit_cost,
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
            db,
            merchant_id,
            product_id,
            refund_qty,
            sku_id=item.sku_id,
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
    _perm=Depends(require_permission("order_refund")),
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
        (await db.execute(select(SaleOrderItem).where(SaleOrderItem.order_id == order.id)))
        .scalars()
        .all()
    )
    item_map = {item.id: item for item in items}

    product_ids = {item.product_id for item in items if item.product_id is not None}
    product_map = await _resolve_product_map(db, product_ids)

    results: list[dict] = []
    total_refund = Decimal("0")

    if body.items:
        # Partial refund: refund specified items
        refund_spec = {item.item_id: item for item in body.items}
        refund_plan = []
        for item_id, spec in refund_spec.items():
            item = item_map.get(item_id)
            if not item:
                raise HTTPException(status_code=404, detail=f"订单行项目 {item_id} 不存在")
            _require_product_id(item, action="执行库存退款")
            refund_qty = Decimal(str(spec.quantity)).quantize(Decimal("0.01"))
            already_refunded = item.refund_quantity or Decimal("0")
            available = item.quantity - already_refunded
            if refund_qty > available:
                raise HTTPException(
                    status_code=400,
                    detail=f"退款数量{refund_qty}超过可退数量{available}",
                )
            refund_plan.append((item, spec, refund_qty))

        for item, spec, refund_qty in refund_plan:
            result = await _refund_single_item(
                db,
                order,
                item,
                refund_qty,
                spec.return_to_stock,
                body.reason,
                merchant.id,
                _product_label(product_map, item.product_id),
            )
            results.append(result)
            total_refund += Decimal(str(result["refund_amount"])).quantize(Decimal("0.01"))

        # Determine new status: check if ALL items are fully refunded
        all_items_refunded = all(
            (item.refund_quantity or Decimal("0")) >= item.quantity for item in items
        )
        if all_items_refunded:
            order.status = "refunded"
        else:
            order.status = "partial_refund"
    else:
        # Full refund
        full_refund_plan = []
        for item in items:
            remaining = item.quantity - (item.refund_quantity or Decimal("0"))
            if remaining <= 0:
                continue
            _require_product_id(item, action="执行库存退款")
            full_refund_plan.append((item, remaining))

        for item, remaining in full_refund_plan:
            result = await _refund_single_item(
                db,
                order,
                item,
                remaining,
                body.return_to_stock,
                body.reason,
                merchant.id,
                _product_label(product_map, item.product_id),
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
        (
            await db.execute(
                select(Payment).where(
                    Payment.order_id == order.id,
                    Payment.status == "success",
                )
            )
        )
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
            db.add(
                Payment(
                    merchant_id=merchant.id,
                    order_id=order.id,
                    amount=-amt,  # negative = refund
                    method=method,
                    status="refunded",
                    note=f"退款 订单 {order.order_no}: {body.reason}",
                )
            )
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
    db.add(
        AuditLog(
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
        )
    )

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
    body: ResumeHeldOrderRequest = ResumeHeldOrderRequest(),
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
            status_code=409, detail=f"只能取回挂单状态的订单，当前状态: {order.status}"
        )

    # Fetch items
    items = (
        (await db.execute(select(SaleOrderItem).where(SaleOrderItem.order_id == order.id)))
        .scalars()
        .all()
    )
    if not items:
        raise HTTPException(status_code=400, detail="挂单内无商品，请取消后重新开单")

    # Optionally update discount
    if body.discount_amount is not None:
        new_discount = Decimal(str(body.discount_amount)).quantize(Decimal("0.01"))
        gross = order.total_amount + order.discount_amount  # reverse-engineer gross
        if new_discount > gross:
            raise HTTPException(status_code=400, detail="优惠金额不能大于商品总额")
        order.discount_amount = new_discount
        order.total_amount = (gross - new_discount).quantize(Decimal("0.01"))

    if body.note is not None:
        order.note = body.note
    if body.customer_name is not None:
        order.customer_name = body.customer_name.strip() or None

    # Validate all historical rows before consuming any batch, preventing partial deductions.
    product_ids_by_item = {
        item.id: _require_product_id(item, action="取回挂单") for item in items
    }
    product_map = await _resolve_product_map(db, set(product_ids_by_item.values()))
    for item in items:
        product_id = product_ids_by_item[item.id]
        product_name = _product_label(product_map, product_id)
        consumption = await consume_batches_fifo_costed(
            db,
            merchant.id,
            product_id,
            item.quantity,
            sku_id=item.sku_id,
        )
        consumed = consumption["quantity"]
        if consumed < item.quantity:
            raise HTTPException(
                status_code=409,
                detail=f"{product_name}库存不足，需要{item.quantity}{item.unit}，可售{consumed}{item.unit}",
            )
        item.unit_cost = (
            (consumption["total_cost"] / consumed).quantize(Decimal("0.01"))
            if consumed > 0 and consumption["missing_cost_quantity"] == 0
            else None
        )
        db.add(
            InventoryRecord(
                merchant_id=merchant.id,
                product_id=product_id,
                sku_id=item.sku_id,
                quantity=-item.quantity,
                unit=item.unit,
                unit_cost=item.unit_cost,
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
    payments_raw = body.payments
    payment_method = body.payment_method
    has_credit = payment_method == "credit" or any(
        p.method == "credit" for p in (payments_raw or [])
    )
    if has_credit and not (order.customer_name or "").strip():
        raise HTTPException(status_code=400, detail="赊账订单必须填写客户名称")
    await _apply_payments(
        db,
        order,
        merchant.id,
        order.total_amount,
        payment_method=payment_method if not payments_raw else None,
        payments=payments_raw,
        customer_name=order.customer_name,
    )

    order.held_at = None  # clear hold timestamp

    await db.commit()
    await db.refresh(order)
    await _auto_reconcile_after_payment(db, merchant.id, order)

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

    return {
        "code": 0,
        "message": "挂单已取消",
        "data": {"order_id": str(order.id), "status": "cancelled"},
    }


# ---------------------------------------------------------------------------
# 日结对账
# ---------------------------------------------------------------------------


async def _auto_reconcile_after_payment(
    db: AsyncSession,
    merchant_id: uuid.UUID,
    order: SaleOrder,
) -> None:
    """Best-effort auto-reconciliation after payment creation.

    Checks if there are imported channel bills for the same date and channel,
    and triggers reconciliation if so. Failures are silently ignored — reconciliation
    is non-blocking for the payment flow.
    """
    try:
        payments = (
            await db.execute(
                select(Payment.method).where(
                    Payment.order_id == order.id,
                    Payment.status == "success",
                )
            )
        ).scalars().all()

        unique_channels = set(payments)
        today = utc_now().date()
        fee_rate = Decimal("0.006")

        for channel in unique_channels:
            task = await get_or_create_task(db, merchant_id, channel, today)
            import_count = await db.scalar(
                select(func.count(ChannelBillImport.id)).where(
                    ChannelBillImport.task_id == task.id
                )
            )
            if import_count and import_count > 0:
                await reconcile_task(db, task, fee_rate=fee_rate)
    except Exception:
        pass  # Reconciliation is best-effort; don't fail the payment


async def _check_settlement_locked(
    db: AsyncSession,
    merchant_id: uuid.UUID,
    action_date: date | None = None,
) -> None:
    """如果当天日结已关闭，禁止业务操作（section 4.10 日结锁定）。"""
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


async def _estimate_daily_cogs(
    db: AsyncSession,
    merchant_id: uuid.UUID,
    day_start: datetime,
    day_end: datetime,
) -> Decimal:
    """Estimate COGS from sold quantity and recent average purchase cost."""
    inventory_rows = (
        await db.execute(
            select(
                InventoryRecord.product_id,
                InventoryRecord.quantity,
                InventoryRecord.unit_cost,
            )
            .where(
                InventoryRecord.merchant_id == merchant_id,
                InventoryRecord.is_voided.is_(False),
                InventoryRecord.event_type.in_(("sale", "refund")),
                InventoryRecord.event_time >= day_start,
                InventoryRecord.event_time <= day_end,
            )
        )
    ).all()
    if not inventory_rows:
        return Decimal("0")

    exact_cogs = Decimal("0")
    unknown_quantities: dict[int, Decimal] = {}
    for product_id, quantity, unit_cost in inventory_rows:
        normalized_quantity = _decimal_value(quantity)
        if unit_cost is not None:
            exact_cogs -= normalized_quantity * _decimal_value(unit_cost)
        else:
            unknown_quantities[product_id] = (
                unknown_quantities.get(product_id, Decimal("0")) - normalized_quantity
            )
    if not unknown_quantities:
        return exact_cogs.quantize(Decimal("0.01"))

    average_cost_rows = (
        await db.execute(
            select(
                InventoryRecord.product_id,
                func.avg(InventoryRecord.unit_cost),
            )
            .where(
                InventoryRecord.merchant_id == merchant_id,
                InventoryRecord.is_voided.is_(False),
                InventoryRecord.event_type == "purchase",
                InventoryRecord.product_id.in_(set(unknown_quantities)),
                InventoryRecord.unit_cost.isnot(None),
                InventoryRecord.event_time >= day_start - timedelta(days=30),
                InventoryRecord.event_time <= day_end,
            )
            .group_by(InventoryRecord.product_id)
        )
    ).all()
    average_costs = {
        product_id: _decimal_value(average_cost)
        for product_id, average_cost in average_cost_rows
    }
    fallback_cogs = sum(
        (
            quantity * average_costs.get(product_id, Decimal("0"))
            for product_id, quantity in unknown_quantities.items()
        ),
        Decimal("0"),
    )
    return (exact_cogs + fallback_cogs).quantize(Decimal("0.01"))


async def _settlement_numbers(
    db: AsyncSession, merchant_id: uuid.UUID, settle_date: date
) -> SettlementNumbers:
    day_start = datetime.combine(settle_date, time.min, tzinfo=UTC)
    day_end = datetime.combine(settle_date, time.max, tzinfo=UTC)
    order_filters = (
        SaleOrder.merchant_id == merchant_id,
        SaleOrder.created_at >= day_start,
        SaleOrder.created_at <= day_end,
        SaleOrder.status.not_in(("cancelled", "held")),
    )
    total_sales_raw, order_count, credit_amount_raw, refund_amount_raw = (
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
    total_sales = _decimal_value(total_sales_raw)
    credit_amount = _decimal_value(credit_amount_raw)
    refund_amount = _decimal_value(refund_amount_raw)

    payment_rows = (
        await db.execute(
            select(Payment.method, func.coalesce(func.sum(Payment.amount), Decimal("0")))
            .join(SaleOrder, SaleOrder.id == Payment.order_id)
            .where(
                Payment.merchant_id == merchant_id,
                Payment.status.in_(("success", "refunded")),
                Payment.created_at >= day_start,
                Payment.created_at <= day_end,
                SaleOrder.created_at >= day_start,
                SaleOrder.created_at <= day_end,
                SaleOrder.status.not_in(("cancelled", "held")),
            )
            .group_by(Payment.method)
        )
    ).all()
    by_method: dict[str, Decimal] = {
        method: _decimal_value(amount) for method, amount in payment_rows
    }
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
    purchase_paid = _decimal_value(purchase_paid_row.scalar())

    # 新增供应商欠款（当日产生的应付）
    purchase_new_debt_row = await db.execute(
        select(func.coalesce(func.sum(SupplierPayable.amount), Decimal("0"))).where(
            SupplierPayable.merchant_id == merchant_id,
            SupplierPayable.direction == "purchase",
            SupplierPayable.created_at >= day_start,
            SupplierPayable.created_at <= day_end,
        )
    )
    purchase_new_debt = _decimal_value(purchase_new_debt_row.scalar())

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
    customer_repay = _decimal_value(customer_repay_row.scalar())

    # 报损成本
    waste_cost_row = await db.execute(
        select(func.coalesce(func.sum(InventoryRecord.total_amount), Decimal("0"))).where(
            InventoryRecord.merchant_id == merchant_id,
            InventoryRecord.event_type == "waste",
            InventoryRecord.event_time >= day_start,
            InventoryRecord.event_time <= day_end,
        )
    )
    waste_cost = abs(_decimal_value(waste_cost_row.scalar()))

    estimated_cogs = await _estimate_daily_cogs(db, merchant_id, day_start, day_end)
    net_cash_flow = payments + customer_repay - purchase_paid
    estimated_gross_profit = total_sales - refund_amount - estimated_cogs

    return {
        "total_sales": total_sales,
        "order_count": int(order_count),
        "total_payments": payments,
        "cash_amount": cash,
        "wechat_amount": wechat,
        "alipay_amount": alipay,
        "card_amount": card,
        "credit_amount": credit_amount,
        "refund_amount": refund_amount,
        "purchase_paid": purchase_paid,
        "purchase_new_debt": purchase_new_debt,
        "customer_repay": customer_repay,
        "waste_cost": waste_cost,
        "net_cash_flow": net_cash_flow,
        "estimated_cogs": estimated_cogs,
        "estimated_gross_profit": estimated_gross_profit,
        "diff_amount": total_sales - payments - credit_amount - refund_amount,
    }


@router.post("/daily-settlement/{settle_date}/close", response_model=AnyResponse)
async def close_daily_settlement(
    settle_date: date,
    merchant: Merchant = Depends(get_current_merchant),
    db: AsyncSession = Depends(get_db),
    _perm=Depends(require_permission("daily_settle")),
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

    db.add(
        AuditLog(
            merchant_id=merchant.id,
            action="daily_settlement_close",
            target_table="daily_settlements",
            target_id=str(settlement.id),
            after_data={k: float(v) if isinstance(v, Decimal) else v for k, v in numbers.items()},
            reason=f"日结 {settle_date}",
            operator="merchant",
        )
    )

    await db.commit()
    return {
        "code": 0,
        "data": {
            "date": settle_date.isoformat(),
            **{
                key: float(value) if isinstance(value, Decimal) else value
                for key, value in numbers.items()
            },
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
                **{
                    key: float(value) if isinstance(value, Decimal) else value
                    for key, value in numbers.items()
                },
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
