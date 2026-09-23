"""QA 修复回归（Agent R：报表/日结/看板族，2026-09-22）。

覆盖测试报告 §② 以下缺陷：
- QA-01（P0）：退款回库口径 —— reports/twin 的已售成本与日结同步冲减退款回库，
  同日三处恒等（回归：进10@2 → 卖2 → 退1回库，reports 与日结 cogs 相等）。
- QA-03（P1）：员工越权 —— /reports/daily|weekly|monthly|trends、/twin/dashboard
  挂 view_profit；owner/manager 200，cashier 403。
- QA-13（P2）：POS unit_price 无上限 —— 对齐目录价 1e6 上限，超限 422。
- QA-14（P2）：/reports/daily top_products 按退款净额统计，退款订单不再污染排名。
- QA-15（P2）：成本兜底口径改采购量加权平均 sum(qty×uc)/sum(qty)，
  reports / twin / 日结三处一致。
- QA-18（P2）：日结 reopen 后 GET 回实时数（与行不存在路径同源），
  refunds_total 字段两条路径都返回。
- QA-32（P3）：日报 voice_count 只计 confirmed（对齐 /voice/today-count）。
- QA-44（P3）：POS 单号前缀时间戳改 CST 业务日。
"""

import sys
import uuid
from decimal import Decimal
from pathlib import Path


sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest
from tests.conftest import TEST_MERCHANT_ID

from app.core.timezone import cst_now, cst_today, utc_now


pytestmark = pytest.mark.asyncio


# ------------------------------------------------------------------
# 造数工具
# ------------------------------------------------------------------


async def _add_ledger_record(
    db_session,
    event_type: str,
    quantity: str,
    total_amount: str,
    unit_cost: str | None = None,
    product_id: int = 1,
):
    """直接插一条当日库存台账流水（reports/twin/日结的统计来源）。"""
    from app.models.inventory import InventoryRecord

    async with db_session() as session:
        session.add(
            InventoryRecord(
                merchant_id=uuid.UUID(TEST_MERCHANT_ID),
                product_id=product_id,
                quantity=Decimal(quantity),
                unit="斤",
                unit_cost=Decimal(unit_cost) if unit_cost is not None else None,
                unit_price=Decimal(total_amount) if event_type == "sale" else None,
                total_amount=Decimal(total_amount),
                event_type=event_type,
                # DB 约定：event_time 存 naive UTC（写入端走 utc_now()）
                event_time=utc_now(),
                source="test",
            )
        )
        await session.commit()


async def _seed_stock(db_session, quantity: int = 10, unit_cost: Decimal | None = Decimal("2")):
    from app.services.batch import create_batch

    async with db_session() as session:
        await create_batch(
            session,
            uuid.UUID(TEST_MERCHANT_ID),
            1,
            "白菜",
            f"白菜-qaR-{uuid.uuid4().hex[:6]}",
            Decimal(str(quantity)),
            unit_cost=unit_cost,
        )
        await session.commit()


def _order_payload(client_id: str, **overrides):
    payload = {
        "client_id": client_id,
        "payment_method": "cash",
        "items": [{"product_id": 1, "quantity": 2, "unit": "斤", "unit_price": 3.5}],
    }
    payload.update(overrides)
    return payload


async def _live_settlement_cogs(client) -> float:
    """GET 日结（无行 → live 路径）返回当日实时已售成本。"""
    resp = await client.get(f"/api/v1/pos/daily-settlement/{cst_today().isoformat()}")
    assert resp.status_code == 200
    return resp.json()["data"]["estimated_cogs"]


# ------------------------------------------------------------------
# QA-01：退款回库口径统一（reports / twin / 日结三处恒等）
# ------------------------------------------------------------------


async def test_qa01_restock_refund_cogs_consistent_between_reports_and_settlement(
    client, db_session
):
    """进10@2 → 卖2(成本2) → 退1回库：reports 日报 cogs == 日结实时 cogs == 2.0.

    修复前：reports/_estimate_cogs 只算 sale（=4），日结冲减退款回库（=2），
    同日两处打架（QA-01 P0）。
    """
    await _add_ledger_record(db_session, "purchase", "10", "20", unit_cost="2")
    await _add_ledger_record(db_session, "sale", "-2", "7", unit_cost="2")
    # 回库退款行：quantity=+1、unit_cost 沿用售出成本（与 POS _refund_single_item 一致）
    await _add_ledger_record(db_session, "refund", "1", "3.5", unit_cost="2")

    daily = await client.get("/api/v1/reports/daily")
    assert daily.status_code == 200
    report_cogs = daily.json()["data"]["estimated_cogs"]

    settlement_cogs = await _live_settlement_cogs(client)

    assert report_cogs == 2.0, "reports 日报应冲减回库退款成本 1×2"
    assert settlement_cogs == 2.0
    assert report_cogs == settlement_cogs, "同日 reports 与日结的已售成本必须相等（QA-01）"

    # 经营台看板同口径
    twin = await client.get("/api/v1/twin/dashboard")
    assert twin.status_code == 200
    assert twin.json()["data"]["estimated_cogs"] == 2.0


async def test_qa01_no_restock_refund_keeps_cogs_unchanged(client, db_session):
    """不回库退款（quantity=0）无成本影响：reports == 日结 == 4.0（仅收入冲减）。"""
    await _add_ledger_record(db_session, "purchase", "10", "20", unit_cost="2")
    await _add_ledger_record(db_session, "sale", "-2", "7", unit_cost="2")
    await _add_ledger_record(db_session, "refund", "0", "3.5", unit_cost="2")

    daily = await client.get("/api/v1/reports/daily")
    assert daily.json()["data"]["estimated_cogs"] == 4.0
    assert await _live_settlement_cogs(client) == 4.0


# ------------------------------------------------------------------
# QA-03：员工越权看老板利润报表
# ------------------------------------------------------------------


@pytest.mark.parametrize(
    "path",
    [
        "/api/v1/reports/daily",
        "/api/v1/reports/weekly",
        "/api/v1/reports/monthly",
        "/api/v1/reports/trends",
        "/api/v1/twin/dashboard",
    ],
)
async def test_qa03_cashier_forbidden_on_profit_reports(client, path):
    """cashier 无 view_profit → 403（修复前全 200）。"""
    resp = await client.get(path, headers={"X-Test-Token-Role": "cashier"})
    assert resp.status_code == 403, f"{path} 应对 cashier 拒绝"


@pytest.mark.parametrize(
    "path",
    [
        "/api/v1/reports/daily",
        "/api/v1/reports/weekly",
        "/api/v1/reports/monthly",
        "/api/v1/reports/trends",
        "/api/v1/twin/dashboard",
    ],
)
async def test_qa03_owner_and_manager_allowed_on_profit_reports(client, path):
    """owner（默认 token 角色）与 manager（含 view_profit）不受影响 → 200。"""
    resp_owner = await client.get(path)
    assert resp_owner.status_code == 200, f"{path} owner 应放行"
    resp_manager = await client.get(path, headers={"X-Test-Token-Role": "manager"})
    assert resp_manager.status_code == 200, f"{path} manager 应放行"


# ------------------------------------------------------------------
# QA-13：POS unit_price 上限对齐目录价 1e6
# ------------------------------------------------------------------


async def test_qa13_pos_unit_price_over_1e6_rejected(client, db_session):
    """unit_price=1e10 落单 → 422（此前 1e13 可创建并退款成功）。"""
    await _seed_stock(db_session)
    resp = await client.post(
        "/api/v1/pos/orders",
        json=_order_payload(
            "qaR-price-cap-001",
            items=[{"product_id": 1, "quantity": 1, "unit": "斤", "unit_price": 1e10}],
        ),
    )
    assert resp.status_code == 422


async def test_qa13_pos_unit_price_at_1e6_boundary_allowed(client, db_session):
    """边界 1e6 与目录价上限一致，仍可正常落单。"""
    await _seed_stock(db_session)
    resp = await client.post(
        "/api/v1/pos/orders",
        json=_order_payload(
            "qaR-price-cap-002",
            items=[{"product_id": 1, "quantity": 1, "unit": "斤", "unit_price": 1000000}],
        ),
    )
    assert resp.status_code == 200
    assert resp.json()["data"]["total_amount"] == 1000000.0


# ------------------------------------------------------------------
# QA-14：top_products 按退款净额统计
# ------------------------------------------------------------------


async def test_qa14_top_products_net_out_refunds(client, db_session):
    """商品1 卖100 整单退100、商品2 卖50 → 榜首应为商品2（此前商品1 以毛额霸榜）。"""
    await _add_ledger_record(db_session, "purchase", "20", "40", unit_cost="2")
    await _add_ledger_record(db_session, "sale", "-10", "100", unit_cost="2")
    # 整单退款回库：数量/金额全额冲减
    await _add_ledger_record(db_session, "refund", "10", "100", unit_cost="2")
    await _add_ledger_record(db_session, "sale", "-5", "50", unit_cost="2", product_id=2)

    daily = await client.get("/api/v1/reports/daily")
    assert daily.status_code == 200
    top = daily.json()["data"]["top_products"]
    assert top, "净额后仍有成交商品"
    assert top[0]["product_id"] == 2
    assert top[0]["revenue"] == 50.0
    # 商品1 全额退：净额归零，不再污染排名
    p1 = next(row for row in top if row["product_id"] == 1)
    assert p1["revenue"] == 0.0
    assert p1["qty"] == 0.0


async def test_qa14_no_restock_refund_nets_revenue_only(client, db_session):
    """不回库退款（quantity=0）：营收冲减、销量保留（货已出手，仅退钱）。"""
    await _add_ledger_record(db_session, "sale", "-10", "100", unit_cost="2")
    await _add_ledger_record(db_session, "refund", "0", "30", unit_cost="2")

    daily = await client.get("/api/v1/reports/daily")
    top = daily.json()["data"]["top_products"]
    assert top[0]["product_id"] == 1
    assert top[0]["revenue"] == 70.0  # 与响应 revenue 净额口径一致
    assert top[0]["qty"] == 10.0


# ------------------------------------------------------------------
# QA-15：成本兜底改采购量加权平均
# ------------------------------------------------------------------


async def test_qa15_weighted_average_cost_fallback(client, db_session):
    """进1@2 + 进10@5，售2（无实际成本）→ 兜底 = 2×(52/11) ≈ 9.45.

    修复前简单平均 (2+5)/2=3.5 → 7.0，多批次不同进价时毛利系统性失真。
    断言 reports / twin / 日结三处一致。
    """
    await _add_ledger_record(db_session, "purchase", "1", "2", unit_cost="2")
    await _add_ledger_record(db_session, "purchase", "10", "50", unit_cost="5")
    await _add_ledger_record(db_session, "sale", "-2", "20")  # unit_cost=None → 走兜底

    expected = round(2 * (1 * 2 + 10 * 5) / 11, 2)  # 9.45

    daily = await client.get("/api/v1/reports/daily")
    assert daily.status_code == 200
    assert daily.json()["data"]["estimated_cogs"] == expected

    twin = await client.get("/api/v1/twin/dashboard")
    assert twin.json()["data"]["estimated_cogs"] == expected

    assert await _live_settlement_cogs(client) == expected


# ------------------------------------------------------------------
# QA-18：日结 reopen 后 GET 回实时数
# ------------------------------------------------------------------


async def test_qa18_get_settlement_after_reopen_reflects_new_sales(client, db_session):
    """close → reopen → 再记一笔 → GET 反映新流水且 status=open、refunds_total 在场。"""
    await _seed_stock(db_session, quantity=30)
    first = await client.post("/api/v1/pos/orders", json=_order_payload("qaR-reopen-001"))
    assert first.status_code == 200
    settle_date = cst_today().isoformat()

    closed = await client.post(f"/api/v1/pos/daily-settlement/{settle_date}/close")
    assert closed.status_code == 200
    assert closed.json()["data"]["total_sales"] == 7.0

    # 关闭回显：快照含 refunds_total 字段
    echo = await client.get(f"/api/v1/pos/daily-settlement/{settle_date}")
    assert echo.status_code == 200
    echo_data = echo.json()["data"]
    assert echo_data["status"] == "closed"
    assert echo_data["refunds_total"] == 0.0

    reopen = await client.post(f"/api/v1/pos/daily-settlement/{settle_date}/reopen")
    assert reopen.status_code == 200

    # reopen 后补录一笔 1×5=5
    extra = await client.post(
        "/api/v1/pos/orders",
        json=_order_payload(
            "qaR-reopen-002",
            items=[{"product_id": 1, "quantity": 1, "unit": "斤", "unit_price": 5}],
        ),
    )
    assert extra.status_code == 200

    # 修复前：GET 回读关闭时快照 total_sales=7，新流水不体现
    after = await client.get(f"/api/v1/pos/daily-settlement/{settle_date}")
    assert after.status_code == 200
    data = after.json()["data"]
    assert data["status"] == "open"
    assert data["total_sales"] == 12.0, "reopen 后 GET 应为实时净销售额（7+5）"
    assert data["order_count"] == 2
    assert "refunds_total" in data, "open 路径也必须带 refunds_total 字段"


async def test_qa18_get_settlement_open_row_without_close_returns_live(client, db_session):
    """未日结（无行）路径回归：live 计算带 refunds_total（既有口径不受影响）。"""
    resp = await client.get(f"/api/v1/pos/daily-settlement/{cst_today().isoformat()}")
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["status"] == "open"
    assert data["refunds_total"] == 0.0


# ------------------------------------------------------------------
# QA-32：日报 voice_count 只计 confirmed
# ------------------------------------------------------------------


async def test_qa32_daily_voice_count_counts_confirmed_only(client, db_session):
    """2 confirmed + 1 parsed + 1 voided → voice_count == 2（对齐 /voice/today-count）。"""
    from app.models.voice import VoiceLog

    async with db_session() as session:
        for status in ("confirmed", "confirmed", "parsed", "voided"):
            session.add(
                VoiceLog(
                    merchant_id=uuid.UUID(TEST_MERCHANT_ID),
                    asr_text=f"QA-32 {status}",
                    status=status,
                    created_at=utc_now(),
                )
            )
        await session.commit()

    daily = await client.get("/api/v1/reports/daily")
    assert daily.status_code == 200
    assert daily.json()["data"]["voice_count"] == 2

    # 与 /voice/today-count 口径一致
    today_count = await client.get("/api/v1/voice/today-count")
    assert today_count.status_code == 200
    assert today_count.json()["data"]["today_count"] == 2


# ------------------------------------------------------------------
# QA-44：POS 单号前缀 CST 业务日
# ------------------------------------------------------------------


async def test_qa44_order_no_prefix_uses_cst_business_day(client, db_session):
    """单号前缀 = CST 当日（修复前 UTC 时间戳在 CST 0-8 点落前一 UTC 日）。"""
    await _seed_stock(db_session)
    resp = await client.post("/api/v1/pos/orders", json=_order_payload("qaR-cst-order-001"))
    assert resp.status_code == 200
    order_no = resp.json()["data"]["order_no"]
    assert order_no.startswith("POS"), order_no
    assert order_no.startswith(f"POS{cst_now().strftime('%Y%m%d')}"), (
        f"单号 {order_no} 前缀应为 CST 业务日 {cst_now().strftime('%Y%m%d')}"
    )
