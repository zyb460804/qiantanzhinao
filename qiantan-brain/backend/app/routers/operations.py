"""经营管理 API — 报损/临期清货/客户赊账档案/数据导出。

覆盖规格文档 sections 4.4, 4.8, 4.12, 4.19。
"""

from __future__ import annotations

import csv
import io
import uuid
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import get_current_merchant
from app.core.timezone import utc_now
from app.database import get_db
from app.models.accounts import CustomerCreditProfile, CustomerReceivable
from app.models.audit import AuditLog
from app.models.batch import BatchLifecycle
from app.models.inventory import InventoryRecord
from app.models.merchant import Merchant
from app.models.pos import SaleOrder
from app.models.product import ProductCategory
from app.routers.staff import require_permission
from app.schemas.common import AnyResponse
from app.services.accounts_service import get_customer_balance, record_customer_receivable
from app.services.batch import consume_batches_fifo


class ClearancePromotionRequest(BaseModel):
    promotion_price: Decimal = Field(gt=0)
    start_at: datetime | None = None
    end_at: datetime | None = None


def _naive_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is not None:
        value = value.astimezone(UTC)
    return value.replace(tzinfo=None)


router = APIRouter(prefix="/api/v1/ops", tags=["operations"])

# 报损原因字典
WASTE_REASONS = [
    "腐烂",
    "碰伤",
    "脱水",
    "过熟",
    "顾客挑拣损坏",
    "试吃",
    "赠送",
    "称重误差",
    "盘点差异",
    "供应商质量问题",
    "冷柜故障",
    "临期未售完",
    "其他",
]


# ═══════════════════════════════════════════════════════════
# 报损记录 (section 4.12)
# ═══════════════════════════════════════════════════════════


@router.get("/waste-reasons", response_model=AnyResponse)
async def list_waste_reasons():
    """Return standard waste reason list for UI picker."""
    return {"code": 0, "data": WASTE_REASONS}


@router.post("/waste", response_model=AnyResponse)
async def record_waste(
    body: dict,
    merchant: Merchant = Depends(get_current_merchant),
    db: AsyncSession = Depends(get_db),
    _perm=Depends(require_permission("record_waste")),
):
    """Record waste/loss: deduct from FIFO batches, write inventory ledger.

    Body: {product_id, sku_id?, quantity, unit, reason, notes?, photos?}
    """
    product_id = body["product_id"]
    sku_id = uuid.UUID(body["sku_id"]) if body.get("sku_id") else None
    quantity = Decimal(str(body["quantity"]))
    reason = body.get("reason", "其他")
    notes = body.get("notes", "")
    photos = body.get("photos", "")

    if photos:
        notes = f"{notes} [照片: {photos}]" if notes else f"[照片: {photos}]"

    if quantity <= 0:
        raise HTTPException(status_code=400, detail="报损数量必须大于0")
    if reason not in WASTE_REASONS:
        raise HTTPException(status_code=400, detail=f"无效报损原因: {reason}")

    product = await db.get(ProductCategory, product_id)
    if not product:
        raise HTTPException(status_code=404, detail="商品不存在")

    # FIFO consume for waste
    consumed = await consume_batches_fifo(db, merchant.id, product_id, quantity, sku_id=sku_id)
    if consumed < quantity:
        raise HTTPException(
            status_code=409,
            detail=f"库存不足，报损需要{float(quantity)}{product.unit}，可用{float(consumed)}{product.unit}",
        )

    record = InventoryRecord(
        merchant_id=merchant.id,
        product_id=product_id,
        sku_id=sku_id,
        quantity=-quantity,
        unit=body.get("unit", product.unit),
        event_type="waste",
        event_time=utc_now(),
        source="manual",
        notes=f"{reason}: {notes}" if notes else reason,
        idempotency_key=body.get("idempotency_key")
        or f"waste:{merchant.id}:{product_id}:{utc_now().timestamp()}",
    )
    db.add(record)

    db.add(
        AuditLog(
            merchant_id=merchant.id,
            action="record_waste",
            target_table="inventory_records",
            target_id=str(record.id),
            after_data={
                "product_id": product_id,
                "quantity": float(quantity),
                "reason": reason,
                "notes": notes,
            },
            reason=reason,
            operator="merchant",
        )
    )

    await db.commit()
    return {
        "code": 0,
        "message": f"已记录{reason} {float(quantity)}{product.unit}",
        "data": {"record_id": str(record.id), "consumed": float(consumed)},
    }


@router.get("/waste", response_model=AnyResponse)
async def list_waste(
    page: int = 1,
    limit: int = 20,
    reason: str | None = None,
    merchant: Merchant = Depends(get_current_merchant),
    db: AsyncSession = Depends(get_db),
):
    """List waste records with optional reason filter."""
    filters = [InventoryRecord.merchant_id == merchant.id, InventoryRecord.event_type == "waste"]
    if reason:
        filters.append(InventoryRecord.notes.like(f"{reason}%"))
    offset = (page - 1) * limit
    rows = (
        (
            await db.execute(
                select(InventoryRecord)
                .where(*filters)
                .order_by(InventoryRecord.event_time.desc())
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
                "record_id": str(r.id),
                "product_id": r.product_id,
                "quantity": float(r.quantity),
                "unit": r.unit,
                "unit_cost": float(r.unit_cost) if r.unit_cost else None,
                "total_amount": float(r.total_amount) if r.total_amount else None,
                "notes": r.notes,
                "event_time": r.event_time.isoformat() if r.event_time else None,
            }
            for r in rows
        ],
    }


@router.get("/waste/analysis", response_model=AnyResponse)
async def waste_analysis(
    days: int = Query(default=30, ge=1, le=365),
    merchant: Merchant = Depends(get_current_merchant),
    db: AsyncSession = Depends(get_db),
):
    """Analyze waste by reason, product, and supplier over N days."""
    since = utc_now() - timedelta(days=days)

    # By reason
    reason_rows = (
        await db.execute(
            select(
                InventoryRecord.notes,
                func.sum(func.abs(InventoryRecord.quantity)),
                func.sum(func.abs(InventoryRecord.total_amount)),
            )
            .where(
                InventoryRecord.merchant_id == merchant.id,
                InventoryRecord.event_type == "waste",
                InventoryRecord.event_time >= since,
            )
            .group_by(InventoryRecord.notes)
            .order_by(func.sum(func.abs(InventoryRecord.total_amount)).desc())
        )
    ).all()

    # By product
    product_rows = (
        await db.execute(
            select(
                InventoryRecord.product_id,
                func.sum(func.abs(InventoryRecord.quantity)),
                func.sum(func.abs(InventoryRecord.total_amount)),
            )
            .where(
                InventoryRecord.merchant_id == merchant.id,
                InventoryRecord.event_type == "waste",
                InventoryRecord.event_time >= since,
            )
            .group_by(InventoryRecord.product_id)
            .order_by(func.sum(func.abs(InventoryRecord.total_amount)).desc())
            .limit(10)
        )
    ).all()

    product_ids = {p for p, _, _ in product_rows}
    product_names = {}
    if product_ids:
        cats = (
            (await db.execute(select(ProductCategory).where(ProductCategory.id.in_(product_ids))))
            .scalars()
            .all()
        )
        product_names = {c.id: c.name for c in cats}

    return {
        "code": 0,
        "data": {
            "period_days": days,
            "by_reason": [
                {"reason": r, "qty": float(q), "cost": float(c or 0)} for r, q, c in reason_rows
            ],
            "by_product": [
                {
                    "product_id": pid,
                    "product_name": product_names.get(pid, f"商品{pid}"),
                    "qty": float(q),
                    "cost": float(c or 0),
                }
                for pid, q, c in product_rows
            ],
        },
    }


# ═══════════════════════════════════════════════════════════
# 临期清货中心 (section 4.12)
# ═══════════════════════════════════════════════════════════


@router.get("/expiry/clearance", response_model=AnyResponse)
async def expiry_clearance(
    within_hours: int = Query(default=24, ge=1, le=168),
    merchant: Merchant = Depends(get_current_merchant),
    db: AsyncSession = Depends(get_db),
):
    """Return near-expiry items with suggested discount pricing."""
    # SQLite/MySQL legacy datetime columns are naive UTC values.
    now = utc_now().replace(tzinfo=None)
    threshold = now + timedelta(hours=within_hours)

    batches = (
        (
            await db.execute(
                select(BatchLifecycle)
                .where(
                    BatchLifecycle.merchant_id == merchant.id,
                    BatchLifecycle.remaining_qty > 0,
                    BatchLifecycle.expiry_date.isnot(None),
                    BatchLifecycle.expiry_date <= threshold,
                    BatchLifecycle.status.in_(["sellable", "near_expiry"]),
                )
                .order_by(BatchLifecycle.expiry_date.asc())
            )
        )
        .scalars()
        .all()
    )

    product_ids = {b.product_id for b in batches}
    product_names = {}
    if product_ids:
        cats = (
            (await db.execute(select(ProductCategory).where(ProductCategory.id.in_(product_ids))))
            .scalars()
            .all()
        )
        product_names = {c.id: c.name for c in cats}

    # Get SKU prices for discount suggestions
    from app.models.catalog import ProductSKU

    sku_ids = {b.sku_id for b in batches if b.sku_id}
    sku_prices = {}
    if sku_ids:
        skus = (
            (await db.execute(select(ProductSKU).where(ProductSKU.id.in_(sku_ids)))).scalars().all()
        )
        sku_prices = {s.id: s.default_sale_price for s in skus}

    items = []
    for b in batches:
        expiry_date = b.expiry_date
        if expiry_date is None:
            continue
        hours_left = int((expiry_date - now).total_seconds() / 3600)
        risk = "high" if hours_left <= 8 else ("medium" if hours_left <= 24 else "low")
        current_price = sku_prices.get(b.sku_id) if b.sku_id is not None else None
        suggested_price = None
        if current_price:
            if risk == "high":
                suggested_price = float(current_price * Decimal("0.6"))
            elif risk == "medium":
                suggested_price = float(current_price * Decimal("0.8"))
        items.append(
            {
                "batch_id": str(b.id),
                "sku_id": str(b.sku_id) if b.sku_id else None,
                "product_id": b.product_id,
                "product_name": product_names.get(b.product_id, f"商品{b.product_id}"),
                "remaining_qty": float(b.remaining_qty),
                "hours_left": hours_left,
                "risk": risk,
                "current_price": float(current_price) if current_price is not None else None,
                "suggested_price": suggested_price,
                "promotion_price": float(b.promotion_price)
                if b.promotion_price is not None
                else None,
                "promotion_start_at": b.promotion_start_at.isoformat()
                if b.promotion_start_at
                else None,
                "promotion_end_at": b.promotion_end_at.isoformat() if b.promotion_end_at else None,
                "purchase_date": b.purchase_date.isoformat() if b.purchase_date else None,
                "expiry_date": expiry_date.isoformat(),
            }
        )

    return {"code": 0, "data": {"within_hours": within_hours, "count": len(items), "items": items}}


@router.post("/expiry/clearance/{batch_id}/promotion", response_model=AnyResponse)
async def set_clearance_promotion(
    batch_id: uuid.UUID,
    body: ClearancePromotionRequest,
    merchant: Merchant = Depends(get_current_merchant),
    db: AsyncSession = Depends(get_db),
):
    """Set a temporary promotion on one batch without changing SKU base price."""
    result = await db.execute(
        select(BatchLifecycle).where(
            BatchLifecycle.id == batch_id,
            BatchLifecycle.merchant_id == merchant.id,
        )
    )
    batch = result.scalar_one_or_none()
    if batch is None:
        raise HTTPException(status_code=404, detail="批次不存在")
    if batch.remaining_qty <= 0 or batch.status not in {"sellable", "near_expiry"}:
        raise HTTPException(status_code=400, detail="该批次已无可售库存")

    now = utc_now().replace(tzinfo=None)
    start_at = _naive_utc(body.start_at) or now
    end_at = _naive_utc(body.end_at) or batch.expiry_date
    if end_at is None:
        raise HTTPException(status_code=400, detail="批次没有过期时间，不能设置临期促销")
    if end_at <= start_at:
        raise HTTPException(status_code=400, detail="促销结束时间必须晚于开始时间")
    if batch.expiry_date and end_at > batch.expiry_date:
        raise HTTPException(status_code=400, detail="促销结束时间不能晚于批次过期时间")

    batch.promotion_price = body.promotion_price.quantize(Decimal("0.01"))
    batch.promotion_start_at = start_at
    batch.promotion_end_at = end_at
    await db.commit()
    return {
        "code": 0,
        "message": "已设置批次临期促销，不会修改常规售价",
        "data": {
            "batch_id": str(batch.id),
            "promotion_price": float(batch.promotion_price),
            "promotion_start_at": start_at.isoformat(),
            "promotion_end_at": end_at.isoformat(),
        },
    }


# ═══════════════════════════════════════════════════════════
# 客户赊账档案 (section 4.8)
# ═══════════════════════════════════════════════════════════


@router.get("/customers", response_model=AnyResponse)
async def list_customers(
    merchant: Merchant = Depends(get_current_merchant),
    db: AsyncSession = Depends(get_db),
):
    """List customers with balances, credit profiles, and overdue status."""
    from app.services.accounts_service import list_customer_balances

    balances = await list_customer_balances(db, merchant.id)

    # Pre-fetch all credit profiles in one query
    customer_names = [b["customer_name"] for b in balances]
    profiles = {}
    if customer_names:
        profile_rows = (
            (
                await db.execute(
                    select(CustomerCreditProfile).where(
                        CustomerCreditProfile.merchant_id == merchant.id,
                        CustomerCreditProfile.customer_name.in_(customer_names),
                    )
                )
            )
            .scalars()
            .all()
        )
        profiles = {p.customer_name: p for p in profile_rows}

    # Get last transaction date per customer in one grouped query.
    last_transactions: dict[str, datetime] = {}
    if customer_names:
        last_rows = (
            await db.execute(
                select(
                    CustomerReceivable.customer_name,
                    func.max(CustomerReceivable.created_at),
                )
                .where(
                    CustomerReceivable.merchant_id == merchant.id,
                    CustomerReceivable.customer_name.in_(customer_names),
                )
                .group_by(CustomerReceivable.customer_name)
            )
        ).all()
        last_transactions = {name: created_at for name, created_at in last_rows if created_at}

    now = utc_now().replace(tzinfo=None)
    result = []
    for b in balances:
        customer_name = b["customer_name"]
        profile = profiles.get(customer_name)
        last_txn = _naive_utc(last_transactions.get(customer_name))

        overdue_days = 0
        default_days = (
            profile.default_credit_days
            if profile and profile.default_credit_days is not None
            else 30
        )
        if b["balance"] > 0 and last_txn:
            days_since = (now - last_txn).days
            if days_since > default_days:
                overdue_days = days_since - default_days

        credit_limit = float(profile.credit_limit) if profile and profile.credit_limit else None
        result.append(
            {
                "customer_name": customer_name,
                "balance": b["balance"],
                "last_transaction": last_txn.isoformat() if last_txn else None,
                "overdue_days": overdue_days,
                "is_overdue": overdue_days > 0,
                "credit_limit": credit_limit,
                "remaining_credit": round(credit_limit - b["balance"], 2)
                if credit_limit is not None
                else None,
                "is_blocked": profile.is_blocked if profile else False,
                "block_reason": profile.block_reason if profile and profile.is_blocked else None,
            }
        )

    return {"code": 0, "data": result}


@router.get("/customers/{customer_name}/ledger", response_model=AnyResponse)
async def customer_ledger(
    customer_name: str,
    limit: int = 50,
    merchant: Merchant = Depends(get_current_merchant),
    db: AsyncSession = Depends(get_db),
):
    """Get detailed ledger for a specific customer."""
    rows = (
        (
            await db.execute(
                select(CustomerReceivable)
                .where(
                    CustomerReceivable.merchant_id == merchant.id,
                    CustomerReceivable.customer_name == customer_name,
                )
                .order_by(CustomerReceivable.created_at.desc())
                .limit(min(limit, 200))
            )
        )
        .scalars()
        .all()
    )

    total_charge = Decimal("0")
    total_repay = Decimal("0")
    items = []
    for r in rows:
        if r.direction == "charge":
            total_charge += r.amount
        else:
            total_repay += r.amount
        items.append(
            {
                "id": str(r.id),
                "direction": r.direction,
                "amount": float(r.amount),
                "note": r.note,
                "settled": r.settled,
                "created_at": r.created_at.isoformat() if r.created_at else None,
            }
        )

    return {
        "code": 0,
        "data": {
            "customer_name": customer_name,
            "total_charge": float(total_charge),
            "total_repay": float(total_repay),
            "balance": float(total_charge - total_repay),
            "items": items,
        },
    }


@router.post("/customers/repay", response_model=AnyResponse)
async def customer_repay(
    body: dict,
    merchant: Merchant = Depends(get_current_merchant),
    db: AsyncSession = Depends(get_db),
):
    """Record a customer repayment."""
    customer_name = (body.get("customer_name") or "").strip()
    amount = Decimal(str(body.get("amount", 0)))
    if not customer_name:
        raise HTTPException(status_code=400, detail="客户名称不能为空")
    if amount <= 0:
        raise HTTPException(status_code=400, detail="回款金额必须大于0")
    current_balance = await get_customer_balance(db, merchant.id, customer_name)
    if current_balance <= 0:
        raise HTTPException(status_code=400, detail="该客户当前没有欠款")
    if amount > current_balance:
        raise HTTPException(
            status_code=400, detail=f"回款金额不能超过当前欠款 ¥{float(current_balance):.2f}"
        )

    await record_customer_receivable(
        db,
        merchant_id=merchant.id,
        customer_name=customer_name,
        amount=amount,
        direction="repay",
        note=body.get("note", "手动回款"),
        idempotency_key=body.get("idempotency_key"),
    )
    await db.commit()
    new_balance = await get_customer_balance(db, merchant.id, customer_name)
    return {
        "code": 0,
        "message": f"{customer_name} 已回款 ¥{float(amount)}",
        "data": {"customer_name": customer_name, "new_balance": float(new_balance)},
    }


# ═══════════════════════════════════════════════════════════
# 客户信用档案管理 (section 4.8)
# ═══════════════════════════════════════════════════════════


@router.get("/customers/{customer_name}/credit-profile", response_model=AnyResponse)
async def get_customer_credit_profile(
    customer_name: str,
    merchant: Merchant = Depends(get_current_merchant),
    db: AsyncSession = Depends(get_db),
):
    """Get credit profile for a customer. Returns defaults if not configured."""
    profile = (
        await db.execute(
            select(CustomerCreditProfile).where(
                CustomerCreditProfile.merchant_id == merchant.id,
                CustomerCreditProfile.customer_name == customer_name,
            )
        )
    ).scalar_one_or_none()

    if not profile:
        return {
            "code": 0,
            "data": {
                "customer_name": customer_name,
                "credit_limit": None,
                "default_credit_days": None,
                "is_blocked": False,
                "block_reason": None,
                "notes": None,
                "is_default": True,
            },
        }

    return {
        "code": 0,
        "data": {
            "id": str(profile.id),
            "customer_name": profile.customer_name,
            "credit_limit": float(profile.credit_limit) if profile.credit_limit else None,
            "default_credit_days": profile.default_credit_days,
            "is_blocked": profile.is_blocked,
            "block_reason": profile.block_reason,
            "notes": profile.notes,
            "created_at": profile.created_at.isoformat() if profile.created_at else None,
        },
    }


@router.post("/customers/credit-profile", response_model=AnyResponse)
async def upsert_customer_credit_profile(
    body: dict,
    merchant: Merchant = Depends(get_current_merchant),
    db: AsyncSession = Depends(get_db),
):
    """Create or update a customer credit profile (§4.8).

    Body: {customer_name, credit_limit?, default_credit_days?, is_blocked?, block_reason?, notes?}
    """
    customer_name = (body.get("customer_name") or "").strip()
    if not customer_name:
        raise HTTPException(status_code=400, detail="客户名称不能为空")

    profile = (
        await db.execute(
            select(CustomerCreditProfile).where(
                CustomerCreditProfile.merchant_id == merchant.id,
                CustomerCreditProfile.customer_name == customer_name,
            )
        )
    ).scalar_one_or_none()

    if profile:
        # Update existing
        if "credit_limit" in body:
            profile.credit_limit = (
                Decimal(str(body["credit_limit"])) if body["credit_limit"] is not None else None
            )
        if "default_credit_days" in body:
            profile.default_credit_days = body["default_credit_days"]
        if "is_blocked" in body:
            profile.is_blocked = body["is_blocked"]
            if body["is_blocked"]:
                profile.block_reason = body.get("block_reason", "手动停赊")
            else:
                profile.block_reason = None
        if "notes" in body:
            profile.notes = body["notes"]
        action = "updated"
    else:
        profile = CustomerCreditProfile(
            merchant_id=merchant.id,
            customer_name=customer_name,
            credit_limit=(
                Decimal(str(body["credit_limit"])) if body.get("credit_limit") is not None else None
            ),
            default_credit_days=body.get("default_credit_days"),
            is_blocked=body.get("is_blocked", False),
            block_reason=body.get("block_reason") if body.get("is_blocked") else None,
            notes=body.get("notes"),
        )
        db.add(profile)
        action = "created"

    db.add(
        AuditLog(
            merchant_id=merchant.id,
            action=f"credit_profile_{action}",
            target_table="customer_credit_profiles",
            target_id=str(profile.id),
            after_data={
                "customer_name": customer_name,
                "credit_limit": float(profile.credit_limit) if profile.credit_limit else None,
                "is_blocked": profile.is_blocked,
            },
            operator="merchant",
        )
    )
    await db.commit()

    return {
        "code": 0,
        "message": f"客户 {customer_name} 信用档案已{action == 'created' and '创建' or '更新'}",
        "data": {"id": str(profile.id), "customer_name": customer_name},
    }


@router.post("/customers/check-credit", response_model=AnyResponse)
async def check_customer_credit(
    body: dict,
    merchant: Merchant = Depends(get_current_merchant),
    db: AsyncSession = Depends(get_db),
):
    """Check if a customer can make a credit purchase (§4.8 停止赊账).

    Body: {customer_name, amount}
    Returns: {allowed, reason, current_balance, credit_limit, remaining_credit}
    """
    customer_name = (body.get("customer_name") or "").strip()
    amount = Decimal(str(body.get("amount", 0)))
    if not customer_name:
        raise HTTPException(status_code=400, detail="客户名称不能为空")

    # Get current balance
    balance = await get_customer_balance(db, merchant.id, customer_name)

    # Get credit profile
    profile = (
        await db.execute(
            select(CustomerCreditProfile).where(
                CustomerCreditProfile.merchant_id == merchant.id,
                CustomerCreditProfile.customer_name == customer_name,
            )
        )
    ).scalar_one_or_none()

    # Check block
    if profile and profile.is_blocked:
        return {
            "code": 0,
            "data": {
                "allowed": False,
                "reason": profile.block_reason or "该客户已被停赊",
                "current_balance": float(balance),
                "credit_limit": float(profile.credit_limit) if profile.credit_limit else None,
                "remaining_credit": 0,
            },
        }

    # Check credit limit
    if profile and profile.credit_limit is not None:
        remaining = profile.credit_limit - balance
        if amount > remaining:
            return {
                "code": 0,
                "data": {
                    "allowed": False,
                    "reason": f"超出信用额度（剩余 ¥{float(remaining)}）",
                    "current_balance": float(balance),
                    "credit_limit": float(profile.credit_limit),
                    "remaining_credit": float(remaining),
                },
            }

    remaining_credit = None
    if profile and profile.credit_limit is not None:
        remaining_credit = float(profile.credit_limit - balance - amount)

    return {
        "code": 0,
        "data": {
            "allowed": True,
            "reason": None,
            "current_balance": float(balance),
            "credit_limit": float(profile.credit_limit)
            if profile and profile.credit_limit
            else None,
            "remaining_credit": remaining_credit,
        },
    }


# ═══════════════════════════════════════════════════════════
# 数据导出 (section 4.19)
# ═══════════════════════════════════════════════════════════


@router.get("/export/sales")
async def export_sales(
    start_date: date,
    end_date: date,
    merchant: Merchant = Depends(get_current_merchant),
    db: AsyncSession = Depends(get_db),
):
    """Export sales orders as CSV."""
    from datetime import time as dt_time

    day_start = datetime.combine(start_date, dt_time.min)
    day_end = datetime.combine(end_date, dt_time.max)

    orders = (
        (
            await db.execute(
                select(SaleOrder)
                .where(
                    SaleOrder.merchant_id == merchant.id,
                    SaleOrder.created_at >= day_start,
                    SaleOrder.created_at <= day_end,
                )
                .order_by(SaleOrder.created_at.asc())
            )
        )
        .scalars()
        .all()
    )

    output = io.StringIO()
    w = csv.writer(output)
    w.writerow(["订单号", "状态", "金额", "实付", "退款", "客户", "时间"])
    for o in orders:
        w.writerow(
            [
                o.order_no,
                o.status,
                float(o.total_amount),
                float(o.paid_amount or 0),
                float(o.refunded_amount or 0),
                o.customer_name or "",
                o.created_at.isoformat() if o.created_at else "",
            ]
        )

    output.seek(0)
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename=sales_{start_date}_{end_date}.csv"},
    )


@router.get("/export/inventory")
async def export_inventory(
    merchant: Merchant = Depends(get_current_merchant),
    db: AsyncSession = Depends(get_db),
):
    """Export current inventory as CSV."""
    from app.models.inventory import CurrentInventory

    rows = (
        (
            await db.execute(
                select(CurrentInventory).where(CurrentInventory.merchant_id == merchant.id)
            )
        )
        .scalars()
        .all()
    )

    product_ids = {r.product_id for r in rows}
    product_names = {}
    if product_ids:
        cats = (
            (await db.execute(select(ProductCategory).where(ProductCategory.id.in_(product_ids))))
            .scalars()
            .all()
        )
        product_names = {c.id: c.name for c in cats}

    output = io.StringIO()
    w = csv.writer(output)
    w.writerow(["商品", "当前库存", "平均成本"])
    for r in rows:
        w.writerow(
            [
                product_names.get(r.product_id, f"商品{r.product_id}"),
                float(r.current_qty),
                float(r.avg_cost) if r.avg_cost else "",
            ]
        )

    output.seek(0)
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=inventory.csv"},
    )


@router.get("/export/waste")
async def export_waste(
    start_date: date,
    end_date: date,
    merchant: Merchant = Depends(get_current_merchant),
    db: AsyncSession = Depends(get_db),
):
    """Export waste records as CSV."""
    from datetime import time as dt_time

    day_start = datetime.combine(start_date, dt_time.min)
    day_end = datetime.combine(end_date, dt_time.max)

    rows = (
        (
            await db.execute(
                select(InventoryRecord)
                .where(
                    InventoryRecord.merchant_id == merchant.id,
                    InventoryRecord.event_type == "waste",
                    InventoryRecord.event_time >= day_start,
                    InventoryRecord.event_time <= day_end,
                )
                .order_by(InventoryRecord.event_time.asc())
            )
        )
        .scalars()
        .all()
    )

    product_ids = {r.product_id for r in rows}
    product_names = {}
    if product_ids:
        cats = (
            (await db.execute(select(ProductCategory).where(ProductCategory.id.in_(product_ids))))
            .scalars()
            .all()
        )
        product_names = {c.id: c.name for c in cats}

    output = io.StringIO()
    w = csv.writer(output)
    w.writerow(["商品", "数量", "单位", "成本", "原因", "时间"])
    for r in rows:
        w.writerow(
            [
                product_names.get(r.product_id, f"商品{r.product_id}"),
                float(abs(r.quantity)),
                r.unit,
                float(r.total_amount) if r.total_amount else "",
                r.notes,
                r.event_time.isoformat() if r.event_time else "",
            ]
        )

    output.seek(0)
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename=waste_{start_date}_{end_date}.csv"},
    )


@router.get("/export/accounts")
async def export_accounts(
    merchant: Merchant = Depends(get_current_merchant),
    db: AsyncSession = Depends(get_db),
):
    """Export supplier and customer balances as CSV."""
    from app.services.accounts_service import list_customer_balances, list_supplier_balances

    sup_rows = await list_supplier_balances(db, merchant.id)
    cust_rows = await list_customer_balances(db, merchant.id)

    output = io.StringIO()
    w = csv.writer(output)

    w.writerow(["=== 供应商应付 ==="])
    w.writerow(["供应商", "当前欠款"])
    for r in sup_rows:
        w.writerow([r.get("name", r.get("supplier_id")), r["balance"]])

    w.writerow([])
    w.writerow(["=== 客户应收 ==="])
    w.writerow(["客户", "当前欠款"])
    for r in cust_rows:
        w.writerow([r["customer_name"], r["balance"]])

    output.seek(0)
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=accounts.csv"},
    )
