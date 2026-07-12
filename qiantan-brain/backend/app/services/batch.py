"""
Batch lifecycle tracking service.

Creates batch records on purchase, consumes them FIFO on sale/waste,
and powers the expiry / low-stock alert pipeline.

Keeps routers thin — all batch persistence and querying lives here.
"""

import logging
import uuid
from datetime import datetime, timedelta
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.timezone import utc_now
from app.models.batch import BATCH_TRANSITIONS, BatchLifecycle
from app.models.inventory import InventoryRecord
from app.services.lifecycle import get_product_lifecycle


logger = logging.getLogger(__name__)

# Default shelf life when a product has no matching rule.
DEFAULT_SHELF_LIFE_HOURS = 72


async def create_batch(
    db: AsyncSession,
    merchant_id: uuid.UUID,
    product_id: int,
    product_name: str,
    batch_label: str,
    quantity: Decimal,
    purchase_time: datetime | None = None,
    sku_id: uuid.UUID | None = None,
    *,
    supplier_id: uuid.UUID | None = None,
    supplier_name: str | None = None,
    origin: str | None = None,
    unit_cost: Decimal | None = None,
    certificates: str | None = None,
) -> BatchLifecycle:
    """Create a new batch with full traceability data (section 4.13)."""
    purchase_time = purchase_time or utc_now()
    lifecycle = get_product_lifecycle(product_name)
    shelf_life_hours = (
        lifecycle.get("shelf_life_hours", DEFAULT_SHELF_LIFE_HOURS)
        if lifecycle
        else DEFAULT_SHELF_LIFE_HOURS
    )
    expiry = purchase_time + timedelta(hours=shelf_life_hours)

    batch = BatchLifecycle(
        merchant_id=merchant_id,
        product_id=product_id,
        sku_id=sku_id,
        batch_label=batch_label,
        purchase_date=purchase_time,
        purchase_qty=abs(quantity),
        remaining_qty=abs(quantity),
        expiry_date=expiry,
        status="sellable",
        supplier_id=supplier_id,
        supplier_name=supplier_name,
        origin=origin,
        unit_cost=unit_cost,
        certificates=certificates,
    )
    db.add(batch)
    return batch


async def lock_batch(
    db: AsyncSession, batch_id: uuid.UUID, merchant_id: uuid.UUID,
    reason: str, locked_by: str = "merchant",
) -> BatchLifecycle:
    """Lock a batch (food safety failure). POS will skip this batch."""
    batch = await db.get(BatchLifecycle, batch_id)
    if not batch or batch.merchant_id != merchant_id:
        raise ValueError("批次不存在")
    valid_targets = BATCH_TRANSITIONS.get(batch.status, set())
    if "locked" not in valid_targets:
        raise ValueError(f"当前状态 {batch.status} 不允许锁定")
    batch.status = "locked"
    batch.locked_at = utc_now()
    batch.locked_reason = reason
    batch.locked_by = locked_by
    return batch


async def unlock_batch(
    db: AsyncSession, batch_id: uuid.UUID, merchant_id: uuid.UUID,
) -> BatchLifecycle:
    """Unlock a batch (re-check passed)."""
    batch = await db.get(BatchLifecycle, batch_id)
    if not batch or batch.merchant_id != merchant_id:
        raise ValueError("批次不存在")
    if batch.status != "locked":
        raise ValueError(f"当前状态 {batch.status} 不允许解锁")
    batch.status = "sellable"
    batch.locked_reason = None
    return batch


async def recall_batch(
    db: AsyncSession, batch_id: uuid.UUID, merchant_id: uuid.UUID,
    reason: str,
) -> BatchLifecycle:
    """Recall a locked batch — the goods are being pulled from sale."""
    batch = await db.get(BatchLifecycle, batch_id)
    if not batch or batch.merchant_id != merchant_id:
        raise ValueError("批次不存在")
    valid_targets = BATCH_TRANSITIONS.get(batch.status, set())
    if "recalled" not in valid_targets:
        raise ValueError(f"当前状态 {batch.status} 不允许召回")
    batch.status = "recalled"
    return batch


async def destroy_batch(
    db: AsyncSession, batch_id: uuid.UUID, merchant_id: uuid.UUID,
    reason: str,
) -> BatchLifecycle:
    """Destroy a recalled batch — final disposal."""
    batch = await db.get(BatchLifecycle, batch_id)
    if not batch or batch.merchant_id != merchant_id:
        raise ValueError("批次不存在")
    valid_targets = BATCH_TRANSITIONS.get(batch.status, set())
    if "destroyed" not in valid_targets:
        raise ValueError(f"当前状态 {batch.status} 不允许销毁")
    batch.status = "destroyed"
    batch.destroyed_at = utc_now()
    batch.destroyed_reason = reason
    # Record as waste in inventory
    if batch.remaining_qty > 0:
        db.add(InventoryRecord(
            merchant_id=merchant_id, product_id=batch.product_id,
            sku_id=batch.sku_id, quantity=-batch.remaining_qty,
            unit="斤", unit_cost=batch.unit_cost,
            total_amount=(batch.remaining_qty * (batch.unit_cost or Decimal("0"))),
            event_type="waste", event_time=utc_now(), source="food_safety",
            batch_label=batch.batch_label,
            notes=f"销毁: {reason}",
            idempotency_key=f"destroy:{batch_id}",
        ))
    return batch


async def get_batch_trace_data(
    db: AsyncSession, batch_id: uuid.UUID, merchant_id: uuid.UUID,
) -> dict | None:
    """Generate full traceability data for a batch (QR code content)."""
    batch = await db.get(BatchLifecycle, batch_id)
    if not batch or batch.merchant_id != merchant_id:
        return None

    # Get related sale orders
    sale_order_ids = []
    if batch.sale_orders:
        try:
            import json
            sale_order_ids = json.loads(batch.sale_orders)
        except Exception:
            pass

    # Get waste records
    waste_records = (await db.execute(
        select(InventoryRecord).where(
            InventoryRecord.merchant_id == merchant_id,
            InventoryRecord.product_id == batch.product_id,
            InventoryRecord.batch_label == batch.batch_label,
            InventoryRecord.event_type.in_(("waste", "refund")),
        )
    )).scalars().all()

    return {
        "batch_id": str(batch.id),
        "batch_label": batch.batch_label,
        "product_id": batch.product_id,
        "sku_id": str(batch.sku_id) if batch.sku_id else None,
        "supplier": batch.supplier_name,
        "origin": batch.origin,
        "purchase_date": batch.purchase_date.isoformat() if batch.purchase_date else None,
        "original_qty": float(batch.purchase_qty),
        "remaining_qty": float(batch.remaining_qty),
        "unit_cost": float(batch.unit_cost) if batch.unit_cost else None,
        "expiry_date": batch.expiry_date.isoformat() if batch.expiry_date else None,
        "status": batch.status,
        "certificates": batch.certificates,
        "inspection_result": batch.inspection_result,
        "locked_reason": batch.locked_reason,
        "sale_orders": sale_order_ids,
        "waste_records": [
            {"event_type": r.event_type, "qty": float(r.quantity), "notes": r.notes,
             "time": r.event_time.isoformat() if r.event_time else None}
            for r in waste_records
        ],
    }


async def consume_batches_fifo(
    db: AsyncSession,
    merchant_id: uuid.UUID,
    product_id: int,
    quantity: Decimal,
    sku_id: uuid.UUID | None = None,
) -> Decimal:
    """Consume quantity from existing batches using FIFO (oldest first).

    If sku_id is provided, only batches carrying that SKU are consumed;
    otherwise falls back to product_id for backward compatibility with
    legacy data that has no SKU link.
    """
    to_consume = abs(quantity)
    if to_consume <= 0:
        return Decimal("0")

    filters = [
        BatchLifecycle.merchant_id == merchant_id,
        BatchLifecycle.remaining_qty > 0,
        BatchLifecycle.status.not_in(("spoiled", "locked", "recalled", "destroyed", "removed")),
    ]
    if sku_id is not None:
        filters.append(BatchLifecycle.sku_id == sku_id)
    else:
        filters.append(BatchLifecycle.product_id == product_id)

    query = select(BatchLifecycle).where(*filters).order_by(BatchLifecycle.purchase_date.asc())
    result = await db.execute(query)
    batches = result.scalars().all()

    consumed = Decimal("0")
    for batch in batches:
        if to_consume <= 0:
            break
        available = batch.remaining_qty
        take = min(available, to_consume)
        batch.remaining_qty = available - take
        to_consume -= take
        consumed += take

    if to_consume > 0:
        logger.info(
            "FIFO consume short for merchant=%s product=%s sku=%s: requested=%s consumed=%s",
            merchant_id,
            product_id,
            sku_id,
            abs(quantity),
            consumed,
        )

    return consumed.quantize(Decimal("0.01"))


async def rollback_batch_on_void(
    db: AsyncSession,
    merchant_id: uuid.UUID,
    product_id: int,
    record: "InventoryRecord",
) -> dict:
    """Reverse the batch effect of a voided inventory record.

    For purchase voids: reduce or delete the batch created by this record.
    For sale/waste voids: restore remaining_qty to the batches that were consumed.

    Returns a summary dict for audit logging.
    """
    summary = {"event_type": record.event_type, "batches_affected": 0, "qty_adjusted": Decimal("0")}

    if record.event_type == "purchase":
        # Find the batch created by this purchase (match by batch_label + product + date proximity)
        query = select(BatchLifecycle).where(
            BatchLifecycle.merchant_id == merchant_id,
            BatchLifecycle.product_id == product_id,
            BatchLifecycle.batch_label == record.batch_label,
        )
        result = await db.execute(query)
        batch = result.scalar_one_or_none()

        if batch:
            purchased_qty = batch.purchase_qty
            remaining = batch.remaining_qty
            consumed = purchased_qty - remaining

            if consumed <= 0:
                # Nothing was consumed from this batch — delete it entirely
                await db.delete(batch)
                summary["batches_affected"] = 1
                summary["qty_adjusted"] = -purchased_qty
                summary["action"] = "deleted"
            else:
                # Part of this batch was already consumed — reduce to consumed amount
                batch.purchase_qty = consumed
                batch.remaining_qty = 0
                summary["batches_affected"] = 1
                summary["qty_adjusted"] = consumed - purchased_qty
                summary["action"] = "reduced"

    elif record.event_type in ("sale", "waste"):
        # Restore quantity to the most recently consumed batches (reverse FIFO)
        qty_to_restore = abs(record.quantity)
        query = (
            select(BatchLifecycle)
            .where(
                BatchLifecycle.merchant_id == merchant_id,
                BatchLifecycle.product_id == product_id,
                BatchLifecycle.status != "spoiled",
            )
            .order_by(BatchLifecycle.purchase_date.desc())  # newest first (reverse FIFO)
        )
        result = await db.execute(query)
        batches = result.scalars().all()

        for batch in batches:
            if qty_to_restore <= 0:
                break
            batch_remaining = batch.remaining_qty
            batch_purchased = batch.purchase_qty
            # How much was consumed from this batch
            consumed_from_this = batch_purchased - batch_remaining
            # Restore up to what was consumed
            restore = min(consumed_from_this, qty_to_restore)
            if restore > 0:
                batch.remaining_qty = batch_remaining + restore
                qty_to_restore -= restore
                summary["batches_affected"] += 1
                summary["qty_adjusted"] += restore

        summary["action"] = "restored"

    return summary


async def return_to_batches(
    db: AsyncSession,
    merchant_id: uuid.UUID,
    product_id: int,
    quantity: Decimal,
    sku_id: uuid.UUID | None = None,
) -> Decimal:
    """Return refunded quantity to the most recently consumed batches.

    Unlike rollback (which is for void/correction), this is for legitimate
    customer returns where goods go back to sellable stock. We add to the
    newest batches first (reverse-FIFO) because returned goods were
    most likely sold from those.
    """
    to_return = abs(quantity)
    if to_return <= 0:
        return Decimal("0")

    filters = [
        BatchLifecycle.merchant_id == merchant_id,
        BatchLifecycle.status != "spoiled",
        # Only target batches that had consumption (remaining < purchase)
        BatchLifecycle.remaining_qty < BatchLifecycle.purchase_qty,
    ]
    if sku_id is not None:
        filters.append(BatchLifecycle.sku_id == sku_id)
    else:
        filters.append(BatchLifecycle.product_id == product_id)

    query = (
        select(BatchLifecycle)
        .where(*filters)
        .order_by(BatchLifecycle.purchase_date.desc())  # newest first
    )
    result = await db.execute(query)
    batches = result.scalars().all()

    returned = Decimal("0")
    for batch in batches:
        if to_return <= 0:
            break
        consumed = batch.purchase_qty - batch.remaining_qty
        add = min(consumed, to_return)
        batch.remaining_qty += add
        to_return -= add
        returned += add

    if to_return > 0:
        logger.info(
            "Batch return exceeded consumption for merchant=%s product=%s sku=%s: "
            "returned=%s remainder=%s (creates overage)",
            merchant_id, product_id, sku_id, returned, to_return,
        )

    return returned.quantize(Decimal("0.01"))


async def get_active_batches(
    db: AsyncSession,
    merchant_id: uuid.UUID,
) -> list[BatchLifecycle]:
    """Return all batches with remaining stock, soonest-expiring first."""
    query = (
        select(BatchLifecycle)
        .where(
            BatchLifecycle.merchant_id == merchant_id,
            BatchLifecycle.remaining_qty > 0,
            BatchLifecycle.status != "spoiled",
        )
        .order_by(BatchLifecycle.expiry_date.asc())
    )
    result = await db.execute(query)
    return list(result.scalars().all())


async def count_expiring_batches(
    db: AsyncSession,
    merchant_id: uuid.UUID,
    within_hours: int = 24,
) -> int:
    """Count batches that will expire within ``within_hours`` from now."""
    now = utc_now()
    threshold = now + timedelta(hours=within_hours)
    query = select(func.count(BatchLifecycle.id)).where(
        BatchLifecycle.merchant_id == merchant_id,
        BatchLifecycle.remaining_qty > 0,
        BatchLifecycle.expiry_date.isnot(None),
        BatchLifecycle.expiry_date <= threshold,
        BatchLifecycle.status != "spoiled",
    )
    result = await db.execute(query)
    return int(result.scalar() or 0)
