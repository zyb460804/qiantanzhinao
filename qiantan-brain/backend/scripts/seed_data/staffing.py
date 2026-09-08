"""种子分片：员工 + 盘点。

- 员工（staff_members）：每摊 3-4 人，覆盖 manager/cashier/purchaser/stocker，
  演示多角色权限矩阵。owner 只能由商户本人承载，员工表不再生成 owner 行
  （V3-H1：staff_login 对 role='owner' 员工直接 403）。
- 盘点（stocktake_sessions + stocktake_items）：每摊 3 次历史盘点，
  含盘亏/盘盈/称重误差，演示库存校正闭环。

幂等：按固定 staff UUID 与盘点会话判重。
"""

from __future__ import annotations

import secrets
import uuid
from datetime import timedelta
from decimal import Decimal

from sqlalchemy import func, select

from app.models.staff import StaffMember
from app.models.stocktake import StocktakeItem, StocktakeSession
from scripts.seed_data.common import (
    MERCHANTS,
    days_ago,
    make_rng,
    money,
    products_for,
    staff_uuid,
)


async def seed_staff(db) -> None:
    """每摊 3-4 名员工。

    PIN 用 secrets 随机生成（V3-H1：原 1000/2111/4333 线性可预测，撞库
    即可登录任意种子员工）。随机 PIN 仅在首次创建时打印一次，重跑种子
    对已有员工不重置 PIN。
    """
    from scripts.seed_data.common import STAFF_BY_MERCHANT

    n = 0
    created_pins: list[tuple[str, str]] = []
    for merchant_id, staff_list in STAFF_BY_MERCHANT.items():
        for idx, s in enumerate(staff_list):
            sid = staff_uuid(merchant_id, idx)
            if await db.get(StaffMember, sid) is not None:
                continue
            pin = f"{secrets.randbelow(10000):04d}"
            db.add(
                StaffMember(
                    id=sid,
                    merchant_id=merchant_id,
                    name=s.name,
                    phone=s.phone,
                    role=s.role,
                    is_active=True,
                    pin_code=pin,
                )
            )
            created_pins.append((s.name, pin))
            n += 1
    await db.flush()
    print(f"  [+] 员工: {n} 名")
    if created_pins:
        print("  [!] 员工 PIN 已随机生成（仅本次展示，请立即转交对应员工，重跑不重置）:")
        for name, pin in created_pins:
            print(f"      {name}: {pin}")


async def seed_stocktake(db) -> None:
    """每摊 3 次盘点历史，含盘亏/盘盈。"""
    rng = make_rng()
    existing = await db.execute(select(func.count()).select_from(StocktakeSession))
    if int(existing.scalar_one()) > 0:
        print("  [=] 盘点记录已存在，跳过")
        return

    n_session = n_item = 0
    for profile in MERCHANTS:
        merchant_id = profile.merchant_id
        my_products = list(products_for(merchant_id))

        for round_n in range(3):
            session_id = uuid.uuid5(
                uuid.NAMESPACE_URL, f"qiantan-stocktake-{merchant_id}-{round_n}"
            )
            started = days_ago(25 - round_n * 8, hour=20)
            completed = started + timedelta(hours=2)

            total_book = Decimal("0")
            total_actual = Decimal("0")
            total_var = Decimal("0")
            total_loss = money("0")

            session = StocktakeSession(
                id=session_id,
                merchant_id=merchant_id,
                status="completed",
                started_at=started,
                completed_at=completed,
                notes=f"第 {round_n + 1} 次例行盘点",
            )
            db.add(session)
            await db.flush()
            n_session += 1

            for prod in my_products:
                book = Decimal(rng.randint(10, 50))
                # 盘亏/盘盈：大部分小差异，1-2 个大差异
                if rng.random() < 0.25:
                    variance = Decimal(rng.randint(-8, -2))  # 盘亏
                elif rng.random() < 0.35:
                    variance = Decimal(rng.randint(1, 4))  # 盘盈
                else:
                    variance = Decimal(rng.randint(-1, 1))  # 基本持平
                actual = book + variance
                if actual < 0:
                    actual = Decimal("0")
                    variance = -book

                total_book += book
                total_actual += actual
                total_var += variance
                total_loss += (
                    (variance * prod.default_price * Decimal("-1")).quantize(Decimal("0.01"))
                    if variance < 0
                    else money("0")
                )

                reason = None
                if variance < -2:
                    reason = rng.choice(["natural_loss", "unrecorded_sale", "weighing_error"])
                elif variance > 1:
                    reason = "weighing_error"

                db.add(
                    StocktakeItem(
                        session_id=session_id,
                        merchant_id=merchant_id,
                        product_id=prod.id,
                        book_qty=book,
                        actual_qty=actual,
                        variance=variance,
                        unit=prod.unit,
                        variance_reason=reason,
                    )
                )
                n_item += 1

            session.total_book_qty = total_book
            session.total_actual_qty = total_actual
            session.total_variance = total_var
            session.total_loss_amount = total_loss

    await db.flush()
    print(f"  [+] 盘点: {n_session} 次会话, {n_item} 条明细（含盘亏盘盈）")


async def seed_staff_and_stocktake(db) -> dict:
    """员工/盘点层总入口。"""
    print("[6/7] 员工/盘点层")
    await seed_staff(db)
    await seed_stocktake(db)
    return {}
