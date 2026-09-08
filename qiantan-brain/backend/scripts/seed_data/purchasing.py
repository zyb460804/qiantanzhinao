"""种子分片：采购单 + 供应商应付 + 客户应收/信用档案。

覆盖完整采购验收闭环（阶段A）：
  draft → confirmed → partial_arrival → accepted → stored → completed
                                                ↘ returned

刻意制造演示状态：每摊 6 张采购单，覆盖草稿/在途/已入库/退货。
应付账款：进货产生应付 + 部分付款核销（演示供应商结算）。
客户应收：赊账产生 + 部分回款（演示往来账催收）。

幂等：按 purchase_lists 的固定 UUID 判重。
"""

from __future__ import annotations

import uuid
from datetime import timedelta
from decimal import Decimal

from sqlalchemy import func, select

from app.models.accounts import (
    CustomerCreditProfile,
    CustomerReceivable,
    SupplierPayable,
)
from app.models.purchase import PurchaseItem, PurchaseList
from scripts.seed_data.common import (
    CREDIT_CUSTOMERS,
    MERCHANTS,
    date_ago,
    days_ago,
    make_rng,
    money,
    products_for,
    sku_uuid,
    supplier_id_for,
)


def _purchase_list_uuid(merchant_id: uuid.UUID, n: int) -> uuid.UUID:
    return uuid.uuid5(uuid.NAMESPACE_URL, f"qiantan-purchase-list-{merchant_id}-{n}")


async def seed_purchase_lists(db) -> None:
    """每摊 6 张采购单，覆盖完整状态机。"""
    rng = make_rng()
    n_list = n_item = 0

    # 每摊的 6 张单状态分布
    states = ["draft", "confirmed", "partial_arrival", "completed", "stored", "returned"]

    for profile in MERCHANTS:
        merchant_id = profile.merchant_id
        my_products = products_for(merchant_id)

        for idx, status in enumerate(states):
            list_id = _purchase_list_uuid(merchant_id, idx)
            if await db.get(PurchaseList, list_id) is not None:
                continue

            created = days_ago(20 - idx * 3, hour=7)
            confirmed = created + timedelta(hours=2) if status != "draft" else None
            purchased = (
                confirmed + timedelta(hours=4) if status not in ("draft", "confirmed") else None
            )
            accepted = (
                purchased + timedelta(hours=12)
                if status in ("partial_arrival", "accepted", "stored", "completed", "returned")
                else None
            )
            stored = accepted + timedelta(hours=4) if status in ("stored", "completed") else None
            completed = stored + timedelta(hours=8) if status == "completed" else None

            chosen = rng.sample(list(my_products), min(3, len(my_products)))
            total_est = money("0")
            total_act = money("0")
            payment_status = "unpaid"
            paid_amount = money("0")

            items_meta = []
            for prod in chosen:
                rec_qty = Decimal(rng.randint(20, 50))
                cost = (prod.default_price * Decimal("0.65")).quantize(Decimal("0.01"))
                est_cost = (rec_qty * cost).quantize(Decimal("0.01"))
                total_est += est_cost
                items_meta.append((prod, rec_qty, cost, est_cost))

            # 实际成本（部分到货有差异）
            if status in ("completed", "stored"):
                total_act = (total_est * Decimal(str(rng.uniform(0.95, 1.05)))).quantize(
                    Decimal("0.01")
                )
                payment_status = rng.choice(["paid", "partial", "credit"])
                if payment_status == "paid":
                    paid_amount = total_act
                elif payment_status == "partial":
                    paid_amount = (total_act * Decimal("0.5")).quantize(Decimal("0.01"))
            elif status == "partial_arrival":
                total_act = (total_est * Decimal("0.6")).quantize(Decimal("0.01"))

            supplier_idx = rng.choice([1, 2, 3, 4, 5])

            pl = PurchaseList(
                id=list_id,
                merchant_id=merchant_id,
                status=status,
                total_estimated_cost=total_est,
                total_actual_cost=total_act if total_act > 0 else None,
                item_count=len(chosen),
                notes=_purchase_note(status),
                expected_arrival_date=confirmed + timedelta(hours=12) if confirmed else None,
                payment_status=payment_status,
                paid_amount=paid_amount,
                created_at=created,
                confirmed_at=confirmed,
                purchased_at=purchased,
                accepted_at=accepted,
                stored_at=stored,
                completed_at=completed,
            )
            db.add(pl)
            await db.flush()
            n_list += 1

            for prod, rec_qty, cost, est_cost in items_meta:
                actual_qty = rec_qty
                arrival = None
                shortage = None
                damaged = None
                accepted_qty = None
                quality_ok = None
                item_status = "pending"

                if status in ("partial_arrival",):
                    actual_qty = (rec_qty * Decimal("0.6")).quantize(Decimal("0.01"))
                    arrival = actual_qty
                    shortage = (rec_qty - actual_qty).quantize(Decimal("0.01"))
                    item_status = "purchased"
                elif status in ("stored", "completed"):
                    actual_qty = (rec_qty * Decimal(str(rng.uniform(0.92, 1.0)))).quantize(
                        Decimal("0.01")
                    )
                    arrival = actual_qty
                    shortage = (
                        (rec_qty - actual_qty).quantize(Decimal("0.01"))
                        if actual_qty < rec_qty
                        else None
                    )
                    damaged = Decimal(str(rng.randint(0, 2))) if rng.random() < 0.3 else None
                    accepted_qty = (actual_qty - (damaged or Decimal("0"))).quantize(
                        Decimal("0.01")
                    )
                    quality_ok = True
                    item_status = "purchased"
                elif status == "returned":
                    actual_qty = rec_qty
                    arrival = rec_qty
                    accepted_qty = Decimal("0")
                    quality_ok = False
                    item_status = "returned"

                db.add(
                    PurchaseItem(
                        list_id=list_id,
                        merchant_id=merchant_id,
                        supplier_id=supplier_id_for(merchant_id, supplier_idx),
                        product_id=prod.id,
                        sku_id=sku_uuid(merchant_id, prod.id),
                        recommended_qty=rec_qty,
                        actual_qty=actual_qty,
                        unit=prod.unit,
                        estimated_unit_cost=cost,
                        actual_unit_cost=cost,
                        estimated_cost=est_cost,
                        actual_cost=(actual_qty * cost).quantize(Decimal("0.01")),
                        status=item_status,
                        arrival_qty=arrival,
                        shortage_qty=shortage,
                        damaged_qty=damaged,
                        accepted_qty=accepted_qty,
                        quality_ok=quality_ok,
                        package_count=rng.randint(1, 3) if arrival else None,
                        net_weight=arrival,
                        certificates='{"quarantine": "合格"}',
                        accepted_at=accepted,
                    )
                )
                n_item += 1

    await db.flush()
    print(f"  [+] 采购单: {n_list}, 采购明细: {n_item}")


def _purchase_note(status: str) -> str:
    notes = {
        "draft": "AI 建议草稿，待摊主确认",
        "confirmed": "已确认下单，等待供应商发货",
        "partial_arrival": "部分到货，缺斤待补",
        "stored": "已验收入库",
        "completed": "采购完成，账货两清",
        "returned": "快检不合格，整批退回供应商",
    }
    return notes.get(status, "")


async def seed_supplier_payables(db) -> None:
    """供应商应付账款：进货产生应付 + 部分付款核销。"""
    rng = make_rng()
    existing = await db.execute(select(func.count()).select_from(SupplierPayable))
    if int(existing.scalar_one()) > 0:
        print("  [=] 应付账款已存在，跳过")
        return

    n = 0
    for profile in MERCHANTS:
        merchant_id = profile.merchant_id
        # 3 个供应商，每个 2 笔应付（一笔已付清、一笔未付/部分付）
        for s_idx in [1, 2, 3]:
            sid = supplier_id_for(merchant_id, s_idx)
            for batch_n in range(2):
                # 进货应付
                amount = money(rng.randint(300, 1500))
                purchase_list = _purchase_list_uuid(merchant_id, batch_n + 3)
                due = date_ago(rng.randint(-5, 10))  # 部分已逾期（负数天=未来）

                payable_id = uuid.uuid5(
                    uuid.NAMESPACE_URL, f"qiantan-payable-{merchant_id}-{s_idx}-{batch_n}"
                )
                db.add(
                    SupplierPayable(
                        id=payable_id,
                        merchant_id=merchant_id,
                        supplier_id=sid,
                        direction="purchase",
                        amount=amount,
                        purchase_list_id=purchase_list,
                        note=f"采购货款-{date_ago(batch_n * 5 + 3)}",
                        due_date=due,
                        settled=(batch_n == 0),  # 第一笔已结清
                        settled_amount=amount if batch_n == 0 else money("0"),
                        idempotency_key=f"seed-pay-{merchant_id.hex[-1]}-{s_idx}-{batch_n}",
                    )
                )
                n += 1

                # 第二笔：部分付款
                if batch_n == 1:
                    pay_amount = (amount * Decimal("0.4")).quantize(Decimal("0.01"))
                    payment_id = uuid.uuid5(
                        uuid.NAMESPACE_URL, f"qiantan-payment-{merchant_id}-{s_idx}-{batch_n}"
                    )
                    db.add(
                        SupplierPayable(
                            id=payment_id,
                            merchant_id=merchant_id,
                            supplier_id=sid,
                            direction="payment",
                            amount=pay_amount,
                            purchase_list_id=purchase_list,
                            note="部分付款",
                            settled=False,
                            settled_amount=money("0"),
                            idempotency_key=f"seed-payp-{merchant_id.hex[-1]}-{s_idx}-{batch_n}",
                        )
                    )
                    n += 1

    await db.flush()
    print(f"  [+] 应付账款流水: {n} 条")


async def seed_customer_credit_and_receivables(db) -> None:
    """客户信用档案 + 应收账款流水（赊账/回款）。"""
    rng = make_rng()
    # 信用档案
    n_profile = 0
    for c in CREDIT_CUSTOMERS:
        exists = await db.execute(
            select(CustomerCreditProfile.id)
            .where(CustomerCreditProfile.merchant_id == c.merchant_id)
            .where(CustomerCreditProfile.customer_name == c.name)
        )
        if exists.first() is not None:
            continue
        db.add(
            CustomerCreditProfile(
                merchant_id=c.merchant_id,
                customer_name=c.name,
                credit_limit=c.credit_limit,
                default_credit_days=c.default_credit_days,
                is_blocked=c.is_blocked,
                block_reason=c.block_reason,
                notes="种子数据",
            )
        )
        n_profile += 1

    # 应收流水：每个客户 1-3 笔赊账 + 0-1 笔回款
    existing = await db.execute(select(func.count()).select_from(CustomerReceivable))
    n_recv = 0
    if int(existing.scalar_one()) == 0:
        for c in CREDIT_CUSTOMERS:
            for k in range(rng.randint(2, 3)):
                amount = money(rng.randint(50, 400))
                db.add(
                    CustomerReceivable(
                        merchant_id=c.merchant_id,
                        customer_name=c.name,
                        direction="charge",
                        amount=amount,
                        note=f"赊账-买菜{k + 1}",
                        due_date=date_ago(-c.default_credit_days + 5),
                        settled=False,
                        idempotency_key=f"seed-recv-{c.merchant_id.hex[-1]}-{c.name}-{k}",
                    )
                )
                n_recv += 1
            # 非黑名单客户有 1 笔回款
            if not c.is_blocked and rng.random() < 0.6:
                repay = money(rng.randint(100, 300))
                db.add(
                    CustomerReceivable(
                        merchant_id=c.merchant_id,
                        customer_name=c.name,
                        direction="repay",
                        amount=repay,
                        note="月底结清部分欠款",
                        settled=True,
                        idempotency_key=f"seed-recv-r-{c.merchant_id.hex[-1]}-{c.name}",
                    )
                )
                n_recv += 1

    await db.flush()
    print(f"  [+] 信用档案: {n_profile}, 应收流水: {n_recv}")


async def seed_purchasing_and_payables(db) -> dict:
    """采购/账期层总入口。"""
    print("[4/7] 采购/账期层（采购单/应付/应收）")
    await seed_purchase_lists(db)
    await seed_supplier_payables(db)
    await seed_customer_credit_and_receivables(db)
    return {}
