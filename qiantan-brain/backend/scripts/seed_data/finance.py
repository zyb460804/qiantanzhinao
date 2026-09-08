"""种子分片：经营费用 + 发票归档。

覆盖财务对账页面的费用侧：
  - 租金 / 水电 / 人工 / 手续费（微信支付手续费）/ 其他
  - 数电发票归档（关联费用）
  - 跨 3 个月，演示月度费用趋势

幂等：按固定 UUID 判重。
"""

from __future__ import annotations

import uuid

from sqlalchemy import func, select

from app.models.expense import Expense, Invoice
from scripts.seed_data.common import (
    MERCHANTS,
    date_ago,
    make_rng,
    money,
)


async def seed_invoices(db) -> dict[str, uuid.UUID]:
    """发票归档（每摊 4 张），返回 invoice_number → id 映射。"""
    existing = await db.execute(select(func.count()).select_from(Invoice))
    if int(existing.scalar_one()) > 0:
        print("  [=] 发票已存在，跳过")
        return {}

    invoice_map: dict[str, uuid.UUID] = {}
    n = 0
    for profile in MERCHANTS:
        merchant_id = profile.merchant_id
        inv_defs = [
            (
                "SH2026050001",
                "上海农产品批发中心",
                money("3500.00"),
                money("175.00"),
                date_ago(75),
                "摊位租金-5月",
            ),
            (
                "SH2026060001",
                "上海农产品批发中心",
                money("3500.00"),
                money("175.00"),
                date_ago(45),
                "摊位租金-6月",
            ),
            (
                "PDD2026060002",
                "国网上海电力",
                money("280.00"),
                money("0.00"),
                date_ago(40),
                "电费-6月",
            ),
            (
                "PDD2026070001",
                "国网上海电力",
                money("315.00"),
                money("0.00"),
                date_ago(10),
                "电费-7月",
            ),
        ]
        for inv_no, supplier, amount, tax, inv_date, note in inv_defs:
            inv_id = uuid.uuid5(uuid.NAMESPACE_URL, f"invoice-{merchant_id}-{inv_no}")
            invoice_map[inv_no] = inv_id
            db.add(
                Invoice(
                    id=inv_id,
                    merchant_id=merchant_id,
                    invoice_number=inv_no,
                    invoice_type="electronic",
                    supplier_name=supplier,
                    amount=amount,
                    tax_amount=tax,
                    invoice_date=inv_date,
                    notes=note,
                )
            )
            n += 1
    await db.flush()
    print(f"  [+] 发票归档: {n} 张")
    return invoice_map


async def seed_expenses(db, invoice_map: dict[str, uuid.UUID]) -> None:
    """经营费用（租金/水电/人工/手续费），跨 3 个月。"""
    rng = make_rng()
    existing = await db.execute(select(func.count()).select_from(Expense))
    if int(existing.scalar_one()) > 0:
        print("  [=] 费用已存在，跳过")
        return

    n = 0
    for profile in MERCHANTS:
        merchant_id = profile.merchant_id
        rent_base = {"蔬菜水果": 3500, "水果": 4200, "肉类": 4800}.get(profile.business_type, 3500)

        for month_offset in [75, 45, 15]:  # 近 3 个月
            month_date = date_ago(month_offset)
            # 租金（关联发票）
            rent_invoice_no = f"SH{month_date.strftime('%Y%m')}0001"
            rent_inv_id = invoice_map.get(rent_invoice_no)
            db.add(
                Expense(
                    merchant_id=merchant_id,
                    category="rent",
                    amount=money(rent_base),
                    description=f"摊位租金-{month_date.strftime('%Y年%m月')}",
                    expense_date=month_date,
                    payment_method="bank_transfer",
                    invoice_id=rent_inv_id,
                )
            )
            n += 1
            # 水电
            db.add(
                Expense(
                    merchant_id=merchant_id,
                    category="utility",
                    amount=money(rng.randint(250, 400)),
                    description=f"水电费-{month_date.strftime('%Y年%m月')}",
                    expense_date=month_date,
                    payment_method="wechat",
                )
            )
            n += 1
            # 人工（仅菜摊/肉摊有雇员）
            if profile.business_type in ("蔬菜水果", "肉类"):
                db.add(
                    Expense(
                        merchant_id=merchant_id,
                        category="labor",
                        amount=money(rng.randint(3000, 4500)),
                        description=f"人工工资-{month_date.strftime('%Y年%m月')}",
                        expense_date=month_date,
                        payment_method="bank_transfer",
                    )
                )
                n += 1
            # 手续费（微信/支付宝）
            db.add(
                Expense(
                    merchant_id=merchant_id,
                    category="fee",
                    amount=money(rng.randint(80, 200)),
                    description="移动支付手续费",
                    expense_date=month_date,
                    payment_method=None,
                )
            )
            n += 1
    await db.flush()
    print(f"  [+] 经营费用: {n} 条（3 个月趋势）")


async def seed_finance(db) -> dict:
    """财务层总入口。"""
    print("[5/7] 财务层（费用/发票）")
    invoice_map = await seed_invoices(db)
    await seed_expenses(db, invoice_map)
    return {}
