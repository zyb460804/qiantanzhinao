"""
Batch lifecycle tracking service.

Creates batch records on purchase, consumes them FIFO on sale/waste,
and powers the expiry / low-stock alert pipeline.

Keeps routers thin — all batch persistence and querying lives here.
"""

import logging
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import NotRequired, TypedDict

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.timezone import utc_now
from app.models.batch import BATCH_TRANSITIONS, BatchLifecycle
from app.models.inventory import InventoryRecord
from app.models.product import ProductCategory
from app.services.lifecycle import get_product_lifecycle


logger = logging.getLogger(__name__)


class BatchRollbackSummary(TypedDict):
    event_type: str
    batches_affected: int
    qty_adjusted: Decimal
    action: NotRequired[str]


class BatchConsumption(TypedDict):
    quantity: Decimal
    total_cost: Decimal
    missing_cost_quantity: Decimal


# Default shelf life when a product has no matching rule.
DEFAULT_SHELF_LIFE_HOURS = 72

# ── 锁序统一（第五轮 V2-C1）─────────────────────────────────────────────
# 所有会锁批次行的入口（consume / rollback / return / 采购退货缩减）历史上
# 分别以 FEFO 正序、purchase_date 倒序、无序（purchase 回滚分支）加锁：
# 并发事务对同一商品的批次集合以相反顺序获取行锁 → ABBA 死锁（PG16 双副本
# 互等；SQLite 忽略 FOR UPDATE 故测试不可见）。
# 统一约定：SELECT ... FOR UPDATE 一律 ORDER BY id ASC 加锁——id 是主键
# 全序，与业务字段无关，天然构成跨路径一致的加锁全序。
# 加锁顺序与业务分配方向正交：行锁全部获得后再在应用层按业务序迭代
# （consume 按 FEFO 正序；rollback/return 按 purchase_date+id 倒序，
# 即 reversed(升序)）。SQL 不再承载业务排序，业务语义保持不变。

# 排序键兜底时间（expiry/purchase_date 判空用；DB 约定一律存 naive UTC）。
_SORT_EPOCH = datetime(1, 1, 1)


def _as_naive(dt: datetime | None) -> datetime | None:
    """排序键用：aware → naive UTC，与「DB 时间列一律存 naive UTC」约定对齐。"""
    if dt is None or dt.tzinfo is None:
        return dt
    return dt.astimezone(UTC).replace(tzinfo=None)


def _fefo_key(batch: BatchLifecycle) -> tuple:
    """FEFO 业务序：最早到期优先（NULL 到期最后），purchase_date、id 升序兜底。

    与旧 SQL ``ORDER BY expiry_date.asc().nullslast(), purchase_date.asc(), id``
    完全同序——锁序改 id ASC 后由应用层重排，主序零变化。
    """
    expiry = _as_naive(batch.expiry_date)
    purchase = _as_naive(batch.purchase_date) or _SORT_EPOCH
    return (expiry is None, expiry or _SORT_EPOCH, purchase, batch.id)


def _purchase_key(batch: BatchLifecycle) -> tuple:
    """入库时间业务序：purchase_date、id 升序；reversed() 后即倒序（先进后出）。

    与旧 SQL ``ORDER BY purchase_date.desc(), id.desc()`` 完全同序。
    """
    return (_as_naive(batch.purchase_date) or _SORT_EPOCH, batch.id)


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
    db: AsyncSession,
    batch_id: uuid.UUID,
    merchant_id: uuid.UUID,
    reason: str,
    locked_by: str = "merchant",
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
    db: AsyncSession,
    batch_id: uuid.UUID,
    merchant_id: uuid.UUID,
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
    db: AsyncSession,
    batch_id: uuid.UUID,
    merchant_id: uuid.UUID,
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
    # Mark remaining stock as frozen so the ledger reflects the recall.
    # destroy_batch later writes the final waste record; this is the freeze audit trail.
    if batch.remaining_qty > 0:
        product = await db.get(ProductCategory, batch.product_id)
        unit = product.unit if product else "斤"
        db.add(
            InventoryRecord(
                merchant_id=merchant_id,
                product_id=batch.product_id,
                sku_id=batch.sku_id,
                quantity=-batch.remaining_qty,
                unit=unit,
                unit_cost=batch.unit_cost,
                total_amount=(batch.remaining_qty * (batch.unit_cost or Decimal("0"))),
                event_type="recall",
                event_time=utc_now(),
                source="food_safety",
                batch_label=batch.batch_label,
                notes=f"召回冻结: {reason}",
                idempotency_key=f"recall:{batch_id}",
            )
        )
    return batch


async def destroy_batch(
    db: AsyncSession,
    batch_id: uuid.UUID,
    merchant_id: uuid.UUID,
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
        product = await db.get(ProductCategory, batch.product_id)
        unit = product.unit if product else "斤"
        db.add(
            InventoryRecord(
                merchant_id=merchant_id,
                product_id=batch.product_id,
                sku_id=batch.sku_id,
                quantity=-batch.remaining_qty,
                unit=unit,
                unit_cost=batch.unit_cost,
                total_amount=(batch.remaining_qty * (batch.unit_cost or Decimal("0"))),
                event_type="waste",
                event_time=utc_now(),
                source="food_safety",
                batch_label=batch.batch_label,
                notes=f"销毁: {reason}",
                idempotency_key=f"destroy:{batch_id}",
            )
        )
    return batch


async def get_batch_trace_data(
    db: AsyncSession,
    batch_id: uuid.UUID,
    merchant_id: uuid.UUID,
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
    waste_records = (
        (
            await db.execute(
                select(InventoryRecord).where(
                    InventoryRecord.merchant_id == merchant_id,
                    InventoryRecord.product_id == batch.product_id,
                    InventoryRecord.batch_label == batch.batch_label,
                    InventoryRecord.event_type.in_(("waste", "refund")),
                )
            )
        )
        .scalars()
        .all()
    )

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
            {
                "event_type": r.event_type,
                "qty": float(r.quantity),
                "notes": r.notes,
                "time": r.event_time.isoformat() if r.event_time else None,
            }
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
    consumption = await consume_batches_fifo_costed(
        db,
        merchant_id,
        product_id,
        quantity,
        sku_id=sku_id,
    )
    return consumption["quantity"]


async def consume_batches_fifo_costed(
    db: AsyncSession,
    merchant_id: uuid.UUID,
    product_id: int,
    quantity: Decimal,
    sku_id: uuid.UUID | None = None,
) -> BatchConsumption:
    """Consume quantity from existing batches using FIFO (oldest first).

    If sku_id is provided, only batches carrying that SKU are consumed;
    otherwise falls back to product_id for backward compatibility with
    legacy data that has no SKU link. Returns actual FIFO cost where all
    consumed batches have a unit cost, and separately reports unknown-cost
    quantity for historical batches.
    """
    to_consume = abs(quantity)
    if to_consume <= 0:
        return {
            "quantity": Decimal("0"),
            "total_cost": Decimal("0"),
            "missing_cost_quantity": Decimal("0"),
        }

    filters = [
        BatchLifecycle.merchant_id == merchant_id,
        BatchLifecycle.remaining_qty > 0,
        # Food-safety whitelist: only sellable / near_expiry batches may be consumed.
        # Expired, locked, recalled, destroyed, removed, wasted, returned, pending_acceptance
        # are all excluded — this is the food safety red line.
        BatchLifecycle.status.in_(("sellable", "near_expiry")),
    ]
    if sku_id is not None:
        filters.append(BatchLifecycle.sku_id == sku_id)
    else:
        filters.append(BatchLifecycle.product_id == product_id)

    # FEFO (First-Expiry-First-Out): earliest expiry wins, NULL expiry last.
    # with_for_update() acquires row-level locks on PostgreSQL (SQLite ignores it silently).
    # 锁序统一：ORDER BY id 升序加锁（跨入口一致的加锁全序，防 ABBA），
    # FEFO 业务主序在锁内应用层重排（_fefo_key 与旧 SQL 排序完全同序）。
    query = (
        select(BatchLifecycle).where(*filters).order_by(BatchLifecycle.id.asc()).with_for_update()
    )
    result = await db.execute(query)
    batches = sorted(result.scalars().all(), key=_fefo_key)

    consumed = Decimal("0")
    total_cost = Decimal("0")
    missing_cost_quantity = Decimal("0")
    for batch in batches:
        if to_consume <= 0:
            break
        available = batch.remaining_qty
        take = min(available, to_consume)
        batch.remaining_qty = available - take
        to_consume -= take
        consumed += take
        if batch.unit_cost is None:
            missing_cost_quantity += take
        else:
            total_cost += take * batch.unit_cost

    if to_consume > 0:
        logger.info(
            "FIFO consume short for merchant=%s product=%s sku=%s: requested=%s consumed=%s",
            merchant_id,
            product_id,
            sku_id,
            abs(quantity),
            consumed,
        )

    return {
        "quantity": consumed.quantize(Decimal("0.01")),
        "total_cost": total_cost.quantize(Decimal("0.01")),
        "missing_cost_quantity": missing_cost_quantity.quantize(Decimal("0.01")),
    }


async def rollback_batch_on_void(
    db: AsyncSession,
    merchant_id: uuid.UUID,
    product_id: int,
    record: "InventoryRecord",
) -> BatchRollbackSummary:
    """Reverse the batch effect of a voided inventory record.

    For purchase voids: reduce or delete the batch created by this record.
    For sale/waste voids: restore remaining_qty to the batches that were consumed.

    Returns a summary dict for audit logging.
    """
    summary: BatchRollbackSummary = {
        "event_type": record.event_type,
        "batches_affected": 0,
        "qty_adjusted": Decimal("0"),
    }

    if record.event_type == "purchase":
        # Find the batch created by this purchase (match by batch_label + product + date proximity)
        # 行锁锚点：串行化同一批次的并发回滚（PG 生效；SQLite 静默忽略 FOR UPDATE）。
        # 锁序统一：ORDER BY id ASC（V2-C1）。
        query = (
            select(BatchLifecycle)
            .where(
                BatchLifecycle.merchant_id == merchant_id,
                BatchLifecycle.product_id == product_id,
                BatchLifecycle.batch_label == record.batch_label,
            )
            .order_by(BatchLifecycle.id.asc())
            .with_for_update()
        )
        result = await db.execute(query)
        # 同分钟同商品可落多行同 label 批次：scalar_one_or_none 会
        # MultipleResultsFound → 500。取 id 最小（最早）的一行，回滚口径不变。
        batch = result.scalars().first()

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
                batch.remaining_qty = Decimal("0")
                summary["batches_affected"] = 1
                summary["qty_adjusted"] = consumed - purchased_qty
                summary["action"] = "reduced"

    elif record.event_type in ("sale", "waste"):
        # Restore quantity to the most recently consumed batches (reverse FIFO)
        qty_to_restore = abs(record.quantity)
        # 行锁锚点：串行化并发撤销回滚，防止读-算-写竞态丢失一次回滚或产生幻影库存
        # （PG 生效；SQLite 静默忽略 FOR UPDATE）。
        # 锁序统一：ORDER BY id ASC 加锁（V2-C1）；业务方向（最新批次先回补）
        # 由应用层 reversed(升序) 实现——与旧 SQL purchase_date DESC, id DESC 同序。
        query = (
            select(BatchLifecycle)
            .where(
                BatchLifecycle.merchant_id == merchant_id,
                BatchLifecycle.product_id == product_id,
                BatchLifecycle.status.not_in(
                    ("wasted", "destroyed", "removed", "recalled", "returned")
                ),
            )
            .order_by(BatchLifecycle.id.asc())
            .with_for_update()
        )
        result = await db.execute(query)
        batches = sorted(result.scalars().all(), key=_purchase_key)

        for batch in reversed(batches):
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
        BatchLifecycle.status.not_in(("wasted", "destroyed", "removed", "recalled", "returned")),
        # Only target batches that had consumption (remaining < purchase)
        BatchLifecycle.remaining_qty < BatchLifecycle.purchase_qty,
    ]
    if sku_id is not None:
        filters.append(BatchLifecycle.sku_id == sku_id)
    else:
        filters.append(BatchLifecycle.product_id == product_id)

    # 行锁锚点：串行化并发退货回库对 remaining_qty 的读-改-写，防止丢更新
    # （PG 生效；SQLite 静默忽略 FOR UPDATE）。
    # 锁序统一：ORDER BY id ASC 加锁（V2-C1）；业务方向（最新批次先回库）
    # 由应用层 reversed(升序) 实现——与旧 SQL purchase_date DESC, id DESC 同序。
    query = (
        select(BatchLifecycle).where(*filters).order_by(BatchLifecycle.id.asc()).with_for_update()
    )
    result = await db.execute(query)
    batches = sorted(result.scalars().all(), key=_purchase_key)

    returned = Decimal("0")
    for batch in reversed(batches):
        if to_return <= 0:
            break
        consumed = batch.purchase_qty - batch.remaining_qty
        add = min(consumed, to_return)
        batch.remaining_qty += add
        to_return -= add
        returned += add

    if to_return > 0:
        # Surplus: returned qty exceeds total consumed across all batches.
        # Create a new sellable batch so the excess doesn't vanish from the ledger.
        product = await db.get(ProductCategory, product_id)
        product_name = product.name if product else f"product-{product_id}"
        await create_batch(
            db,
            merchant_id=merchant_id,
            product_id=product_id,
            product_name=product_name,
            batch_label=f"return-surplus-{utc_now().strftime('%Y%m%d%H%M%S')}",
            quantity=to_return,
            sku_id=sku_id,
        )
        logger.info(
            "Batch return exceeded consumption for merchant=%s product=%s sku=%s: "
            "returned=%s surplus_batch=%s",
            merchant_id,
            product_id,
            sku_id,
            returned,
            to_return,
        )
        returned += to_return
        to_return = Decimal("0")

    return returned.quantize(Decimal("0.01"))


async def reduce_batches_on_purchase_return(
    db: AsyncSession,
    merchant_id: uuid.UUID,
    product_id: int,
    quantity: Decimal,
    batch_label: str | None = None,
    sku_id: uuid.UUID | None = None,
) -> Decimal:
    """采购退货时同步缩减批次（第五轮 V2-H4）。

    修复前缺陷：退货只写负向 InventoryRecord（账面减少），批次 remaining_qty
    原地不动 → 批次仍按旧余量参与 FIFO 消耗，账面已减批次未减 → 超卖。

    口径与 rollback_batch_on_void 的 purchase 分支一致：
      - 先扣 remaining_qty（每批最多扣到 0），等量压降 purchase_qty；
      - 退货量超过批次现存余量时，缺口对应「已被消耗/售出」的部分，
        只压降 purchase_qty（历史入库量），保持 purchase_qty >= remaining_qty
        不变量——与 rollback purchase 分支「缩减至已消耗量」同语义。

    定位策略：优先按采购入库时写入的 batch_label 精确命中本采购项的批次；
    label 缺失/未命中（历史数据）时回退到该商品（或 SKU）现存可售批次。
    业务方向：FEFO（最早到期优先送还供应商）——与 consume 同向，
    锁序仍统一为 ORDER BY id ASC（V2-C1）。

    Returns: 实际从批次 remaining_qty 中移除的数量。
    """
    to_remove = abs(quantity)
    if to_remove <= 0:
        return Decimal("0")

    if batch_label:
        filters = [
            BatchLifecycle.merchant_id == merchant_id,
            BatchLifecycle.product_id == product_id,
            BatchLifecycle.batch_label == batch_label,
        ]
    else:
        # 回退：现存可售批次（与 consume 的可扣口径一致）。
        filters = [
            BatchLifecycle.merchant_id == merchant_id,
            BatchLifecycle.remaining_qty > 0,
            BatchLifecycle.status.in_(("sellable", "near_expiry")),
        ]
        if sku_id is not None:
            filters.append(BatchLifecycle.sku_id == sku_id)
        else:
            filters.append(BatchLifecycle.product_id == product_id)

    query = (
        select(BatchLifecycle).where(*filters).order_by(BatchLifecycle.id.asc()).with_for_update()
    )
    result = await db.execute(query)
    batches = sorted(result.scalars().all(), key=_fefo_key)

    if not batches and batch_label:
        # label 未命中（如批次已被撤销回滚删除）→ 回退到现存可售批次。
        return await reduce_batches_on_purchase_return(
            db, merchant_id, product_id, to_remove, batch_label=None, sku_id=sku_id
        )

    removed = Decimal("0")
    for batch in batches:
        if to_remove <= 0:
            break
        take = min(batch.remaining_qty, to_remove)
        if take > 0:
            batch.remaining_qty -= take
            batch.purchase_qty -= take
            to_remove -= take
            removed += take

    # 缺口：退货量超过现存批次余量，差额对应已售出部分（见 docstring 口径）。
    for batch in batches:
        if to_remove <= 0:
            break
        consumed = batch.purchase_qty - batch.remaining_qty
        take = min(max(consumed, Decimal("0")), to_remove)
        if take > 0:
            batch.purchase_qty -= take
            to_remove -= take

    if to_remove > 0:
        logger.warning(
            "purchase return exceeded batch capacity for merchant=%s product=%s "
            "label=%s: unabsorbed=%s",
            merchant_id,
            product_id,
            batch_label,
            to_remove,
        )

    return removed.quantize(Decimal("0.01"))


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
            BatchLifecycle.status.not_in(
                ("wasted", "destroyed", "removed", "recalled", "returned")
            ),
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
        BatchLifecycle.status.not_in(("wasted", "destroyed", "removed", "recalled", "returned")),
    )
    result = await db.execute(query)
    return int(result.scalar() or 0)
