"""V5-H3/V5-M3 回归：天气日历键与销量 CST 业务日分桶的端到端对齐。

两条链路共用一个不变量：**业务日一律按 CST（UTC+8）切，与部署服务器时区无关**。
UTC 16:00-24:00（= CST 次日 0-8 点）是错位高发窗口，本文件用例都在这个
窗口内构造数据，锁死：

1. 天气写端（weather.py）与读端（environment.py）用同一把 CST 尺子 —— 否则
   在非 CST 时区部署机上 0-8 点会写错/读错日历键，缓存永不命中。
2. 异常扫描（anomalies.py）按 CST 业务日分桶 —— 旧 SQL func.date 分桶会把
   CST 0-8 点的销售记到前一天，「最后一天 = current_value」随之取错。

（原第 2 条「经验云天气关联」随 app/services/experience_cloud.py 下线一并移除。）
"""

import sys
import uuid
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from pathlib import Path


sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest
from sqlalchemy import select
from tests.conftest import TEST_MERCHANT_ID  # noqa: F401

from app.core.timezone import CST, cst_today
from app.models.environment import EnvironmentRecord
from app.models.inventory import InventoryRecord


def _cst_to_utc_naive(d: date, hour: int, minute: int) -> datetime:
    """CST 时刻 → naive UTC（DB 列存储形态）。"""
    return datetime.combine(d, time(hour, minute), tzinfo=CST).astimezone(UTC).replace(tzinfo=None)


def _freeze_clock(monkeypatch, utc_naive: datetime) -> None:
    """冻结 app.core.timezone 的时钟（模拟 utc 层）。

    只拦截 timezone 模块内的 datetime.now；被测代码若退回 date.today()
    （服务器本地日历），拿到的是真实日期，与冻结的 CST 日必然不同——
    用 2026-01-02 这类远离真实日期的冻结值保证回归可被确定性地抓住。
    """
    import app.core.timezone as tz

    class _FrozenDatetime(datetime):
        @classmethod
        def now(cls, tzinfo=None):
            if tzinfo is not None:
                return utc_naive.replace(tzinfo=UTC).astimezone(tzinfo)
            return utc_naive

    monkeypatch.setattr(tz, "datetime", _FrozenDatetime)


# ------------------------------------------------------------------
# 1. 天气写端：UTC 17:30（= CST 次日 01:30）必须写到 CST 次日
# ------------------------------------------------------------------


def test_mock_today_uses_cst_day_when_utc_behind(monkeypatch):
    """无 API key 的 mock 天气：日历键 = CST 业务日，不是 UTC 日。"""
    from app.services.weather import _mock_today

    _freeze_clock(monkeypatch, datetime(2026, 1, 1, 17, 30))  # = CST 2026-01-02 01:30
    record = _mock_today("上海")
    assert record["date"] == date(2026, 1, 2)
    assert record["day_of_week"] == date(2026, 1, 2).weekday()


def test_mock_forecast_starts_from_cst_day(monkeypatch):
    """mock 预报从 CST「今天」起排 3 天（i=0 是今天）。"""
    from app.services.weather import _mock_forecast

    _freeze_clock(monkeypatch, datetime(2026, 1, 1, 17, 30))
    forecasts = _mock_forecast("上海", 3)
    assert [f["date"] for f in forecasts] == [
        date(2026, 1, 2),
        date(2026, 1, 3),
        date(2026, 1, 4),
    ]


@pytest.mark.asyncio
async def test_env_today_writes_and_reads_cst_day(client, db_session, monkeypatch):
    """/env/today 在 UTC 16-24 点窗口：落库与缓存命中都按 CST 业务日。"""
    _freeze_clock(monkeypatch, datetime(2026, 1, 1, 17, 30))  # = CST 2026-01-02 01:30

    resp = await client.get("/api/v1/env/today", params={"city": "上海"})
    assert resp.status_code == 200
    assert resp.json()["data"]["date"] == "2026-01-02"

    async with db_session() as session:
        dates = (await session.execute(select(EnvironmentRecord.date))).scalars().all()
    assert dates == [date(2026, 1, 2)]

    # 读端同一把 CST 尺子 → 第二次请求命中缓存；若读写日历键不一致，
    # 会再插一行不同日期的环境记录（信封 AnyResponse 会剥掉 source 字段，
    # 故用「行数仍为 1」锁定缓存命中）
    resp2 = await client.get("/api/v1/env/today", params={"city": "上海"})
    assert resp2.status_code == 200
    assert resp2.json()["data"]["date"] == "2026-01-02"

    async with db_session() as session:
        dates = (await session.execute(select(EnvironmentRecord.date))).scalars().all()
    assert dates == [date(2026, 1, 2)]


# ------------------------------------------------------------------
# 2. 异常扫描：CST 0-8 点的销售落在 CST 当天桶
# ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_anomaly_scan_buckets_by_cst_day(client, db_session):
    """scan 的 current_value 取「CST 今天」的销量：00:30 的销售是今天，不是昨天。

    旧实现（SQL func.date UTC 分桶）会把 CST 00:30 的 50 斤并入前一 UTC 日，
    今天被补 0 → current_value 错取 0；新实现直接取 50。
    """
    mid = uuid.UUID(TEST_MERCHANT_ID)
    today = cst_today()

    # 白菜：近 7 个 CST 日正午各一笔稳定销量（今天为最后一桶），不触发异常
    bai_cai_vals = [10, 11, 9, 10, 11, 9, 10]
    # 土豆：前 6 个 CST 日正午稳定销量，今天 CST 00:30 突然大额（50）
    tu_dou_vals = [10, 11, 9, 10, 11, 9]

    rows = []
    for i, qty in enumerate(bai_cai_vals):
        d = today - timedelta(days=6 - i)
        rows.append(_sale(mid, 1, qty, _cst_to_utc_naive(d, 12, 0)))
    for i, qty in enumerate(tu_dou_vals):
        d = today - timedelta(days=6 - i)
        rows.append(_sale(mid, 2, qty, _cst_to_utc_naive(d, 12, 0)))
    rows.append(_sale(mid, 2, 50, _cst_to_utc_naive(today, 0, 30)))  # CST 今天 00:30

    async with db_session() as session:
        session.add_all(rows)
        await session.commit()

    res = await client.get("/api/v1/anomalies/scan?days=30")
    assert res.status_code == 200
    anomalies = res.json()["data"]["anomalies"]

    names = [a["product_name"] for a in anomalies]
    assert "土豆" in names
    assert "白菜" not in names  # 稳定序列不误报

    potato = next(a for a in anomalies if a["product_name"] == "土豆")
    assert potato["current_value"] == 50  # CST 00:30 的销售是「今天」的当前值
    assert len(potato["history"]) == 6


def _sale(
    merchant_id: uuid.UUID, product_id: int, qty: int, event_time: datetime
) -> InventoryRecord:
    return InventoryRecord(
        merchant_id=merchant_id,
        product_id=product_id,
        quantity=Decimal(str(qty)),
        unit="斤",
        event_type="sale",
        total_amount=Decimal(str(qty * 2)),
        event_time=event_time,
        source="manual",
    )
