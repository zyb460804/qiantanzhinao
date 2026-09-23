"""QA 口径统一回归（历史 QA 确认的三个账务口径问题）。

1. 日结净额口径：total_sales = 销售总额 - 当日退款（净销售额），
   refunds_total 单列保留退款合计；净额恒等式
   total_sales(净) = total_payments(净) + credit_amount 恒成立，diff==0。
2. 经营台三指标统一：收入=净销售额（扣退款）、成本=已售成本（COGS）、
   毛利=收入-成本；采购支出单列（today_purchase_cost），不与成本混用。
3. 报表 slow_moving 商品名与 /inventory/current 同源：
   SKU 标准名（ProductSKU.name）优先，兜底「商品{id}」。
"""

import sys
import uuid
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest
from sqlalchemy import select
from tests.conftest import TEST_MERCHANT_ID

from app.core.timezone import local_now, utc_now
from app.services.batch import create_batch

pytestmark = pytest.mark.asyncio


async def _seed_stock(db_session, quantity: int = 10, unit_cost: Decimal = Decimal("2.50")):
    """种一个有成本批次的库存（与 POS 日结测试同套路）。"""
    async with db_session() as session:
        await create_batch(
            session,
            uuid.UUID(TEST_MERCHANT_ID),
            1,
            "白菜",
            f"白菜-qa-{uuid.uuid4().hex[:6]}",
            Decimal(str(quantity)),
            unit_cost=unit_cost,
        )
        await session.commit()


async def _add_ledger_record(
    db_session,
    event_type: str,
    quantity: str,
    total_amount: str,
    unit_cost: str | None = None,
    product_id: int = 1,
    sku_id: uuid.UUID | None = None,
):
    """直接插一条当日库存台账流水（twin/dashboard 与日报的统计来源）。"""
    from app.models.inventory import InventoryRecord

    async with db_session() as session:
        session.add(
            InventoryRecord(
                merchant_id=uuid.UUID(TEST_MERCHANT_ID),
                product_id=product_id,
                sku_id=sku_id,
                quantity=Decimal(quantity),
                unit="斤",
                unit_cost=Decimal(unit_cost) if unit_cost else None,
                total_amount=Decimal(total_amount),
                event_type=event_type,
                event_time=utc_now(),
                source="test",
            )
        )
        await session.commit()


# ------------------------------------------------------------------
# 1. 日结净额口径：refunds_total + diff 自洽
# ------------------------------------------------------------------


async def _cash_order(client, client_id: str, qty: int, price: float) -> str:
    res = await client.post(
        "/api/v1/pos/orders",
        json={
            "client_id": client_id,
            "payment_method": "cash",
            "items": [{"product_id": 1, "quantity": qty, "unit": "斤", "unit_price": price}],
        },
    )
    assert res.status_code == 200
    return res.json()["data"]["order_id"]


async def test_settlement_close_net_sales_and_refunds_total(client, db_session):
    """现金单 7 当日整单退 → 关闭日结：净销售==0、refunds_total==7、diff==0，
    且关闭快照回显（GET）带 refunds_total。"""
    await _seed_stock(db_session)
    order_id = await _cash_order(client, "qa-net-close-001", 2, 3.5)
    refund = await client.post(
        f"/api/v1/pos/orders/{order_id}/refund",
        json={"reason": "QA 口径回归", "return_to_stock": True},
    )
    assert refund.status_code == 200

    settle_date = local_now().date().isoformat()
    closed = await client.post(f"/api/v1/pos/daily-settlement/{settle_date}/close")
    assert closed.status_code == 200
    data = closed.json()["data"]
    # 净额口径：total_sales = 7 - 7 = 0；退款合计单列；毛利 = 净销售 - 已售成本
    assert data["total_sales"] == 0.0
    assert data["refunds_total"] == 7.0
    assert data["total_payments"] == 0.0
    assert data["estimated_cogs"] == 0.0
    assert data["estimated_gross_profit"] == 0.0
    assert data["diff_amount"] == 0.0

    # 已关闭日结回显快照：净销售与 refunds_total 均不丢
    echo = await client.get(f"/api/v1/pos/daily-settlement/{settle_date}")
    assert echo.status_code == 200
    echo_data = echo.json()["data"]
    assert echo_data["status"] == "closed"
    assert echo_data["total_sales"] == 0.0
    assert echo_data["refunds_total"] == 7.0


async def test_settlement_partial_refund_identity(client, db_session):
    """现金单 7 当日部分退 3.5 → 净销售==3.5 == 净实收，diff==0，refunds_total==3.5."""
    from app.models.pos import SaleOrderItem

    await _seed_stock(db_session, quantity=10)
    order_id = await _cash_order(client, "qa-net-partial-001", 2, 3.5)
    async with db_session() as session:
        item = (
            await session.execute(
                select(SaleOrderItem).where(SaleOrderItem.order_id == uuid.UUID(order_id))
            )
        ).scalar_one()
        item_id = str(item.id)
    refund_res = await client.post(
        f"/api/v1/pos/orders/{order_id}/refund",
        json={
            "reason": "部分退",
            "items": [{"item_id": item_id, "quantity": 1, "return_to_stock": True}],
        },
    )
    assert refund_res.status_code == 200

    numbers = (
        await client.get(f"/api/v1/pos/daily-settlement/{local_now().date().isoformat()}")
    ).json()["data"]
    assert numbers["total_sales"] == 3.5  # 净 = 7 - 3.5
    assert numbers["refunds_total"] == 3.5
    assert numbers["total_payments"] == 3.5  # 7 收款 - 3.5 退款
    assert numbers["diff_amount"] == 0.0


# ------------------------------------------------------------------
# 2. 经营台三指标统一口径
# ------------------------------------------------------------------


async def test_twin_dashboard_unified_metric_semantics(client, db_session):
    """销售100(COGS40) 退30 进50 → 收入70、成本40、毛利30、采购支出50 单列。

    QA-01 更新（2026-09-22）：退款回库（quantity>0）开始冲减已售成本（与日结
    _estimate_daily_cogs 口径对齐）。该场景退款行 unit_cost=None，兜底成本按
    当日采购加权均价 5 元/斤冲减 1.5 斤 → 成本 = 40 - 7.5 = 32.5，毛利 = 37.5。
    """
    await _add_ledger_record(db_session, "sale", "-5", "100", unit_cost="8")
    await _add_ledger_record(db_session, "refund", "1.5", "30")
    await _add_ledger_record(db_session, "purchase", "10", "50", unit_cost="5")

    resp = await client.get("/api/v1/twin/dashboard")
    assert resp.status_code == 200
    d = resp.json()["data"]
    assert d["today_revenue"] == 70.0  # 净销售额 = 100 - 30
    assert d["today_refund_total"] == 30.0
    # 已售成本 = 5×8 - 1.5×5（回库退款按兜底均价冲减，QA-01）
    assert d["today_cost"] == 32.5
    assert d["estimated_cogs"] == 32.5
    assert d["today_profit"] == 37.5  # 毛利 = 净收入 - 已售成本
    assert d["estimated_gross_profit"] == 37.5
    assert d["today_purchase_cost"] == 50.0  # 采购支出单独呈现
    assert d["cash_balance"] == 20.0  # 现金结余 = 净收入 - 采购支出


# ------------------------------------------------------------------
# 3. slow_moving 命名与 /inventory/current 同源
# ------------------------------------------------------------------


async def test_slow_moving_name_prefers_sku_same_as_inventory_current(client, db_session):
    """滞销商品挂 sku_id → 日报 slow_moving 显示 SKU 标准名（非「商品{id}」兜底），
    与 /inventory/current 的 sku_name 同字段来源。"""
    from app.models.catalog import ProductSKU

    sku_id = uuid.uuid4()
    async with db_session() as session:
        # 品类表故意无 product_id=999 的行：旧实现会落兜底名「商品999」
        session.add(
            ProductSKU(
                id=sku_id,
                merchant_id=uuid.UUID(TEST_MERCHANT_ID),
                name="本地番茄",
                canonical_unit="斤",
            )
        )
        await session.commit()
    await _add_ledger_record(
        db_session, "purchase", "8", "20", unit_cost="2.5", product_id=999, sku_id=sku_id
    )

    resp = await client.get("/api/v1/reports/daily")
    assert resp.status_code == 200
    slow_moving = resp.json()["data"]["slow_moving"]
    target = next(item for item in slow_moving if item["product_id"] == 999)
    assert target["product_name"] == "本地番茄"
    assert target["sku_id"] == str(sku_id)

    # 与库存接口同源对照：/inventory/current 对同一 sku_id 返回相同 sku_name
    inv = await client.get("/api/v1/inventory/current")
    assert inv.status_code == 200
    inv_item = next(item for item in inv.json()["data"] if item["product_id"] == 999)
    assert inv_item["sku_name"] == "本地番茄"
