"""advice/daily 冷启动：零数据/极少数据商户返回引导建议而非缺货告警风暴。

审计实测：新商户（零流水）GET /advice/daily 会收到逐商品
「当前已缺货，请立即补货！」告警（4 个品类全中），首屏全是噪音，且把
垃圾 Recommendation / pending AIAction 落库。

锁定行为：
- 记录覆盖 < 3 个 CST 业务日（含零数据）→ 引导型建议（is_onboarding），
  recommendation_ids 为空、不写任何 Recommendation/AIAction；
- ≥ 3 天数据 → 原有逐商品建议不变（正常三行格式 + 落库）。
"""

import sys
import uuid
from datetime import UTC, datetime, time, timedelta
from decimal import Decimal
from pathlib import Path


sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest
from sqlalchemy import func, select
from tests.conftest import TEST_MERCHANT_ID

from app.core.timezone import CST, cst_today
from app.models.ai_action import AIAction
from app.models.inventory import InventoryRecord
from app.models.recommendation import Recommendation


pytestmark = pytest.mark.asyncio


def _cst_utc_naive(day, hour=10) -> datetime:
    return (
        datetime.combine(day, time(hour, 0), tzinfo=CST).astimezone(UTC).replace(tzinfo=None)
    )


async def _seed_ledger(db_session, day, event_type: str = "sale"):
    """在指定 CST 业务日播种一条流水（销售 qty=-5 或采购 qty=+10）。"""
    mid = uuid.UUID(TEST_MERCHANT_ID)
    is_sale = event_type == "sale"
    async with db_session() as session:
        session.add(
            InventoryRecord(
                merchant_id=mid,
                product_id=1,
                quantity=Decimal("-5") if is_sale else Decimal("10"),
                unit="斤",
                unit_cost=None if is_sale else Decimal("0.5"),
                total_amount=Decimal("7.5") if is_sale else Decimal("5"),
                event_type=event_type,
                event_time=_cst_utc_naive(day, 10),
            )
        )
        await session.commit()


async def _count(db_session, model) -> int:
    async with db_session() as session:
        return int(
            (
                await session.execute(
                    select(func.count()).select_from(model).where(
                        model.merchant_id == uuid.UUID(TEST_MERCHANT_ID)
                    )
                )
            ).scalar()
            or 0
        )


# ------------------------------------------------------------------
# 冷启动：零数据 / 不足 3 天
# ------------------------------------------------------------------


async def test_zero_data_merchant_gets_onboarding_guidance(client, db_session):
    """零数据商户：返回引导建议，不输出缺货告警，不落库。"""
    resp = await client.get("/api/v1/advice/daily")
    assert resp.status_code == 200
    body = resp.json()
    assert body["code"] == 0
    data = body["data"]

    assert data["cold_start"] is True
    assert data["days_with_data"] == 0
    recs = data["recommendations"]
    assert 2 <= len(recs) <= 3
    assert all(r["is_onboarding"] for r in recs)
    # 引导文案：语音记账 / 商品库 / 连续记账
    joined = "".join(r["suggestion"] for r in recs)
    assert "记" in joined  # 「先语音记 3 笔账」类引导
    # 不输出缺货告警风暴
    assert "缺货" not in joined
    assert all(not r.get("risk_warning") for r in recs)
    # 兼容三行卡片结构（前端 advisor 页直接渲染）
    for r in recs:
        assert "suggestion" in r and isinstance(r["basis"], list) and "confidence" in r
    # 无落库建议 ID；env_summary 保持可用（test_timezone_boundaries 同口径）
    assert data["recommendation_ids"] == []
    assert "env_summary" in data

    # 引导路径不写 Recommendation / AIAction（否则冷启动每天生成垃圾行）
    assert await _count(db_session, Recommendation) == 0
    assert await _count(db_session, AIAction) == 0


async def test_two_days_of_data_still_onboarding(client, db_session):
    """仅 2 个业务日的流水：仍属冷启动（阈值 3 天）。"""
    today = cst_today()
    await _seed_ledger(db_session, today, "sale")
    await _seed_ledger(db_session, today - timedelta(days=1), "sale")

    resp = await client.get("/api/v1/advice/daily")
    data = resp.json()["data"]
    assert data["cold_start"] is True
    assert data["days_with_data"] == 2
    assert all(r["is_onboarding"] for r in data["recommendations"])


# ------------------------------------------------------------------
# 有数据：原有行为不变
# ------------------------------------------------------------------


async def test_three_days_data_returns_normal_advice(client, db_session):
    """≥3 个业务日流水：走原有逐商品建议路径（落库 + 三行格式）。"""
    today = cst_today()
    for offset in (2, 1, 0):
        await _seed_ledger(db_session, today - timedelta(days=offset), "sale")
    await _seed_ledger(db_session, today, "purchase")  # 补点库存让建议有据可依

    resp = await client.get("/api/v1/advice/daily")
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert not data.get("cold_start")
    assert not data.get("message") or data.get("cold_start") is not True

    recs = data["recommendations"]
    # conftest 播种 4 个在售品类（白菜/土豆/豆腐/猪肉）→ 逐商品建议
    assert len(recs) == 4
    assert all(not r.get("is_onboarding") for r in recs)
    for r in recs:
        assert "product_name" in r and r["suggestion"]
    # 落库 ID 全量返回
    assert len(data["recommendation_ids"]) == 4

    async with db_session() as session:
        saved = (
            await session.execute(
                select(Recommendation).where(
                    Recommendation.merchant_id == uuid.UUID(TEST_MERCHANT_ID)
                )
            )
        )
        assert len(saved.scalars().all()) == 4
