"""种子分片：库存流水 + 当前库存 + 批次追溯 + 环境记录。

- 库存流水（inventory_records）：30 天进销事件，三摊各自独立账本。
- 当前库存（current_inventory）：净库存汇总视图。
- 批次追溯（batch_lifecycles）：近 14 天进货批次，带 QR / 产地 / 临期 /
  锁定，演示食安追溯 + FIFO + 临期预警。
- 环境记录（environment_records）：30 天天气，城市级共享。

幂等：批量数据按 (merchant_id, batch_label) 或日期判重。
"""

from __future__ import annotations

import json
from datetime import timedelta
from decimal import Decimal

from sqlalchemy import func, select

from app.models.batch import BatchLifecycle
from app.models.environment import EnvironmentRecord
from app.models.inventory import CurrentInventory, InventoryRecord
from scripts.seed_data.common import (
    ALL_MERCHANT_IDS,
    MERCHANTS,
    PRODUCTS_BY_ID,
    date_ago,
    days_ago,
    make_rng,
    money,
    products_for,
    qty,
    sku_uuid,
    supplier_id_for,
)


async def seed_environment(db) -> None:
    """30 天天气（城市级，与原 seed 保持兼容）。"""
    existing = await db.execute(select(func.count()).select_from(EnvironmentRecord))
    if int(existing.scalar_one()) > 0:
        print("  [=] 环境记录已存在，跳过")
        return

    rng = make_rng()
    for d in range(30, 0, -1):
        day = date_ago(d)
        temp_h = round(rng.uniform(15, 35), 1)
        temp_l = round(temp_h - rng.uniform(5, 12), 1)
        rain = rng.randint(0, 100)
        weather = "雨" if rain > 50 else "多云" if rain > 20 else "晴"
        dow = day.weekday()
        db.add(
            EnvironmentRecord(
                date=day,
                city="上海",
                temp_high=temp_h,
                temp_low=temp_l,
                weather_type=weather,
                rainfall_prob=float(rain),
                is_holiday=False,
                day_of_week=dow,
                is_weekend=dow >= 5,
            )
        )
    await db.flush()
    print("  [+] 环境记录: 30 天天气")


def _purchase_qty(rng, profile) -> int:
    """根据摊主经营节奏生成单次进货量。"""
    base = {"蔬菜水果": (15, 50), "水果": (20, 60), "肉类": (10, 35)}
    lo, hi = base.get(profile.business_type, (15, 50))
    return rng.randint(lo, hi)


async def seed_inventory_records(db) -> dict:
    """30 天进销流水。返回 {merchant_id: {product_id: net_qty}} 供后续复用。"""
    rng = make_rng()
    net_stock: dict = {m: {} for m in ALL_MERCHANT_IDS}
    n = 0

    for profile in MERCHANTS:
        merchant_id = profile.merchant_id
        # 已有该商户流水则跳过（幂等）
        existed = await db.execute(
            select(func.count())
            .select_from(InventoryRecord)
            .where(InventoryRecord.merchant_id == merchant_id)
        )
        skip = int(existed.scalar_one()) > 0

        my_products = products_for(merchant_id)
        for d in range(30, 0, -1):
            # 进货日：按经营节奏
            is_purchase_day = d % profile.purchase_interval_days == 0
            n_purchase = rng.randint(3, 6) if is_purchase_day else 0

            for prod in my_products:
                pid = prod.id
                net_stock[merchant_id].setdefault(pid, Decimal("0"))

                if n_purchase > 0 and pid in [p.id for p in rng.sample(list(my_products), min(n_purchase, len(my_products)))]:
                    purchase_qty_val = _purchase_qty(rng, profile)
                    cost = prod.default_price * Decimal(str(round(rng.uniform(*prod.cost_ratio), 2)))
                    day = date_ago(d)
                    if not skip:
                        db.add(
                            InventoryRecord(
                                merchant_id=merchant_id,
                                product_id=pid,
                                sku_id=sku_uuid(merchant_id, pid),
                                quantity=qty(purchase_qty_val),
                                unit=prod.unit,
                                unit_cost=cost,
                                total_amount=(cost * Decimal(purchase_qty_val)).quantize(Decimal("0.01")),
                                event_type="purchase",
                                event_time=days_ago(d, hour=6),
                                source="seed",
                                batch_label=f"{prod.name}-{day.strftime('%m%d')}",
                            )
                        )
                        n += 1
                    net_stock[merchant_id][pid] += Decimal(purchase_qty_val)

                # 当日销售（约进货量的 30%-85%）
                stock = net_stock[merchant_id][pid]
                if stock > 0:
                    sold = max(1, int(stock * Decimal(str(rng.uniform(0.3, 0.85)))))
                    sold = min(sold, int(stock))
                    if not skip:
                        db.add(
                            InventoryRecord(
                                merchant_id=merchant_id,
                                product_id=pid,
                                sku_id=sku_uuid(merchant_id, pid),
                                quantity=qty(-sold),
                                unit=prod.unit,
                                unit_price=prod.default_price,
                                total_amount=(prod.default_price * Decimal(sold)).quantize(Decimal("0.01")),
                                event_type="sale",
                                event_time=days_ago(d, hour=18),
                                source="seed",
                            )
                        )
                        n += 1
                    net_stock[merchant_id][pid] -= Decimal(sold)

    await db.flush()
    print(f"  [+] 库存流水: {n} 条（三摊 30 天）")
    # 只保留还有存货的
    return {
        m: {pid: v for pid, v in items.items() if v > 0}
        for m, items in net_stock.items()
    }


async def seed_current_inventory(db, net_stock: dict) -> None:
    """当前库存汇总（current_inventory 视图表直接写入）。"""
    n = 0
    for merchant_id, items in net_stock.items():
        for pid, stock_qty in items.items():
            prod = PRODUCTS_BY_ID[pid]
            existing = await db.get(CurrentInventory, (merchant_id, pid))
            cost = prod.default_price * Decimal("0.65")
            if existing is None:
                db.add(
                    CurrentInventory(
                        merchant_id=merchant_id,
                        product_id=pid,
                        sku_id=sku_uuid(merchant_id, pid),
                        current_qty=stock_qty.quantize(Decimal("0.01")),
                        avg_cost=cost,
                        last_updated=days_ago(0, hour=9),
                    )
                )
                n += 1
            else:
                existing.current_qty = stock_qty.quantize(Decimal("0.01"))
                existing.avg_cost = cost
    await db.flush()
    print(f"  [+] 当前库存: {n} 条汇总")


def _qr_data(trace_code: str, prod_name: str, supplier_name: str) -> str:
    """批次二维码数据（演示食安追溯扫码）。"""
    return json.dumps(
        {
            "trace_code": trace_code,
            "product": prod_name,
            "supplier": supplier_name,
            "urls": {
                "verify": f"https://qiantan.example.com/t/{trace_code}",
                "report": f"https://qiantan.example.com/r/{trace_code}",
            },
        },
        ensure_ascii=False,
    )


async def seed_batches(db, net_stock: dict) -> None:
    """近 14 天进货批次：含 QR、产地、临期促销、1 个锁定批次。

    刻意制造演示故事点：
    - 菜摊：2 个临期叶菜批次（带促销价）→ 触发临期预警 + 数字孪生
    - 水果摊：1 个临期草莓批次 → 触发经验云清货建议
    - 肉摊：1 个锁定批次（快检不合格）→ 食安召回故事
    """
    rng = make_rng()
    existing = await db.execute(select(func.count()).select_from(BatchLifecycle))
    if int(existing.scalar_one()) > 0:
        print("  [=] 批次记录已存在，跳过")
        return

    n = 0
    for profile in MERCHANTS:
        merchant_id = profile.merchant_id
        # 每摊挑 3-5 个商品造近期批次
        my_products = list(products_for(merchant_id))
        chosen = rng.sample(my_products, min(5, len(my_products)))

        for i, prod in enumerate(chosen):
            days_old = rng.randint(1, 10)
            purchase_date = days_ago(days_old, hour=6)
            purchase_qty_val = Decimal(rng.randint(15, 40))
            remaining = max(Decimal("2"), purchase_qty_val * Decimal(str(rng.uniform(0.2, 0.6))))
            remaining = remaining.quantize(Decimal("0.01"))

            # 保质期：用商品定义
            expiry = purchase_date + timedelta(hours=prod.shelf_life_hours)

            # 状态决策（故事化）
            supplier_idx = 1 if prod.group in {"叶菜类", "根茎类", "瓜果类"} else (
                2 if prod.group == "水果类" else 3
            )
            supplier_name = {1: "老王蔬菜批发", 2: "张姐水果直供", 3: "李记肉联厂"}.get(
                supplier_idx, "老王蔬菜批发"
            )

            status = "sellable"
            promotion_price = None
            promotion_start = None
            promotion_end = None
            locked_at = None
            locked_reason = None
            inspection = "pass"
            origin = "山东寿光"

            hours_to_expiry = (expiry - days_ago(0)).total_seconds() / 3600
            # 菜摊叶菜 + 水果摊草莓：刻意临期 + 促销
            if prod.shelf_life_hours <= 96 and hours_to_expiry < prod.shelf_life_hours * 0.3:
                status = "near_expiry"
                promotion_price = (prod.default_price * Decimal("0.7")).quantize(Decimal("0.01"))
                promotion_start = days_ago(0, hour=8)
                promotion_end = expiry
                if prod.group == "水果类":
                    origin = "海南三亚"

            # 肉摊排骨：1 个锁定批次（快检不合格 → 食安故事）
            if profile.merchant_id == ALL_MERCHANT_IDS[2] and prod.name == "排骨":
                status = "locked"
                locked_at = days_ago(1, hour=10)
                locked_reason = "快检沙门氏菌疑似阳性，已锁定待复检"
                inspection = "fail"
                origin = "河南双汇"

            batch_label = f"{prod.name}-{purchase_date.strftime('%m%d')}-{i:02d}"
            trace_code = f"QT{merchant_id.hex[-4:].upper()}{prod.id:03d}{i:02d}"

            db.add(
                BatchLifecycle(
                    merchant_id=merchant_id,
                    product_id=prod.id,
                    sku_id=sku_uuid(merchant_id, prod.id),
                    batch_label=batch_label,
                    purchase_date=purchase_date,
                    purchase_qty=purchase_qty_val,
                    remaining_qty=remaining,
                    expiry_date=expiry,
                    promotion_price=promotion_price,
                    promotion_start_at=promotion_start,
                    promotion_end_at=promotion_end,
                    status=status,
                    supplier_id=supplier_id_for(merchant_id, supplier_idx),
                    supplier_name=supplier_name,
                    origin=origin,
                    unit_cost=(prod.default_price * Decimal("0.65")).quantize(Decimal("0.01")),
                    certificates=json.dumps(
                        {"quarantine": f"Q{2026}{prod.id:03d}", "inspection": "合格" if inspection == "pass" else "复检中"},
                        ensure_ascii=False,
                    ),
                    inspection_result=inspection,
                    locked_at=locked_at,
                    locked_reason=locked_reason,
                    locked_by="market_admin" if status == "locked" else None,
                    qr_data=_qr_data(trace_code, prod.name, supplier_name),
                    last_check=days_ago(0, hour=6),
                )
            )
            n += 1

    await db.flush()
    print(f"  [+] 批次追溯: {n} 条（含临期促销 + 锁定召回）")


async def seed_inventory_and_batches(db) -> dict:
    """库存层总入口。返回 net_stock 供 POS/采购模块复用。"""
    print("[2/7] 库存层（流水/当前库存/批次/环境）")
    await seed_environment(db)
    net_stock = await seed_inventory_records(db)
    await seed_current_inventory(db, net_stock)
    await seed_batches(db, net_stock)
    return {"net_stock": net_stock}
