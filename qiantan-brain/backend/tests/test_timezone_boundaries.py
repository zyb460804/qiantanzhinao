"""审计 C4 回归：event_time/created_at 为 naive UTC，读端日界必须按 CST 业务日切。

播种一条 CST 00:30（= 前一 UTC 日 16:30）的销售/订单，断言它落在 CST 当天的
日报/趋势/月报里，且不出现在前一天/前一月——旧实现按服务器本地日界切，
CST 凌晨 0-8 点的经营数据会被归入前一天。

纯函数部分用固定日期，与运行机器时区无关；接口部分与「今天」相对，
旧实现在任意部署时区下都过不了其中至少一侧断言。
"""

import sys
import uuid
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from pathlib import Path


sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest
from tests.conftest import TEST_MERCHANT_ID

from app.core.timezone import (
    CST,
    cst_date_of_utc_naive,
    cst_day_bounds_utc,
    cst_days_ago_bounds_utc,
    cst_month_bounds_utc,
    cst_today,
    utc_now,
)


# ------------------------------------------------------------------
# 纯函数：固定日期，任意机器时区下结果一致
# ------------------------------------------------------------------


def test_cst_day_bounds_utc_shifts_eight_hours():
    """CST 8/14 的 [00:00, 24:00) = UTC naive [8/13 16:00, 8/14 16:00)。"""
    start, end = cst_day_bounds_utc(date(2026, 8, 14))
    assert start == datetime(2026, 8, 13, 16, 0)
    assert end == datetime(2026, 8, 14, 16, 0)
    assert start.tzinfo is None and end.tzinfo is None


def test_cst_month_bounds_utc():
    """CST 业务月界换算；跨年月（12 月）同样正确。"""
    assert cst_month_bounds_utc(2026, 8) == (
        datetime(2026, 7, 31, 16, 0),
        datetime(2026, 8, 31, 16, 0),
    )
    assert cst_month_bounds_utc(2026, 12) == (
        datetime(2026, 11, 30, 16, 0),
        datetime(2026, 12, 31, 16, 0),
    )


def test_cst_days_ago_bounds_utc_covers_today():
    """近 1 天窗口恰好是今天的完整 CST 业务日，且覆盖当前时刻。"""
    start, end = cst_days_ago_bounds_utc(1)
    assert end - start == timedelta(days=1)
    now_naive = utc_now().replace(tzinfo=None)
    assert start <= now_naive < end


def test_cst_date_of_utc_naive():
    """UTC 16:30 已是 CST 次日 00:30；15:59 仍属前一个 CST 业务日。"""
    assert cst_date_of_utc_naive(datetime(2026, 8, 13, 16, 30)) == date(2026, 8, 14)
    assert cst_date_of_utc_naive(datetime(2026, 8, 13, 16, 0)) == date(2026, 8, 14)
    assert cst_date_of_utc_naive(datetime(2026, 8, 13, 15, 59)) == date(2026, 8, 13)


# ------------------------------------------------------------------
# 接口回归：CST 00:30 的销售必须记在 CST 当天
# ------------------------------------------------------------------


def _cst_today_0030_utc_naive() -> datetime:
    """CST 今天 00:30 对应的 naive UTC（= 前一 UTC 日 16:30）。"""
    cst_dt = datetime.combine(cst_today(), time(0, 30), tzinfo=CST)
    return cst_dt.astimezone(UTC).replace(tzinfo=None)


async def _seed_midnight_sale(db_session, total_amount="12.5", quantity="-5"):
    from app.models.inventory import InventoryRecord

    mid = uuid.UUID(TEST_MERCHANT_ID)
    async with db_session() as session:
        session.add(
            InventoryRecord(
                merchant_id=mid,
                product_id=1,
                quantity=Decimal(quantity),
                unit="斤",
                event_type="sale",
                total_amount=Decimal(total_amount),
                event_time=_cst_today_0030_utc_naive(),
            )
        )
        await session.commit()


@pytest.mark.asyncio
async def test_daily_report_cst_midnight_sale(client, db_session):
    """CST 00:30 的销售计入今天的日报，不计入昨日对比。"""
    await _seed_midnight_sale(db_session)

    resp = await client.get("/api/v1/reports/daily", params={"merchant_id": TEST_MERCHANT_ID})
    assert resp.status_code == 200
    d = resp.json()["data"]
    assert d["date"] == cst_today().isoformat()
    assert d["revenue"] == 12.5
    assert d["yesterday_revenue"] == 0


@pytest.mark.asyncio
async def test_trends_cst_day_bucket(client, db_session):
    """趋势序列按 CST 日分桶：00:30 的销售落在今天，不落在昨天。"""
    await _seed_midnight_sale(db_session)

    resp = await client.get(
        "/api/v1/reports/trends", params={"merchant_id": TEST_MERCHANT_ID, "days": 7}
    )
    assert resp.status_code == 200
    by_date = {t["date"]: t for t in resp.json()["data"]}
    today_str = cst_today().isoformat()
    yesterday_str = (cst_today() - timedelta(days=1)).isoformat()
    assert by_date[today_str]["revenue"] == 12.5
    assert by_date[yesterday_str]["revenue"] == 0


@pytest.mark.asyncio
async def test_weekly_and_monthly_include_cst_midnight_sale(client, db_session):
    """周报（近 7 业务日）与月报（近 30 业务日）都从 CST 日界起算。"""
    await _seed_midnight_sale(db_session)

    weekly = (await client.get("/api/v1/reports/weekly")).json()["data"]
    monthly = (await client.get("/api/v1/reports/monthly")).json()["data"]
    assert weekly["week_revenue"] == 12.5
    assert monthly["week_revenue"] == 12.5  # 30 天报表沿用 week_* 字段名


@pytest.mark.asyncio
async def test_twin_dashboard_cst_midnight_sale(client, db_session):
    """数字孪生首页「今日」口径 = CST 业务日。"""
    await _seed_midnight_sale(db_session)

    resp = await client.get("/api/v1/twin/dashboard")
    assert resp.status_code == 200
    assert resp.json()["data"]["today_revenue"] == 12.5


@pytest.mark.asyncio
async def test_twin_business_mirror_cst_day_bucket(client, db_session):
    """经营镜像按 CST 日分桶（原 SQL func.date 只能得到 UTC 日期键）。"""
    await _seed_midnight_sale(db_session)

    resp = await client.get("/api/v1/twin/business-mirror")
    assert resp.status_code == 200
    by_date = {x["date"]: x for x in resp.json()["data"]["sales_7d"]}
    today_entry = by_date[cst_today().isoformat()]
    yesterday_entry = by_date[(cst_today() - timedelta(days=1)).isoformat()]
    assert today_entry["revenue"] == 12.5
    assert today_entry["sale_count"] == 1
    assert yesterday_entry["revenue"] == 0
    assert yesterday_entry["sale_count"] == 0


@pytest.mark.asyncio
async def test_expense_monthly_report_cst_month_boundary(client, db_session):
    """CST 8/1 00:30 的订单（= UTC 7/31 16:30）计入 8 月月报，不串到 7 月。"""
    from app.models.pos import SaleOrder

    mid = uuid.UUID(TEST_MERCHANT_ID)
    async with db_session() as session:
        session.add(
            SaleOrder(
                merchant_id=mid,
                order_no="TZ-CST-0801",
                status="paid",
                total_amount=Decimal("88"),
                created_at=datetime(2026, 7, 31, 16, 30),  # = CST 2026-08-01 00:30
            )
        )
        await session.commit()

    aug = await client.get("/api/v1/expenses/monthly-report?month=2026-08")
    jul = await client.get("/api/v1/expenses/monthly-report?month=2026-07")
    assert aug.status_code == 200
    assert jul.status_code == 200
    assert aug.json()["data"]["revenue"] == 88.0
    assert jul.json()["data"]["revenue"] == 0.0


# ------------------------------------------------------------------
# 预测/建议：按 CST 日聚合 + 环境真实零值
# ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_forecast_history_cst_day_bucket(db_session):
    """销量历史按 CST 日聚合：00:30 的销售算今天，不算昨天。"""
    from app.services.forecast import _get_daily_sales_history

    await _seed_midnight_sale(db_session)
    mid = uuid.UUID(TEST_MERCHANT_ID)
    async with db_session() as session:
        history = await _get_daily_sales_history(session, mid, 1, days=7)

    assert history[-1]["date"] == cst_today().isoformat()
    assert history[-1]["qty"] == 5.0
    assert history[-2]["date"] == (cst_today() - timedelta(days=1)).isoformat()
    assert history[-2]["qty"] == 0.0


@pytest.mark.asyncio
async def test_advice_env_zero_values_not_overwritten(client, db_session):
    """temp_high=0°C / rainfall_prob=0 是真实值，不得被 falsy 回退成 25/20。"""
    from app.models.environment import EnvironmentRecord

    async with db_session() as session:
        session.add(
            EnvironmentRecord(
                date=cst_today(),
                city="上海",
                temp_high=Decimal("0"),
                temp_low=Decimal("-5"),
                rainfall_prob=Decimal("0"),
            )
        )
        await session.commit()

    resp = await client.get("/api/v1/advice/daily", params={"merchant_id": TEST_MERCHANT_ID})
    assert resp.status_code == 200
    env = resp.json()["data"]["env_summary"]
    assert env["temp_high"] == 0
    assert env["rainfall_prob"] == 0
