"""reports 期间参数回归：daily ?date= / weekly ?end_date= / monthly ?end_date=。

审计实测：GET /reports/daily 路由签名无 date 参数，任何 ?date= 被 FastAPI
静默丢弃、永远只返回今天；weekly/monthly 同样只锚定「今天」。本文件锁定：

1. 指定历史日期/期间锚点返回对应 CST 业务日/窗口的数据；
2. 默认调用（不带参数）行为不变（今天/以今天收尾的窗口）；
3. 历史窗口必须封上界——窗口之后的新记录不得计入；
4. 非法日期由 FastAPI 参数解析自动 422。
"""

import sys
import uuid
from datetime import UTC, datetime, time, timedelta
from decimal import Decimal
from pathlib import Path


sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest
from tests.conftest import TEST_MERCHANT_ID

from app.core.timezone import CST, cst_today
from app.models.inventory import InventoryRecord


pytestmark = pytest.mark.asyncio


def _cst_utc_naive(day, hour=10, minute=0) -> datetime:
    """CST 时刻 → naive UTC（DB 列存储形态）。"""
    return (
        datetime.combine(day, time(hour, minute), tzinfo=CST)
        .astimezone(UTC)
        .replace(tzinfo=None)
    )


async def _seed_sale(
    db_session, day, amount: str, hour: int = 10, minute: int = 0, product_id: int = 1
):
    """在指定 CST 业务日播种一条销售流水。"""
    mid = uuid.UUID(TEST_MERCHANT_ID)
    async with db_session() as session:
        session.add(
            InventoryRecord(
                merchant_id=mid,
                product_id=product_id,
                quantity=Decimal("-5"),
                unit="斤",
                event_type="sale",
                total_amount=Decimal(amount),
                event_time=_cst_utc_naive(day, hour),
            )
        )
        await session.commit()


# ------------------------------------------------------------------
# GET /api/v1/reports/daily?date=
# ------------------------------------------------------------------


async def test_daily_historical_date_returns_that_day(client, db_session):
    """?date=历史日 → 返回该 CST 业务日数据；默认调用仍只看今天。"""
    target = cst_today() - timedelta(days=10)
    # 两条都在目标 CST 业务日内：一条上午 10:00，一条凌晨 00:30（= 前一 UTC 日 16:30，
    # 锁死日界按 CST 切而不是把 date 参数当 UTC 日期）
    await _seed_sale(db_session, target, "12.5", hour=10)
    await _seed_sale(db_session, target, "7.5", hour=0, minute=30)

    resp = await client.get(
        "/api/v1/reports/daily", params={"date": target.isoformat()}
    )
    assert resp.status_code == 200
    d = resp.json()["data"]
    assert d["date"] == target.isoformat()
    assert d["revenue"] == 20.0
    # 摘要随日期平移（不再是「今日」）
    assert "今日" not in d["ai_summary"]
    assert f"{target.month}月{target.day}日" in d["ai_summary"]

    # 默认（不带参数）仍只统计今天：10 天前的数据不得泄入
    resp_default = await client.get("/api/v1/reports/daily")
    assert resp_default.status_code == 200
    assert resp_default.json()["data"]["revenue"] == 0
    assert resp_default.json()["data"]["date"] == cst_today().isoformat()


async def test_daily_historical_date_yesterday_comparison(client, db_session):
    """?date=D → yesterday_revenue = D-1 业务日营业额，环比正确。"""
    d = cst_today() - timedelta(days=5)
    await _seed_sale(db_session, d, "30")
    await _seed_sale(db_session, d - timedelta(days=1), "20")

    resp = await client.get("/api/v1/reports/daily", params={"date": d.isoformat()})
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["revenue"] == 30.0
    assert data["yesterday_revenue"] == 20.0
    assert data["revenue_change_pct"] == 50.0


async def test_daily_invalid_date_returns_422(client):
    """非法日期由 FastAPI 参数解析自动 422，不再被静默丢弃。"""
    for bad in ("2026-02-30", "not-a-date", "20260815"):
        resp = await client.get("/api/v1/reports/daily", params={"date": bad})
        assert resp.status_code == 422, bad


# ------------------------------------------------------------------
# GET /api/v1/reports/weekly?end_date=
# ------------------------------------------------------------------


async def test_weekly_end_date_param(client, db_session):
    """?end_date=历史日 → 窗口平移到 [end_date-6, end_date]，且窗口后记录不得计入。"""
    today = cst_today()
    old_day = today - timedelta(days=8)
    recent_day = today - timedelta(days=6)
    await _seed_sale(db_session, old_day, "15")     # 只应进历史窗口
    await _seed_sale(db_session, recent_day, "99")  # 只应进默认窗口

    # 默认：以今天收尾 → 只有 recent_day 计入
    default = (await client.get("/api/v1/reports/weekly")).json()["data"]
    assert default["week_revenue"] == 99.0

    # end_date=old_day → 窗口 [today-14, old_day]：含 15，不含 99（上界封口）
    hist = (
        await client.get(
            "/api/v1/reports/weekly", params={"end_date": old_day.isoformat()}
        )
    ).json()["data"]
    assert hist["week_revenue"] == 15.0
    assert len(hist["daily_trends"]) == 7
    assert hist["daily_trends"][0]["date"] == (old_day - timedelta(days=6)).isoformat()
    assert hist["daily_trends"][-1]["date"] == old_day.isoformat()
    # 历史锚点的摘要不再自称「本周」
    assert "近7日" in hist["ai_summary"]


async def test_weekly_invalid_end_date_returns_422(client):
    resp = await client.get("/api/v1/reports/weekly", params={"end_date": "2026-99-01"})
    assert resp.status_code == 422


# ------------------------------------------------------------------
# GET /api/v1/reports/monthly?end_date=
# ------------------------------------------------------------------


async def test_monthly_end_date_param(client, db_session):
    """?end_date=历史日 → 30 天窗口平移到 [end_date-29, end_date]。"""
    today = cst_today()
    old_day = today - timedelta(days=40)
    recent_day = today - timedelta(days=5)
    await _seed_sale(db_session, old_day, "15")
    await _seed_sale(db_session, recent_day, "99")

    default = (await client.get("/api/v1/reports/monthly")).json()["data"]
    assert default["week_revenue"] == 99.0  # 30 天月报沿用 week_* 字段名

    hist = (
        await client.get(
            "/api/v1/reports/monthly", params={"end_date": old_day.isoformat()}
        )
    ).json()["data"]
    assert hist["week_revenue"] == 15.0
    assert len(hist["daily_trends"]) == 30
    assert hist["daily_trends"][0]["date"] == (old_day - timedelta(days=29)).isoformat()
    assert hist["daily_trends"][-1]["date"] == old_day.isoformat()


async def test_monthly_invalid_end_date_returns_422(client):
    resp = await client.get("/api/v1/reports/monthly", params={"end_date": "abc"})
    assert resp.status_code == 422
