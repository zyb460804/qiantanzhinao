"""ops 数据导出口径回归（V6 导出审计）。

1. /ops/export/inventory：原读 CurrentInventory 快照表 —— 该表生产路径零写入
   （仅 scripts/seed_data 写过），导出永远空表/陈旧，与 /inventory/current 实时
   聚合矛盾。现改为同口径 InventoryRecord 实时聚合（排除 is_voided、加权均价）。
2. /ops/export/sales、/ops/export/waste：日期参数是摊主在 CST 日历上选的日期，
   落库时间为 naive UTC，必须按 CST 业务日换算（原 naive UTC 拼接会漏掉 CST
   0-8 点的记录）；waste 另需排除已撤销（is_voided）流水。
"""

import sys
import uuid
from datetime import UTC, date, datetime, time
from decimal import Decimal
from pathlib import Path


sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest
from tests.conftest import TEST_MERCHANT_ID

from app.core.timezone import CST, utc_now
from app.models.inventory import InventoryRecord
from app.models.pos import SaleOrder


pytestmark = pytest.mark.asyncio


def _cst_utc_naive(day: date, hour: int, minute: int = 0) -> datetime:
    """CST 时刻 → naive UTC（DB 列存储形态）。"""
    return (
        datetime.combine(day, time(hour, minute), tzinfo=CST)
        .astimezone(UTC)
        .replace(tzinfo=None)
    )


MID = uuid.UUID(TEST_MERCHANT_ID)


# ------------------------------------------------------------------
# /ops/export/inventory — 实时聚合口径
# ------------------------------------------------------------------


async def test_export_inventory_realtime_aggregation(client, db_session):
    """当前库存 = InventoryRecord 实时求和（排除作废），均价 = 入库加权均价。"""
    async with db_session() as session:
        session.add_all(
            [
                # 采购 10斤 @0.5
                InventoryRecord(
                    merchant_id=MID,
                    product_id=1,
                    quantity=Decimal("10"),
                    unit="斤",
                    unit_cost=Decimal("0.5"),
                    total_amount=Decimal("5"),
                    event_type="purchase",
                    event_time=utc_now(),
                ),
                # 销售 4斤（无成本）
                InventoryRecord(
                    merchant_id=MID,
                    product_id=1,
                    quantity=Decimal("-4"),
                    unit="斤",
                    unit_price=Decimal("1.5"),
                    total_amount=Decimal("6"),
                    event_type="sale",
                    event_time=utc_now(),
                ),
                # 已作废的采购 +5斤：不得计入
                InventoryRecord(
                    merchant_id=MID,
                    product_id=1,
                    quantity=Decimal("5"),
                    unit="斤",
                    unit_cost=Decimal("0.5"),
                    total_amount=Decimal("2.5"),
                    event_type="purchase",
                    event_time=utc_now(),
                    is_voided=True,
                ),
            ]
        )
        await session.commit()

    res = await client.get("/api/v1/ops/export/inventory")
    assert res.status_code == 200
    body = res.json()
    assert body["code"] == 0
    rows = body["data"]["rows"]
    # 实时聚合：10 - 4 = 6斤（作废的 +5 不计），加权均价 = 0.5
    assert len(rows) == 1
    assert rows[0]["商品"] == "白菜"
    assert rows[0]["当前库存"] == 6.0
    assert rows[0]["平均成本"] == 0.5
    assert "白菜" in body["data"]["csv"]


async def test_export_inventory_empty_ledger_returns_empty(client):
    """零流水商户导出空表（旧口径下取决于 seed 是否写过快照表）。"""
    res = await client.get("/api/v1/ops/export/inventory")
    assert res.status_code == 200
    assert res.json()["data"]["rows"] == []


# ------------------------------------------------------------------
# /ops/export/sales — CST 业务日边界
# ------------------------------------------------------------------


async def test_export_sales_cst_day_boundary(client, db_session):
    """CST 1/5 01:00（= UTC 1/4 17:00）的订单必须落在 1/5 的导出窗口。"""
    async with db_session() as session:
        session.add(
            SaleOrder(
                merchant_id=MID,
                order_no="TZ-EXP-CST-0105",
                status="paid",
                total_amount=Decimal("88"),
                paid_amount=Decimal("88"),
                created_at=_cst_utc_naive(date(2026, 1, 5), 1, 0),
            )
        )
        await session.commit()

    res = await client.get(
        "/api/v1/ops/export/sales", params={"start_date": "2026-01-05", "end_date": "2026-01-05"}
    )
    assert res.status_code == 200
    rows = res.json()["data"]["rows"]
    assert len(rows) == 1
    assert rows[0]["订单号"] == "TZ-EXP-CST-0105"
    assert rows[0]["实付"] == 88.0


async def test_export_sales_excludes_day_before_cst_window(client, db_session):
    """CST 1/4 23:00 的订单不属于 1/5 起的导出窗口。"""
    async with db_session() as session:
        session.add(
            SaleOrder(
                merchant_id=MID,
                order_no="TZ-EXP-CST-0104",
                status="paid",
                total_amount=Decimal("10"),
                paid_amount=Decimal("10"),
                created_at=_cst_utc_naive(date(2026, 1, 4), 23, 0),
            )
        )
        await session.commit()

    res = await client.get(
        "/api/v1/ops/export/sales", params={"start_date": "2026-01-05", "end_date": "2026-01-05"}
    )
    assert res.status_code == 200
    assert res.json()["data"]["rows"] == []


# ------------------------------------------------------------------
# /ops/export/waste — CST 业务日边界 + 排除作废
# ------------------------------------------------------------------


async def test_export_waste_cst_boundary_and_voided(client, db_session):
    """CST 1/5 01:00 的报损计入 1/5 导出；已作废报损不导出。"""
    async with db_session() as session:
        session.add_all(
            [
                InventoryRecord(
                    merchant_id=MID,
                    product_id=1,
                    quantity=Decimal("-2"),
                    unit="斤",
                    total_amount=Decimal("3"),
                    event_type="waste",
                    event_time=_cst_utc_naive(date(2026, 1, 5), 1, 0),
                    notes="腐烂: 测试",
                ),
                # 已撤销的报损：批次已回滚，不得再进导出
                InventoryRecord(
                    merchant_id=MID,
                    product_id=1,
                    quantity=Decimal("-7"),
                    unit="斤",
                    total_amount=Decimal("9"),
                    event_type="waste",
                    event_time=_cst_utc_naive(date(2026, 1, 5), 2, 0),
                    notes="腐烂: 已撤销",
                    is_voided=True,
                ),
            ]
        )
        await session.commit()

    res = await client.get(
        "/api/v1/ops/export/waste", params={"start_date": "2026-01-05", "end_date": "2026-01-05"}
    )
    assert res.status_code == 200
    rows = res.json()["data"]["rows"]
    assert len(rows) == 1
    assert rows[0]["成本"] == 3.0
    assert "已撤销" not in res.json()["data"]["csv"]
