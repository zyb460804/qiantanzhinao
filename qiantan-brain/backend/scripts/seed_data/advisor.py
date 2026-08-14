"""种子分片：AI 建议 + 行动追踪 + 改价历史 + 语音流水。

覆盖 AI 参谋页面的"建议→采纳→执行→复盘"全闭环：
  - Recommendation：临期清货/补货/改价建议，含 was_adopted 与 actual_deviation
  - AIAction：建议落地为可执行动作（clearance/purchase/price），
    status 覆盖 executed / rejected / failed，演示"建议有没有被执行"
  - PriceHistory：AI 改价追踪（临期打折）
  - VoiceLog：语音记账历史（parsed/confirmed），演示方言 ASR + 语音闭环

幂等：按固定 UUID / 计数判重。
"""

from __future__ import annotations

import uuid
from decimal import Decimal

from sqlalchemy import func, select

from app.models.ai_action import AIAction
from app.models.catalog import PriceHistory
from app.models.recommendation import Recommendation
from app.models.voice import VoiceLog
from scripts.seed_data.common import (
    ALL_MERCHANT_IDS,
    MERCHANTS,
    days_ago,
    make_rng,
    products_for,
    sku_uuid,
)


async def seed_recommendations(db) -> dict:
    """AI 建议（含采纳/拒绝/偏差复盘）。"""
    rng = make_rng()
    existing = await db.execute(select(func.count()).select_from(Recommendation))
    if int(existing.scalar_one()) > 0:
        print("  [=] AI 建议已存在，跳过")
        return {}

    rec_ids: dict = {m: [] for m in ALL_MERCHANT_IDS}
    n = 0
    for profile in MERCHANTS:
        merchant_id = profile.merchant_id
        my_products = list(products_for(merchant_id))

        # 每摊 6 条建议，状态分布：3 采纳 / 2 拒绝 / 1 待处理
        scenarios = [
            "adopted_clearance",
            "adopted_purchase",
            "adopted_price",
            "rejected",
            "rejected",
            "pending",
        ]
        for i, scenario in enumerate(scenarios):
            prod = rng.choice(my_products)
            rec_id = uuid.uuid5(uuid.NAMESPACE_URL, f"rec-{merchant_id}-{i}")
            rec_ids[merchant_id].append((rec_id, prod, scenario))

            if scenario == "adopted_clearance":
                suggestion = f"{prod.name} 临期在即（剩 1 天），建议立即 7 折清货止损"
                basis = [
                    "shelf_life_remaining<20%",
                    "batch_near_expiry",
                    "historical_waste_rate=18%",
                ]
                risk = "降价后毛利压缩，但可避免全损"
                adopted = True
                deviation = Decimal(str(rng.uniform(-2, 5)))
            elif scenario == "adopted_purchase":
                suggestion = f"{prod.name} 库存仅剩 2 天销量，建议补货 30 斤"
                basis = ["forecast_demand=28斤", "current_stock=8斤", "lead_time=12h"]
                risk = "周末降雨可能影响销量"
                adopted = True
                deviation = Decimal(str(rng.uniform(-5, 3)))
            elif scenario == "adopted_price":
                suggestion = f"经验云显示同类摊位 {prod.name} 均价高于你 12%，建议提价"
                basis = ["experience_cloud_benchmark", "price_percentile=35%", "demand_inelastic"]
                risk = "提价过快可能流失老客"
                adopted = True
                deviation = Decimal(str(rng.uniform(0, 8)))
            elif scenario == "rejected":
                suggestion = f"{prod.name} 建议增加 20% 备货迎接周末"
                basis = ["weekend_uplift_forecast=25%"]
                risk = "天气转雨，客流或不及预期"
                adopted = False
                deviation = Decimal(str(rng.uniform(-10, 2)))
            else:  # pending
                suggestion = f"{prod.name} 近 3 天销量下滑 15%，建议小幅促销引流"
                basis = ["sales_decline_3d=-15%", "competitor_promo_detected"]
                risk = None
                adopted = None
                deviation = None

            db.add(
                Recommendation(
                    id=rec_id,
                    merchant_id=merchant_id,
                    product_id=prod.id,
                    sku_id=sku_uuid(merchant_id, prod.id),
                    suggestion=suggestion,
                    basis=basis,
                    risk_warning=risk,
                    recommended_qty=Decimal(rng.randint(20, 40))
                    if "purchase" in scenario
                    else None,
                    confidence=Decimal(str(round(rng.uniform(0.72, 0.94), 2))),
                    was_adopted=adopted,
                    actual_deviation=deviation,
                )
            )
            n += 1

    await db.flush()
    print(f"  [+] AI 建议: {n} 条（含采纳/拒绝/待处理）")
    return {"rec_ids": rec_ids}


async def seed_ai_actions(db, ctx: dict) -> None:
    """AI 行动追踪（建议→动作→执行/拒绝）。"""
    rng = make_rng()
    rec_map = ctx.get("rec_ids", {})
    existing = await db.execute(select(func.count()).select_from(AIAction))
    if int(existing.scalar_one()) > 0:
        print("  [=] AI 行动已存在，跳过")
        return

    n = 0
    for merchant_id, recs in rec_map.items():
        for i, (rec_id, prod, scenario) in enumerate(recs):
            if scenario == "pending":
                continue
            action_id = uuid.uuid5(uuid.NAMESPACE_URL, f"action-{merchant_id}-{i}")

            if scenario == "adopted_clearance":
                action_type, title, status = "clearance", f"清货-{prod.name}", "executed"
                payload = {"product_id": prod.id, "discount": 0.7, "batch_filter": "near_expiry"}
                result = {"sold_qty": rng.randint(8, 20), "revenue": float(rng.randint(60, 150))}
                executed_at = days_ago(3, hour=10)
            elif scenario == "adopted_purchase":
                action_type, title, status = "purchase", f"补货-{prod.name}", "executed"
                payload = {"product_id": prod.id, "qty": 30, "supplier": "老王蔬菜批发"}
                result = {"purchase_list_id": "已生成采购单", "actual_qty": 28}
                executed_at = days_ago(5, hour=9)
            elif scenario == "adopted_price":
                action_type, title, status = "price", f"改价-{prod.name}", "executed"
                payload = {
                    "product_id": prod.id,
                    "old_price": str(prod.default_price),
                    "new_price": str(prod.default_price + Decimal("1")),
                }
                result = {"price_history_id": "已记录", "revenue_lift": "+8%"}
                executed_at = days_ago(4, hour=11)
            else:  # rejected
                action_type, title, status = "stock", f"备货-{prod.name}", "rejected"
                payload = {"product_id": prod.id, "qty": 20}
                result = {"reason": "摊主判断周末有雨，暂不备货"}
                executed_at = None

            db.add(
                AIAction(
                    id=action_id,
                    merchant_id=merchant_id,
                    recommendation_id=rec_id,
                    action_type=action_type,
                    status=status,
                    title=title,
                    payload=payload,
                    result=result,
                    executed_by="merchant",
                    executed_at=executed_at,
                )
            )
            n += 1

        # 额外 1 条 failed 动作（演示失败追踪）
        prod = rng.choice(list(products_for(merchant_id)))
        fail_id = uuid.uuid5(uuid.NAMESPACE_URL, f"action-fail-{merchant_id}")
        db.add(
            AIAction(
                id=fail_id,
                merchant_id=merchant_id,
                action_type="clearance",
                status="failed",
                title=f"清货-{prod.name}",
                payload={"product_id": prod.id, "discount": 0.5},
                result={"error": "批次已被锁定，无法促销"},
                executed_by="system",
                executed_at=days_ago(2, hour=14),
            )
        )
        n += 1

    await db.flush()
    print(f"  [+] AI 行动: {n} 条（含执行/拒绝/失败）")


async def seed_price_history(db) -> None:
    """改价流水（AI 改价 + 手动改价追踪）。"""
    rng = make_rng()
    existing = await db.execute(select(func.count()).select_from(PriceHistory))
    if int(existing.scalar_one()) > 0:
        print("  [=] 改价历史已存在，跳过")
        return

    n = 0
    for profile in MERCHANTS:
        merchant_id = profile.merchant_id
        for prod in rng.sample(list(products_for(merchant_id)), 3):
            old = prod.default_price
            # 两次改价：一次 AI 临期降、一次手动回调
            new1 = (old * Decimal("0.85")).quantize(Decimal("0.01"))
            new2 = (old * Decimal("0.92")).quantize(Decimal("0.01"))
            for new_price, reason, source, when in [
                (new1, "ai_discount", "ai", days_ago(4, hour=10)),
                (new2, "clear_stock", "manual", days_ago(2, hour=16)),
            ]:
                db.add(
                    PriceHistory(
                        merchant_id=merchant_id,
                        sku_id=sku_uuid(merchant_id, prod.id),
                        old_price=old,
                        new_price=new_price,
                        reason=reason,
                        source=source,
                        changed_by=source,
                        created_at=when,
                    )
                )
                old = new_price
                n += 1
    await db.flush()
    print(f"  [+] 改价流水: {n} 条")


async def seed_voice_logs(db) -> None:
    """语音记账历史（含方言、确认、纠错）。"""
    rng = make_rng()
    existing = await db.execute(select(func.count()).select_from(VoiceLog))
    if int(existing.scalar_one()) > 0:
        print("  [=] 语音流水已存在，跳过")
        return

    # 方言语料模板（演示方言 ASR 映射 + 解析）
    templates = [
        ("今朝进了五十斤洋芋，一块五一斤", "purchase", "土豆", 50, "1.50"),
        ("卖脱三十个西红柿，四块一斤", "sale", "番茄", 30, "4.00"),
        ("张记饭店拿走八十块菜，先记账", "credit", None, 80, None),
        ("给老王结了昨天的五百块货款", "payment", None, 500, None),
        ("黄瓜坏了十斤，报损", "waste", "黄瓜", 10, None),
        ("今朝菠菜进价涨到两块八", "price_change", "菠菜", None, "2.80"),
    ]

    n = 0
    for profile in MERCHANTS:
        merchant_id = profile.merchant_id

        for i in range(10):
            text, etype, prod_name, amount_qty, price = rng.choice(templates)
            created = days_ago(
                rng.randint(0, 25), hour=rng.randint(7, 19), minute=rng.randint(0, 59)
            )
            parsed = {
                "event_type": etype,
                "product": prod_name,
                "quantity": amount_qty,
                "unit_price": price,
                "amount": round(amount_qty * float(price), 2) if price and amount_qty else None,
            }
            db.add(
                VoiceLog(
                    merchant_id=merchant_id,
                    asr_text=text,
                    parsed_event=parsed,
                    status=rng.choice(["confirmed", "confirmed", "confirmed", "parsed"]),
                    correction_count=rng.randint(0, 2),
                    client_id=f"seed-voice-{merchant_id.hex[-1]}-{i}",
                    created_at=created,
                )
            )
            n += 1

    await db.flush()
    print(f"  [+] 语音流水: {n} 条（含方言/纠错）")


async def seed_advisor(db) -> dict:
    """AI 参谋层总入口。"""
    print("[7/7] AI 参谋层（建议/行动/改价/语音）")
    ctx = await seed_recommendations(db)
    await seed_ai_actions(db, ctx)
    await seed_price_history(db)
    await seed_voice_logs(db)
    return ctx
