"""种子分片：POS 订单 + 支付 + 日结 + 对账。

刻意覆盖所有 POS 状态以演示完整收银闭环：
  - paid（现金/微信/支付宝）  —— 大多数
  - held（挂单）              —— 2-3 笔
  - credit（赊账）            —— 3-5 笔，关联 customer_receivables
  - refunded / partial_refund —— 2-3 笔
  - 组合支付（现金+微信）     —— 2-3 笔

日结对账：30 天汇总，其中 2-3 天刻意留差异（演示对账异常处理）。

幂等：按 (merchant_id, order_no) 唯一键与 order_no 前缀判重。
"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

from sqlalchemy import func, select

from app.models.pos import DailySettlement, Payment, Reconciliation, SaleOrder, SaleOrderItem
from scripts.seed_data.common import (
    ALL_MERCHANT_IDS,
    CREDIT_CUSTOMERS,
    MERCHANTS,
    PRODUCTS_BY_ID,
    date_ago,
    days_ago,
    make_rng,
    money,
    products_for,
    qty,
    sku_uuid,
)


def _pick_products(rng, merchant_products, k: int):
    k = min(k, len(merchant_products))
    return rng.sample(list(merchant_products), k)


async def seed_pos_orders(db) -> dict:
    """生成 POS 订单 + 行项目 + 支付。返回 {merchant_id: {date: summary}}。"""
    rng = make_rng()
    daily: dict = {m: {} for m in ALL_MERCHANT_IDS}
    n_order = n_item = n_pay = 0

    for profile in MERCHANTS:
        merchant_id = profile.merchant_id
        # 幂等：已有订单跳过
        existed = await db.execute(
            select(func.count()).select_from(SaleOrder).where(SaleOrder.merchant_id == merchant_id)
        )
        if int(existed.scalar_one()) > 0:
            print(f"  [=] {profile.name} 订单已存在，跳过")
            continue

        my_products = products_for(merchant_id)
        credit_customers = [c.name for c in CREDIT_CUSTOMERS if c.merchant_id == merchant_id]
        order_seq = 0

        for d in range(30, 0, -1):
            day = date_ago(d)
            n_orders = profile.daily_order_base + rng.randint(-1, 2)
            n_orders = max(1, n_orders)
            day_sales = money("0")
            day_payments = money("0")
            cash = wechat = alipay = credit_amt = money("0")

            for _ in range(n_orders):
                order_seq += 1
                order_no = f"POS{day.strftime('%Y%m%d')}{merchant_id.hex[-2:].upper()}{order_seq:03d}"
                client_id = f"seed-{order_no}"

                items = _pick_products(rng, my_products, rng.randint(1, 4))
                # 决定订单类型
                roll = rng.random()
                if roll < 0.05:
                    status, scenario = "held", "held"
                elif roll < 0.12 and credit_customers:
                    status, scenario = "credit", "credit"
                elif roll < 0.17:
                    status, scenario = "paid", "refund"
                elif roll < 0.22:
                    status, scenario = "paid", "combined"
                else:
                    status, scenario = "paid", "single"

                total = money("0")
                item_rows = []
                for prod in items:
                    q = Decimal(rng.randint(1, 6))
                    line_total = (prod.default_price * q).quantize(Decimal("0.01"))
                    total += line_total
                    item_rows.append((prod, q, line_total))

                paid_amount = money("0")
                refunded_amount = money("0")
                held_at = None
                customer_name = None
                paid_at = None
                refund_reason = None
                refunded_at = None
                created_dt = days_ago(d, hour=rng.randint(7, 20), minute=rng.randint(0, 59))

                # 支付方式
                pay_method = rng.choice(["cash", "wechat", "alipay"])

                if scenario == "held":
                    held_at = created_dt
                    # 挂单不付款
                elif scenario == "credit":
                    customer_name = rng.choice(credit_customers)
                    paid_amount = money("0")
                    status = "credit"
                    paid_at = created_dt
                    credit_amt += total
                elif scenario == "refund":
                    paid_amount = total
                    refunded_amount = (total * Decimal("0.5")).quantize(Decimal("0.01"))
                    status = "partial_refund"
                    refund_reason = rng.choice(
                        ["顾客反映不新鲜", "称重有误，部分退货", "买多了退一部分"]
                    )
                    refunded_at = created_dt + timedelta(hours=1)
                    paid_at = created_dt
                elif scenario == "combined":
                    paid_amount = total
                    paid_at = created_dt
                else:
                    paid_amount = total
                    paid_at = created_dt

                order = SaleOrder(
                    merchant_id=merchant_id,
                    order_no=order_no,
                    status=status,
                    total_amount=total,
                    paid_amount=paid_amount,
                    refunded_amount=refunded_amount,
                    discount_amount=money("0"),
                    client_id=client_id,
                    customer_name=customer_name,
                    held_at=held_at,
                    refund_reason=refund_reason,
                    refunded_at=refunded_at,
                    paid_at=paid_at,
                    created_at=created_dt,
                )
                db.add(order)
                await db.flush()
                n_order += 1

                for prod, q, line_total in item_rows:
                    db.add(
                        SaleOrderItem(
                            order_id=order.id,
                            merchant_id=merchant_id,
                            sku_id=sku_uuid(merchant_id, prod.id),
                            product_id=prod.id,
                            quantity=q,
                            unit=prod.unit,
                            unit_price=prod.default_price,
                            unit_cost=(prod.default_price * Decimal("0.65")).quantize(Decimal("0.01")),
                            total_amount=line_total,
                        )
                    )
                    n_item += 1

                # 支付流水
                if scenario == "held":
                    pass  # 挂单无支付
                elif scenario == "credit":
                    db.add(
                        Payment(
                            merchant_id=merchant_id,
                            order_id=order.id,
                            amount=total,
                            method="credit",
                            status="success",
                            transaction_id=f"CR{order_no}",
                            note=f"赊账:{customer_name}",
                            created_at=created_dt,
                        )
                    )
                    n_pay += 1
                elif scenario == "combined":
                    cash_part = (total * Decimal("0.4")).quantize(Decimal("0.01"))
                    wechat_part = total - cash_part
                    db.add(
                        Payment(
                            merchant_id=merchant_id,
                            order_id=order.id,
                            amount=cash_part,
                            method="cash",
                            status="success",
                            created_at=created_dt,
                        )
                    )
                    db.add(
                        Payment(
                            merchant_id=merchant_id,
                            order_id=order.id,
                            amount=wechat_part,
                            method="wechat",
                            status="success",
                            transaction_id=f"WX{order_no}",
                            created_at=created_dt,
                        )
                    )
                    n_pay += 2
                    cash += cash_part
                    wechat += wechat_part
                elif scenario == "refund":
                    db.add(
                        Payment(
                            merchant_id=merchant_id,
                            order_id=order.id,
                            amount=total,
                            method=pay_method,
                            status="success",
                            transaction_id=f"{pay_method[:2].upper()}{order_no}",
                            created_at=created_dt,
                        )
                    )
                    db.add(
                        Payment(
                            merchant_id=merchant_id,
                            order_id=order.id,
                            amount=refunded_amount,
                            method=pay_method,
                            status="refunded",
                            note=f"退款:{refund_reason}",
                            created_at=refunded_at or created_dt,
                        )
                    )
                    n_pay += 2
                    net_pay = total - refunded_amount
                    # day_payments 由下方统一累加，此处只拆分渠道
                    if pay_method == "cash":
                        cash += net_pay
                    elif pay_method == "wechat":
                        wechat += net_pay
                    else:
                        alipay += net_pay
                else:  # single paid
                    db.add(
                        Payment(
                            merchant_id=merchant_id,
                            order_id=order.id,
                            amount=total,
                            method=pay_method,
                            status="success",
                            transaction_id=f"{pay_method[:2].upper()}{order_no}",
                            created_at=created_dt,
                        )
                    )
                    n_pay += 1
                    if pay_method == "cash":
                        cash += total
                    elif pay_method == "wechat":
                        wechat += total
                    else:
                        alipay += total

                day_sales += total
                if scenario not in ("held",):
                    day_payments += (total - refunded_amount) if scenario == "refund" else (
                        money("0") if scenario == "credit" else total
                    )

            daily[merchant_id][day] = {
                "sales": day_sales,
                "payments": day_payments,
                "cash": cash,
                "wechat": wechat,
                "alipay": alipay,
                "credit": credit_amt,
            }

    await db.flush()
    print(f"  [+] POS 订单: {n_order}, 行项目: {n_item}, 支付: {n_pay}")
    return daily


async def seed_settlements_and_reconciliation(db, daily: dict) -> None:
    """日结 + 对账。其中 2-3 天刻意留差异（演示异常处理）。"""
    rng = make_rng()
    n_settle = n_recon = 0

    for merchant_id, days in daily.items():
        sorted_days = sorted(days.keys())
        # 随机挑 2 天做对账差异
        diff_days = set(rng.sample(sorted_days, min(2, len(sorted_days)))) if sorted_days else set()

        for day, agg in days.items():
            # 日结
            existing_settle = await db.execute(
                select(DailySettlement).where(
                    DailySettlement.merchant_id == merchant_id,
                    DailySettlement.date == day,
                )
            )
            if existing_settle.scalar_one_or_none() is None:
                diff = agg["sales"] - agg["payments"] - agg["credit"]
                db.add(
                    DailySettlement(
                        merchant_id=merchant_id,
                        date=day,
                        total_sales=agg["sales"],
                        total_payments=agg["payments"],
                        cash_amount=agg["cash"],
                        wechat_amount=agg["wechat"],
                        alipay_amount=agg["alipay"],
                        credit_amount=agg["credit"],
                        diff_amount=diff.quantize(Decimal("0.01")),
                        status="closed",
                        closed_at=days_ago((date_ago(0) - day).days, hour=22),
                    )
                )
                n_settle += 1

            # 对账记录
            existing_recon = await db.execute(
                select(Reconciliation).where(
                    Reconciliation.merchant_id == merchant_id,
                    Reconciliation.date == day,
                )
            )
            if existing_recon.scalar_one_or_none() is None:
                inventory_cost = (agg["sales"] * Decimal("0.65")).quantize(Decimal("0.01"))
                if day in diff_days:
                    # 刻意差异：实收比应收少一点（演示对账异常）
                    payment_total = (agg["payments"] - Decimal("3.50")).quantize(Decimal("0.01"))
                    diff_amount = (agg["sales"] - payment_total).quantize(Decimal("0.01"))
                    status = "exception"
                    note = "系统订单比渠道到账多 3.50 元，疑似一笔微信支付延迟到账"
                else:
                    payment_total = agg["payments"]
                    diff_amount = (agg["sales"] - payment_total - agg["credit"]).quantize(Decimal("0.01"))
                    status = "balanced"
                    note = None

                db.add(
                    Reconciliation(
                        merchant_id=merchant_id,
                        date=day,
                        sale_total=agg["sales"],
                        payment_total=payment_total,
                        inventory_cost_total=inventory_cost,
                        diff_amount=diff_amount,
                        status=status,
                        note=note,
                    )
                )
                n_recon += 1

    await db.flush()
    print(f"  [+] 日结: {n_settle}, 对账: {n_recon}（含异常对账）")


async def seed_pos_and_reconciliation(db) -> dict:
    """POS 层总入口。"""
    print("[3/7] POS 层（订单/支付/日结/对账）")
    daily = await seed_pos_orders(db)
    await seed_settlements_and_reconciliation(db, daily)
    return {"daily": daily}
