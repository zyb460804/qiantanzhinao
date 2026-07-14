"""往来账 service — 把供应商应付 / 客户应收从模型变成真实流水。

设计原则（见 models/accounts.py）：
- 余额不维护可变字段，只追加流水；当前余额 = SUM(增加) - SUM(减少)。
- 所有金额用 Decimal，避免 float 累加误差（红线 #7）。
- 带 idempotency_key，防止网络重试重复入账（红线 #4）。
- 采购确认产生应付；语音赊销/回款产生应收/还款。
"""

from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal

from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.accounts import CustomerReceivable, SupplierPayable
from app.models.purchase import PurchaseItem, PurchaseList


# ---------------------------------------------------------------------------
# Supplier payable (应付账款)
# ---------------------------------------------------------------------------


async def record_supplier_payable_from_purchase(
    db: AsyncSession,
    plist: PurchaseList,
    item: PurchaseItem,
    *,
    idempotency_key: str | None = None,
) -> SupplierPayable | None:
    """根据采购清单/条目生成供应商应付流水。

    规则：
      - payment_status == "paid"：已付清，不产生应付。
      - "unpaid" / "credit"：全额记 direction=purchase 的应付。
      - "partial"：记剩余未付部分（实际成本 - 已分摊支付额）。
        由于 PurchaseList.paid_amount 是整单维度，按 item.actual_cost
        比例分摊到该 item 后，差额即为该 item 产生的应付。
    """
    if not item.supplier_id:
        return None

    status = (plist.payment_status or "unpaid").lower()
    if status == "paid":
        return None

    actual_cost = _to_decimal(item.actual_cost)
    if actual_cost is None or actual_cost <= 0:
        return None

    if status == "partial":
        # 整单已付金额按各 item 实际成本比例分摊
        total_actual = _to_decimal(plist.total_actual_cost)
        paid = _to_decimal(plist.paid_amount)
        if total_actual and total_actual > 0 and paid and paid > 0:
            ratio = paid / total_actual
            already_paid = (actual_cost * ratio).quantize(Decimal("0.01"))
            amount = actual_cost - already_paid
        else:
            amount = actual_cost
    else:
        amount = actual_cost

    if amount <= 0:
        return None

    payable = SupplierPayable(
        merchant_id=plist.merchant_id,
        supplier_id=item.supplier_id,
        direction="purchase",
        amount=amount,
        purchase_list_id=plist.id,
        note=f"采购入库 {item.product_id} x{item.actual_qty}",
        settled=False,
        idempotency_key=idempotency_key,
    )
    db.add(payable)
    return payable


async def record_supplier_payment(
    db: AsyncSession,
    *,
    merchant_id: uuid.UUID,
    supplier_id: uuid.UUID,
    payable_ids: list[uuid.UUID],
    amount: Decimal,
    note: str | None = None,
    idempotency_key: str | None = None,
) -> SupplierPayable:
    """按指定 purchase payable 逐笔核销一笔供应商付款。"""
    amount = amount.quantize(Decimal("0.01"))
    if amount <= 0:
        raise ValueError("付款金额必须大于等于 0.01")
    if not payable_ids:
        raise ValueError("付款必须选择应付账款")
    if len(payable_ids) != len(set(payable_ids)):
        raise ValueError("应付账款不能重复选择")

    if idempotency_key:
        existing = (
            await db.execute(
                select(SupplierPayable).where(
                    SupplierPayable.merchant_id == merchant_id,
                    SupplierPayable.idempotency_key == idempotency_key,
                    SupplierPayable.direction == "payment",
                )
            )
        ).scalar_one_or_none()
        if existing:
            if existing.supplier_id != supplier_id or existing.amount != amount:
                raise ValueError("幂等键已用于另一笔付款")
            return existing

    rows = (
        (
            await db.execute(
                select(SupplierPayable)
                .where(
                    SupplierPayable.merchant_id == merchant_id,
                    SupplierPayable.supplier_id == supplier_id,
                    SupplierPayable.id.in_(payable_ids),
                    SupplierPayable.direction == "purchase",
                )
                .order_by(SupplierPayable.created_at, SupplierPayable.id)
                .with_for_update()
            )
        )
        .scalars()
        .all()
    )
    if len(rows) != len(set(payable_ids)):
        raise ValueError("存在不属于该供应商或不存在的应付账款")

    remaining_total = sum(
        (row.amount - (row.settled_amount or Decimal("0")) for row in rows), Decimal("0")
    )
    if amount > remaining_total:
        raise ValueError("付款金额不能超过所选应付余额")
    # 退货抵扣等历史 payment 流水可能尚未分摊到 settled_amount；同时校验
    # 供应商净余额，防止选中应付看似有余额但实际已被退货抵扣后再次超付。
    supplier_balance = await get_supplier_balance(db, merchant_id, supplier_id)
    if amount > supplier_balance:
        raise ValueError("付款金额不能超过供应商当前应付净余额")

    payment = SupplierPayable(
        merchant_id=merchant_id,
        supplier_id=supplier_id,
        direction="payment",
        amount=amount,
        note=note or "付款",
        settled=True,
        settled_amount=Decimal("0"),
        idempotency_key=idempotency_key,
    )
    db.add(payment)

    remaining_payment = amount
    affected_lists: set[uuid.UUID] = set()
    for row in rows:
        if remaining_payment <= 0:
            break
        row_remaining = row.amount - (row.settled_amount or Decimal("0"))
        applied = min(row_remaining, remaining_payment)
        row.settled_amount = (row.settled_amount or Decimal("0")) + applied
        row.settled = row.settled_amount >= row.amount
        remaining_payment -= applied
        if row.purchase_list_id:
            affected_lists.add(row.purchase_list_id)

    # 采购单付款状态与实际核销金额同步，避免仅依赖供应商总余额推断。
    for list_id in affected_lists:
        plist = await db.get(PurchaseList, list_id)
        if not plist:
            continue
        list_rows = (
            (
                await db.execute(
                    select(SupplierPayable).where(
                        SupplierPayable.merchant_id == merchant_id,
                        SupplierPayable.purchase_list_id == list_id,
                        SupplierPayable.direction == "purchase",
                    )
                )
            )
            .scalars()
            .all()
        )
        remaining = sum(
            (r.amount - (r.settled_amount or Decimal("0")) for r in list_rows), Decimal("0")
        )
        total_actual = plist.total_actual_cost or sum((r.amount for r in list_rows), Decimal("0"))
        paid = max(Decimal("0"), min(total_actual, total_actual - remaining))
        plist.paid_amount = paid
        plist.payment_status = (
            "paid" if remaining <= 0 and total_actual > 0 else ("partial" if paid > 0 else "unpaid")
        )

    return payment


async def get_supplier_balance(
    db: AsyncSession,
    merchant_id: uuid.UUID,
    supplier_id: uuid.UUID,
) -> Decimal:
    """返回某供应商当前欠款余额（正数 = 欠供应商钱）。"""
    purchase_sum = (
        select(func.coalesce(func.sum(SupplierPayable.amount), Decimal("0")))
        .where(
            SupplierPayable.merchant_id == merchant_id,
            SupplierPayable.supplier_id == supplier_id,
            SupplierPayable.direction == "purchase",
        )
        .scalar_subquery()
    )
    payment_sum = (
        select(func.coalesce(func.sum(SupplierPayable.amount), Decimal("0")))
        .where(
            SupplierPayable.merchant_id == merchant_id,
            SupplierPayable.supplier_id == supplier_id,
            SupplierPayable.direction == "payment",
        )
        .scalar_subquery()
    )
    result = await db.execute(select(purchase_sum - payment_sum))
    balance = result.scalar()
    return balance if balance is not None else Decimal("0")


async def list_supplier_balances(
    db: AsyncSession,
    merchant_id: uuid.UUID,
):
    """返回该商户所有供应商的当前余额汇总。"""
    from app.models.catalog import Supplier

    stmt = (
        select(
            Supplier.id,
            Supplier.name,
            func.coalesce(
                func.sum(
                    case(
                        (SupplierPayable.direction == "purchase", SupplierPayable.amount),
                        else_=Decimal("0"),
                    )
                ),
                Decimal("0"),
            ).label("purchase_total"),
            func.coalesce(
                func.sum(
                    case(
                        (SupplierPayable.direction == "payment", SupplierPayable.amount),
                        else_=Decimal("0"),
                    )
                ),
                Decimal("0"),
            ).label("payment_total"),
        )
        .join(SupplierPayable, SupplierPayable.supplier_id == Supplier.id, isouter=True)
        .where(Supplier.merchant_id == merchant_id)
        .group_by(Supplier.id, Supplier.name)
        .order_by(Supplier.name)
    )
    result = await db.execute(stmt)
    rows = []
    for sid, name, p_total, pay_total in result.all():
        rows.append(
            {
                "supplier_id": str(sid),
                "name": name,
                "balance": float(p_total - pay_total),
            }
        )
    return rows


# ---------------------------------------------------------------------------
# Customer receivable (应收账款)
# ---------------------------------------------------------------------------


async def record_customer_receivable(
    db: AsyncSession,
    *,
    merchant_id: uuid.UUID,
    customer_name: str,
    amount: Decimal,
    direction: str = "charge",
    sale_order_id: uuid.UUID | None = None,
    note: str | None = None,
    due_date: date | None = None,
    idempotency_key: str | None = None,
) -> CustomerReceivable | None:
    """记录客户应收或回款。

    direction:
      - charge: 赊账产生应收（余额 +amount）
      - repay: 回款/结算（余额 -amount）
    """
    if amount <= 0:
        return None
    receivable = CustomerReceivable(
        merchant_id=merchant_id,
        customer_name=customer_name.strip(),
        direction=direction,
        amount=amount,
        sale_order_id=sale_order_id,
        note=note or ("赊账" if direction == "charge" else "回款"),
        due_date=due_date,
        settled=(direction == "repay"),
        idempotency_key=idempotency_key,
    )
    db.add(receivable)
    return receivable


async def get_customer_balance(
    db: AsyncSession,
    merchant_id: uuid.UUID,
    customer_name: str,
) -> Decimal:
    """返回某客户当前应收余额（正数 = 客户欠本商户钱）。"""
    charge_sum = (
        select(func.coalesce(func.sum(CustomerReceivable.amount), Decimal("0")))
        .where(
            CustomerReceivable.merchant_id == merchant_id,
            CustomerReceivable.customer_name == customer_name,
            CustomerReceivable.direction == "charge",
        )
        .scalar_subquery()
    )
    repay_sum = (
        select(func.coalesce(func.sum(CustomerReceivable.amount), Decimal("0")))
        .where(
            CustomerReceivable.merchant_id == merchant_id,
            CustomerReceivable.customer_name == customer_name,
            CustomerReceivable.direction == "repay",
        )
        .scalar_subquery()
    )
    result = await db.execute(select(charge_sum - repay_sum))
    balance = result.scalar()
    return balance if balance is not None else Decimal("0")


async def list_customer_balances(
    db: AsyncSession,
    merchant_id: uuid.UUID,
):
    """返回该商户所有客户的当前应收余额汇总。"""
    stmt = (
        select(
            CustomerReceivable.customer_name,
            func.coalesce(
                func.sum(
                    case(
                        (CustomerReceivable.direction == "charge", CustomerReceivable.amount),
                        else_=Decimal("0"),
                    )
                ),
                Decimal("0"),
            ).label("charge_total"),
            func.coalesce(
                func.sum(
                    case(
                        (CustomerReceivable.direction == "repay", CustomerReceivable.amount),
                        else_=Decimal("0"),
                    )
                ),
                Decimal("0"),
            ).label("repay_total"),
        )
        .where(CustomerReceivable.merchant_id == merchant_id)
        .group_by(CustomerReceivable.customer_name)
        .order_by(CustomerReceivable.customer_name)
    )
    result = await db.execute(stmt)
    rows = []
    for name, c_total, r_total in result.all():
        rows.append(
            {
                "customer_name": name,
                "balance": float(c_total - r_total),
            }
        )
    return rows


# ---------------------------------------------------------------------------
# Supplier statement (供应商对账单)
# ---------------------------------------------------------------------------


async def get_supplier_statement(
    db: AsyncSession,
    merchant_id: uuid.UUID,
    supplier_id: uuid.UUID,
    *,
    limit: int = 50,
) -> dict:
    """Return a supplier's ledger statement with all transactions.

    Includes purchase (应付产生), payment (付款), and return (退货抵扣) entries.
    """
    from app.models.catalog import Supplier

    supplier = await db.get(Supplier, supplier_id)
    supplier_name = supplier.name if supplier else None

    # All supplier payable entries for this supplier
    stmt = (
        select(SupplierPayable)
        .where(
            SupplierPayable.merchant_id == merchant_id,
            SupplierPayable.supplier_id == supplier_id,
        )
        .order_by(SupplierPayable.created_at.desc())
        .limit(min(limit, 200))
    )
    result = await db.execute(stmt)
    rows = result.scalars().all()

    items = []
    total_purchases = Decimal("0")
    total_payments = Decimal("0")
    total_returns = Decimal("0")

    for row in rows:
        if row.direction == "purchase":
            total_purchases += row.amount
        elif row.direction == "payment":
            total_payments += row.amount
        elif row.direction == "return":
            total_returns += row.amount

        settled_amount = row.settled_amount or Decimal("0")
        remaining_amount = max(row.amount - settled_amount, Decimal("0"))
        items.append(
            {
                "id": str(row.id),
                "direction": row.direction,
                "amount": float(row.amount),
                "note": row.note,
                "due_date": row.due_date.isoformat() if row.due_date else None,
                "settled": row.settled,
                "settled_amount": float(settled_amount),
                "remaining_amount": float(remaining_amount)
                if row.direction == "purchase"
                else 0,
                "created_at": row.created_at.isoformat() if row.created_at else None,
            }
        )

    current_balance = total_purchases - total_payments - total_returns

    return {
        "supplier_id": str(supplier_id),
        "supplier_name": supplier_name,
        "total_purchases": float(total_purchases),
        "total_payments": float(total_payments),
        "total_returns": float(total_returns),
        "current_balance": float(current_balance),
        "items": items,
    }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _to_decimal(value) -> Decimal | None:
    """把可能的 float/Decimal/None 安全转成 Decimal。"""
    if value is None:
        return None
    if isinstance(value, Decimal):
        return value
    try:
        return Decimal(str(value))
    except Exception:
        return None
