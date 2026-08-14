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
from pydantic import BaseModel, Field
from sqlalchemy import case, func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.idempotency import short_idem_key
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
from app.schemas.common import AnyResponse, DecimalNum
from app.services.accounts_service import get_customer_balance, record_customer_receivable
from app.services.batch import consume_batches_fifo_costed


class ClearancePromotionRequest(BaseModel):
    promotion_price: Decimal = Field(gt=0)
    start_at: datetime | None = None
    end_at: datetime | None = None


class WasteRecordRequest(BaseModel):
    """报损请求 schema — 协议错误（非数字/缺字段/非法 UUID）→ 422。

    quantity 不加 gt=0：负数/零是业务语义错误，走路由显式校验返回
    400 中文报错（保持既有 API 契约），而非 Pydantic 的 422。
    """

    product_id: int
    sku_id: uuid.UUID | None = None
    quantity: DecimalNum
    unit: str | None = Field(default=None, max_length=20)
    reason: str = "其他"
    notes: str | None = Field(default=None, max_length=500)
    photos: str | None = Field(default=None, max_length=2000)
    idempotency_key: str | None = Field(default=None, max_length=64)


class CustomerRepayRequest(BaseModel):
    """客户回款请求 schema — 非数字金额/缺字段 → 422；负数/零金额 → 路由 400。"""

    customer_name: str = Field(max_length=50)
    amount: DecimalNum
    note: str | None = Field(default=None, max_length=500)
    idempotency_key: str | None = Field(default=None, max_length=64)


class CreditProfileUpsertRequest(BaseModel):
    """信用档案 upsert 请求 schema。

    负额度/负账期是业务语义错误 → 路由显式 400 中文报错（不用 ge=0，
    那会变成 422，破坏既有契约）。更新路径用 model_fields_set 区分
    「未传字段」与「显式传 false/null」，保持原 body:dict 部分更新语义。
    """

    customer_name: str = Field(max_length=50)
    credit_limit: DecimalNum | None = None
    default_credit_days: int | None = None
    is_blocked: bool = False
    block_reason: str | None = Field(default=None, max_length=200)
    notes: str | None = None


class CreditCheckRequest(BaseModel):
    """赊账信用检查请求 schema — 非数字金额/缺字段 → 422。"""

    customer_name: str = Field(max_length=50)
    amount: DecimalNum


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


async def _find_inventory_by_idempotency_key(
    db: AsyncSession, merchant_id: uuid.UUID, key: str
) -> InventoryRecord | None:
    """按幂等键回查库存流水（唯一约束 uq_inventory_idempotency_per_merchant）。"""
    return (
        await db.execute(
            select(InventoryRecord).where(
                InventoryRecord.merchant_id == merchant_id,
                InventoryRecord.idempotency_key == key,
            )
        )
    ).scalar_one_or_none()


def _waste_idempotent_replay(record: InventoryRecord) -> dict:
    """幂等键命中已存在流水：返回原记录语义（200），不重复扣库存、不重复审计。"""
    return {
        "code": 0,
        "message": "重复报损请求：幂等键已存在，已返回原报损记录（未重复扣库存）",
        "data": {"record_id": str(record.id), "consumed": float(abs(record.quantity))},
    }


@router.post("/waste", response_model=AnyResponse)
async def record_waste(
    body: WasteRecordRequest,
    merchant: Merchant = Depends(get_current_merchant),
    db: AsyncSession = Depends(get_db),
    _perm=Depends(require_permission("record_waste")),
):
    """Record waste/loss: deduct from FIFO batches, write inventory ledger.

    Body: {product_id, sku_id?, quantity, unit, reason, notes?, photos?}
    """
    product_id = body.product_id
    sku_id = body.sku_id
    quantity = body.quantity
    reason = body.reason
    notes = body.notes or ""
    photos = body.photos or ""
    # 自动幂等键：short_idem_key 压缩到 64 字符列宽内（V1-M4——原拼接
    # "waste:{uuid36}:{pid}:{ts}" 达 66 字符，PG 直接拒写 INSERT）。
    # 微秒级时戳保证同秒多次报损不互撞（秒级会吞掉同秒第二笔报损）。
    idempotency_key = body.idempotency_key or short_idem_key(
        "waste", merchant.id, product_id, int(utc_now().timestamp() * 1_000_000)
    )

    if photos:
        notes = f"{notes} [照片: {photos}]" if notes else f"[照片: {photos}]"

    if quantity <= 0:
        raise HTTPException(status_code=400, detail="报损数量必须大于0")
    if reason not in WASTE_REASONS:
        raise HTTPException(status_code=400, detail=f"无效报损原因: {reason}")

    # 幂等预检：客户端重试同 idempotency_key 时直接返回原记录，
    # 避免二次 FIFO 扣库存。并发竞态由唯一约束 + 下方 commit 捕获兜底。
    if body.idempotency_key:
        existing = await _find_inventory_by_idempotency_key(db, merchant.id, body.idempotency_key)
        if existing is not None:
            if existing.event_type != "waste":
                raise HTTPException(status_code=409, detail="幂等键已被其他库存流水占用，请更换")
            return _waste_idempotent_replay(existing)

    product = await db.get(ProductCategory, product_id)
    if not product:
        raise HTTPException(status_code=404, detail="商品不存在")

    # FIFO consume for waste（成本化：V1-M3——原 consume_batches_fifo 丢弃
    # total_cost，报损流水 unit_cost/total_amount 恒空，报损分析与导出的
    # waste_cost 恒 0。语义与 POS 销售路径一致：全部被消费批次均有成本时
    # 记加权均价与实际 FIFO 成本，任一批次缺成本则置 None（不猜成本））。
    consumption = await consume_batches_fifo_costed(
        db, merchant.id, product_id, quantity, sku_id=sku_id
    )
    consumed = consumption["quantity"]
    if consumed < quantity:
        raise HTTPException(
            status_code=409,
            detail=f"库存不足，报损需要{float(quantity)}{product.unit}，可用{float(consumed)}{product.unit}",
        )
    unit_cost = (
        (consumption["total_cost"] / consumed).quantize(Decimal("0.01"))
        if consumed > 0 and consumption["missing_cost_quantity"] == 0
        else None
    )

    record = InventoryRecord(
        merchant_id=merchant.id,
        product_id=product_id,
        sku_id=sku_id,
        quantity=-quantity,
        unit=body.unit or product.unit,
        unit_cost=unit_cost,
        total_amount=consumption["total_cost"] if unit_cost is not None else None,
        event_type="waste",
        event_time=utc_now(),
        source="manual",
        notes=f"{reason}: {notes}" if notes else reason,
        idempotency_key=idempotency_key,
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

    try:
        await db.commit()
    except IntegrityError as exc:
        # 并发重复提交同幂等键：唯一约束拦截（扣库存随事务一并回滚），
        # 回查原记录幂等返回，而不是向客户端抛 500。
        await db.rollback()
        existing = await _find_inventory_by_idempotency_key(db, merchant.id, idempotency_key)
        if existing is not None:
            if existing.event_type != "waste":
                raise HTTPException(
                    status_code=409, detail="幂等键已被其他库存流水占用，请更换"
                ) from exc
            return _waste_idempotent_replay(existing)
        raise
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
    _perm=Depends(require_permission("change_price")),
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
    """Get detailed ledger for a specific customer.

    总额/余额对该客户的全部流水全量聚合（与 get_customer_balance 同口径），
    items 明细只保留最近 limit 条——不能对分页截断后的子集求和，否则流水
    超过 limit 后余额失真，与 /accounts/customer-balance 各说各话。
    """
    totals = (
        await db.execute(
            select(
                func.coalesce(
                    func.sum(
                        case(
                            (
                                CustomerReceivable.direction == "charge",
                                CustomerReceivable.amount,
                            ),
                            else_=Decimal("0"),
                        )
                    ),
                    Decimal("0"),
                ),
                func.coalesce(
                    func.sum(
                        case(
                            (
                                CustomerReceivable.direction == "repay",
                                CustomerReceivable.amount,
                            ),
                            else_=Decimal("0"),
                        )
                    ),
                    Decimal("0"),
                ),
            ).where(
                CustomerReceivable.merchant_id == merchant.id,
                CustomerReceivable.customer_name == customer_name,
            )
        )
    ).one()
    total_charge = Decimal(str(totals[0] or 0))
    total_repay = Decimal(str(totals[1] or 0))

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

    items = []
    for r in rows:
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
    body: CustomerRepayRequest,
    merchant: Merchant = Depends(get_current_merchant),
    db: AsyncSession = Depends(get_db),
    _perm=Depends(require_permission("credit_sale")),
):
    """Record a customer repayment.

    并发与幂等（V2-H2，模式对齐供应商付款 accounts_service.record_supplier_payment）：

    - PG：``pg_advisory_xact_lock(hashtext('customer-repay:{merchant}:{name}'))``
      事务级串行化同商户同客户的并发回款——余额读与 amount<=balance 校验
      必须在锁内进行，否则两笔并发回款都读到同一余额、双双通过校验，
      余额被扣成负数。SQLite 单写者语义天然串行，跳过。
    - 幂等预检在锁后：并发同键回款串行化后，先到者先落库，后到者预检即
      命中重放（原实现预检缺位 + 键可为 NULL，网络重试会重复扣余额）。
    - 无客户端键时服务端生成非空确定性压缩键（short_idem_key）：NULL 不
      参与唯一约束比较，裸 NULL 无法兜底并发窗口。
    - commit 撞 (merchant_id, idempotency_key) 唯一约束（真正重叠的并发
      窗口）→ 回滚回查重放，不向客户端抛 500。
    """
    customer_name = (body.customer_name or "").strip()
    if not customer_name:
        raise HTTPException(status_code=400, detail="客户名称不能为空")
    amount = body.amount.quantize(Decimal("0.01"))
    if amount <= 0:
        raise HTTPException(status_code=400, detail="回款金额必须大于0")

    idempotency_key = body.idempotency_key or short_idem_key(
        "customer-repay", merchant.id, customer_name, int(utc_now().timestamp() * 1_000_000)
    )

    def _replay_response(new_balance: Decimal) -> dict:
        return {
            "code": 0,
            "message": "重复回款请求：幂等键已存在，已返回原回款结果（未重复扣减欠款）",
            "data": {"customer_name": customer_name, "new_balance": float(new_balance)},
        }

    async def _find_by_key(key: str) -> CustomerReceivable | None:
        return (
            await db.execute(
                select(CustomerReceivable).where(
                    CustomerReceivable.merchant_id == merchant.id,
                    CustomerReceivable.idempotency_key == key,
                )
            )
        ).scalar_one_or_none()

    # PG 事务级 advisory lock：串行化同商户同客户的并发回款（TOCTOU 防线）。
    _dialect = getattr(db.bind.dialect, "name", "") if db.bind is not None else ""
    if _dialect == "postgresql":
        await db.execute(
            text("SELECT pg_advisory_xact_lock(hashtext(:lock_key))"),
            {"lock_key": f"customer-repay:{merchant.id}:{customer_name}"},
        )

    # 幂等预检（锁后）：同键重试直接重放，不二次扣减余额。
    if body.idempotency_key:
        existing = await _find_by_key(body.idempotency_key)
        if existing is not None:
            if existing.direction != "repay":
                raise HTTPException(status_code=409, detail="幂等键已被其他客户流水占用，请更换")
            if existing.customer_name != customer_name or existing.amount != amount:
                raise HTTPException(status_code=409, detail="幂等键已用于另一笔回款")
            new_balance = await get_customer_balance(db, merchant.id, customer_name)
            return _replay_response(new_balance)

    # 余额校验（锁内重读：并发回款在锁上排队，后到者读到先到者提交后的余额）。
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
        note=body.note or "手动回款",
        idempotency_key=idempotency_key,
    )
    try:
        await db.commit()
    except IntegrityError as exc:
        # 重叠并发窗口兜底：同键第二笔回款撞唯一约束（扣减随事务回滚），
        # 回查重放而不是向客户端抛 500。
        await db.rollback()
        existing = await _find_by_key(idempotency_key)
        if existing is not None:
            if existing.direction != "repay":
                raise HTTPException(
                    status_code=409, detail="幂等键已被其他客户流水占用，请更换"
                ) from exc
            if existing.customer_name != customer_name or existing.amount != amount:
                raise HTTPException(status_code=409, detail="幂等键已用于另一笔回款") from exc
            new_balance = await get_customer_balance(db, merchant.id, customer_name)
            return _replay_response(new_balance)
        raise
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
    body: CreditProfileUpsertRequest,
    merchant: Merchant = Depends(get_current_merchant),
    db: AsyncSession = Depends(get_db),
    _perm=Depends(require_permission("credit_sale")),
):
    """Create or update a customer credit profile (§4.8).

    Body: {customer_name, credit_limit?, default_credit_days?, is_blocked?, block_reason?, notes?}
    """
    customer_name = (body.customer_name or "").strip()
    if not customer_name:
        raise HTTPException(status_code=400, detail="客户名称不能为空")

    # 业务校验：负额度/负账期直接拒绝（负账期会让所有欠款永远「未逾期」）。
    if body.credit_limit is not None and body.credit_limit < 0:
        raise HTTPException(status_code=400, detail="信用额度不能为负数")
    if body.default_credit_days is not None and body.default_credit_days < 0:
        raise HTTPException(status_code=400, detail="默认账期天数不能为负数")

    profile = (
        await db.execute(
            select(CustomerCreditProfile).where(
                CustomerCreditProfile.merchant_id == merchant.id,
                CustomerCreditProfile.customer_name == customer_name,
            )
        )
    ).scalar_one_or_none()

    # model_fields_set = 请求里显式出现的字段，等价于原 body:dict 的
    # `if "x" in body` 部分更新语义：未传的字段保持原值不动。
    fields = body.model_fields_set
    if profile:
        # Update existing
        if "credit_limit" in fields:
            profile.credit_limit = body.credit_limit
        if "default_credit_days" in fields:
            profile.default_credit_days = body.default_credit_days
        if "is_blocked" in fields:
            profile.is_blocked = body.is_blocked
            if body.is_blocked:
                profile.block_reason = body.block_reason or "手动停赊"
            else:
                profile.block_reason = None
        if "notes" in fields:
            profile.notes = body.notes
        action = "updated"
    else:
        profile = CustomerCreditProfile(
            merchant_id=merchant.id,
            customer_name=customer_name,
            credit_limit=body.credit_limit,
            default_credit_days=body.default_credit_days,
            is_blocked=body.is_blocked,
            block_reason=body.block_reason if body.is_blocked else None,
            notes=body.notes,
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
    body: CreditCheckRequest,
    merchant: Merchant = Depends(get_current_merchant),
    db: AsyncSession = Depends(get_db),
):
    """Check if a customer can make a credit purchase (§4.8 停止赊账).

    Body: {customer_name, amount}
    Returns: {allowed, reason, current_balance, credit_limit, remaining_credit}
    """
    customer_name = (body.customer_name or "").strip()
    amount = body.amount
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

    credit_limit = float(profile.credit_limit) if profile and profile.credit_limit else None

    # 负/零金额无业务意义：显式拒绝。否则 remaining_credit 会随负金额反向
    # 增加，信用校验被绕过（allowed 恒真），任何负数金额都能「赊」。
    if amount <= 0:
        return {
            "code": 0,
            "data": {
                "allowed": False,
                "reason": "金额必须大于0",
                "current_balance": float(balance),
                "credit_limit": credit_limit,
                "remaining_credit": 0,
            },
        }

    # Check block
    if profile and profile.is_blocked:
        return {
            "code": 0,
            "data": {
                "allowed": False,
                "reason": profile.block_reason or "该客户已被停赊",
                "current_balance": float(balance),
                "credit_limit": credit_limit,
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
            "credit_limit": credit_limit,
            "remaining_credit": remaining_credit,
        },
    }


# ═══════════════════════════════════════════════════════════
# 数据导出 (section 4.19)
# ═══════════════════════════════════════════════════════════


def _sanitize_csv_cell(v) -> str:
    """净化 CSV 单元格防公式注入：= / + / - / @ / \\t / \\r 前缀加单引号。

    CWE-1236：Excel/WPS 打开 CSV 时会执行以 =/+/-/@ 开头的公式，
    恶意 customer_name 如 =CMD(...) 可借此执行命令。
    在写入前对危险前缀加单引号转义，读取时由 Excel 自动忽略。

    负数（- 后紧跟数字或小数点）是合法业务数据，不转义。
    """
    s = str(v) if v is not None else ""
    if not s:
        return s
    # 负数（-50.0 / -.5）是合法数值，不转义
    if s[0] == "-" and len(s) > 1 and (s[1].isdigit() or s[1] == "."):
        return s
    if s[0] in ("=", "+", "-", "@", "\t", "\r"):
        return "'" + s
    return s


def _rows_to_csv(headers: list[str], rows: list[list]) -> str:
    """生成 UTF-8 BOM CSV 字符串，供前端直接写文件分享。

    安全：每个单元格均经 _sanitize_csv_cell 净化，防 CSV 公式注入。
    """
    output = io.StringIO()
    w = csv.writer(output)
    w.writerow(headers)
    for row in rows:
        w.writerow([_sanitize_csv_cell(c) for c in row])
    return "﻿" + output.getvalue()


@router.get("/export/sales")
async def export_sales(
    start_date: date,
    end_date: date,
    merchant: Merchant = Depends(get_current_merchant),
    db: AsyncSession = Depends(get_db),
):
    """Export sales orders as JSON envelope {code:0, data:{rows, csv, filename}}.

    P0 修复：原返回 StreamingResponse CSV，前端 app.request 期待 {code:0} JSON
    信封，CSV 字符串落入 business_error 分支永远失败。改为 JSON 信封后前端
    可直接读 data.csv 落盘分享。
    """
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

    headers = ["订单号", "状态", "金额", "实付", "退款", "客户", "时间"]
    rows = [
        {
            "订单号": o.order_no,
            "状态": o.status,
            "金额": float(o.total_amount),
            "实付": float(o.paid_amount or 0),
            "退款": float(o.refunded_amount or 0),
            "客户": o.customer_name or "",
            "时间": o.created_at.isoformat() if o.created_at else "",
        }
        for o in orders
    ]
    return {
        "code": 0,
        "data": {
            "rows": rows,
            "csv": _rows_to_csv(headers, [[r[h] for h in headers] for r in rows]),
            "filename": f"sales_{start_date}_{end_date}.csv",
        },
    }


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

    headers = ["商品", "当前库存", "平均成本"]
    inv_rows = [
        {
            "商品": product_names.get(r.product_id, f"商品{r.product_id}"),
            "当前库存": float(r.current_qty),
            "平均成本": float(r.avg_cost) if r.avg_cost else "",
        }
        for r in rows
    ]
    return {
        "code": 0,
        "data": {
            "rows": inv_rows,
            "csv": _rows_to_csv(headers, [[r[h] for h in headers] for r in inv_rows]),
            "filename": "inventory.csv",
        },
    }


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

    headers = ["商品", "数量", "单位", "成本", "原因", "时间"]
    waste_rows = [
        {
            "商品": product_names.get(r.product_id, f"商品{r.product_id}"),
            "数量": float(abs(r.quantity)),
            "单位": r.unit,
            "成本": float(r.total_amount) if r.total_amount else "",
            "原因": r.notes,
            "时间": r.event_time.isoformat() if r.event_time else "",
        }
        for r in rows
    ]
    return {
        "code": 0,
        "data": {
            "rows": waste_rows,
            "csv": _rows_to_csv(headers, [[r[h] for h in headers] for r in waste_rows]),
            "filename": f"waste_{start_date}_{end_date}.csv",
        },
    }


@router.get("/export/accounts")
async def export_accounts(
    merchant: Merchant = Depends(get_current_merchant),
    db: AsyncSession = Depends(get_db),
):
    """Export supplier and customer balances as JSON envelope {code:0, data:{rows, csv, filename}}.

    与 /export/sales 等保持一致：前端 app.request 期待 {code:0} JSON 信封，
    原始 CSV 流会让小程序端 JSON.parse 失败（parse_error）。
    """
    from app.services.accounts_service import list_customer_balances, list_supplier_balances

    sup_rows = await list_supplier_balances(db, merchant.id)
    cust_rows = await list_customer_balances(db, merchant.id)

    rows = [
        {
            "类型": "供应商应付",
            "往来对象": r.get("name", r.get("supplier_id")),
            "当前欠款": float(r["balance"]),
        }
        for r in sup_rows
    ]
    rows += [
        {
            "类型": "客户应收",
            "往来对象": r["customer_name"],
            "当前欠款": float(r["balance"]),
        }
        for r in cust_rows
    ]

    headers = ["类型", "往来对象", "当前欠款"]
    return {
        "code": 0,
        "data": {
            "rows": rows,
            "csv": _rows_to_csv(headers, [[r[h] for h in headers] for r in rows]),
            "filename": "accounts.csv",
        },
    }
