"""QA 第二轮修复回归测试（Agent P2 域：离线补账/库存/盘点/费用/员工权限）。

覆盖缺陷：
  - QA2-03：offline-sync 销售不做符号归一化（正数 sale 落 +qty，批次却负向扣减）
  - QA2-04：日结锁覆盖缺口（POST /expenses、/inventory/offline-sync、/ops/waste）
  - QA2-07：盘点接口 avg_cost 与 /inventory/current 不同值（跨商户混算）
  - QA2-08：/inventory/history 返回 voided 行且无标记
  - QA2-09：盘点汇总 loss 与落账 waste 两口径（avg_cost vs FIFO）
  - QA2-10：/staff/permissions/check 硬编码 owner 表
"""

from __future__ import annotations

import sys
import uuid
from datetime import date, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select
from tests.conftest import TEST_MERCHANT_ID

from app.core.timezone import cst_today


SECOND_MERCHANT_ID = "00000000-0000-0000-0000-000000000002"

pytestmark = pytest.mark.asyncio


# ------------------------------------------------------------------
# 公共夹具
# ------------------------------------------------------------------


async def _seed_purchase_record_and_batch(
    session,
    merchant_id,
    product_id,
    qty,
    unit_cost,
    sku_id=None,
    purchased_at=None,
):
    """同时落一笔采购流水与对应批次（模拟真实入库，账实一致）。"""
    from app.models.batch import BatchLifecycle
    from app.models.inventory import InventoryRecord

    mid = uuid.UUID(merchant_id) if isinstance(merchant_id, str) else merchant_id
    now = purchased_at or datetime.utcnow()
    session.add(
        InventoryRecord(
            merchant_id=mid,
            product_id=product_id,
            sku_id=sku_id,
            quantity=Decimal(str(qty)),
            unit="斤",
            unit_cost=Decimal(str(unit_cost)),
            total_amount=Decimal(str(qty)) * Decimal(str(unit_cost)),
            event_type="purchase",
            event_time=now,
        )
    )
    session.add(
        BatchLifecycle(
            merchant_id=mid,
            product_id=product_id,
            sku_id=sku_id,
            batch_label=f"seed-{uuid.uuid4().hex[:8]}",
            purchase_date=now,
            purchase_qty=Decimal(str(qty)),
            remaining_qty=Decimal(str(qty)),
            expiry_date=now + timedelta(hours=72),
            status="sellable",
            unit_cost=Decimal(str(unit_cost)),
        )
    )
    await session.commit()


async def _close_settlement(client, day: date):
    """经正式 close 端点关闭某业务日的日结。"""
    res = await client.post(f"/api/v1/pos/daily-settlement/{day.isoformat()}/close")
    assert res.status_code == 200, res.text


# ------------------------------------------------------------------
# QA2-03：offline-sync 销售符号归一化
# ------------------------------------------------------------------


@pytest.mark.parametrize(
    ("event_type", "raw_qty", "expected"),
    [
        ("sale", 5, Decimal("-5")),
        ("sale", -4, Decimal("-4")),
        ("waste", 3, Decimal("-3")),
        ("purchase", 5, Decimal("5")),
        ("purchase", -5, Decimal("5")),
        ("refund", -2, Decimal("2")),
    ],
)
async def test_qa2_03_build_record_normalizes_sign(event_type, raw_qty, expected):
    """纯函数层：sale/waste 恒负、purchase/refund 恒正（幂等键/其余字段不变）。"""
    from app.schemas.inventory import OfflineSyncItem
    from app.services.offline_sync import _build_inventory_record

    item = OfflineSyncItem(
        idempotency_key="qa2-03-unit",
        event_type=event_type,
        product_name="白菜",
        quantity=raw_qty,
    )
    record = _build_inventory_record(uuid.uuid4(), item, 1)
    assert record.quantity == expected
    assert record.idempotency_key == "qa2-03-unit"
    assert record.event_type == event_type


async def test_qa2_03_offline_sale_positive_input_ledger_matches_batches(client, db_session):
    """端到端：正数 sale 输入 → 流水为负、批次扣减与流水方向一致（台账不再翻倍）。"""
    mid = uuid.UUID(TEST_MERCHANT_ID)
    async with db_session() as session:
        await _seed_purchase_record_and_batch(
            session, mid, 1, qty=16, unit_cost="1.0", sku_id=None
        )

    payload = {
        "items": [
            {
                "idempotency_key": "qa2-03-positive-sale-001",
                "event_type": "sale",
                "product_name": "白菜",
                "quantity": 2,
                "unit": "斤",
                "unit_price": 3.5,
                "total_amount": 7,
                "source": "offline",
            }
        ]
    }
    resp = await client.post("/api/v1/inventory/offline-sync", json=payload)
    assert resp.status_code == 200, resp.text
    assert resp.json()["data"]["created"] == 1
    # 响应结构/幂等键语义不变
    assert resp.json()["data"]["results"][0]["idempotency_key"] == "qa2-03-positive-sale-001"

    from app.models.batch import BatchLifecycle
    from app.models.inventory import InventoryRecord

    async with db_session() as session:
        sale = (
            (
                await session.execute(
                    select(InventoryRecord).where(
                        InventoryRecord.idempotency_key == "qa2-03-positive-sale-001"
                    )
                )
            )
            .scalars()
            .one()
        )
        # 核心断言：sale 流水恒为负（此前 +2 与批次 −2 双向分歧）
        assert float(sale.quantity) == -2.0

        batch = (
            (
                await session.execute(
                    select(BatchLifecycle).where(BatchLifecycle.merchant_id == mid)
                )
            )
            .scalars()
            .one()
        )
        # 流水净和 = 批次余量 = 14（方向一致，账实相符）
        ledger_sum = (
            await session.execute(
                select(InventoryRecord.quantity).where(
                    InventoryRecord.merchant_id == mid,
                    InventoryRecord.product_id == 1,
                    InventoryRecord.is_voided.is_(False),
                )
            )
        ).scalars().all()
        assert float(sum(ledger_sum)) == float(batch.remaining_qty) == 14.0

    # 幂等重试仍为 duplicate（归一化不影响幂等键）
    retry = await client.post("/api/v1/inventory/offline-sync", json=payload)
    assert retry.status_code == 200
    assert retry.json()["data"]["duplicate"] == 1


# ------------------------------------------------------------------
# QA2-04：日结锁覆盖（/expenses、/inventory/offline-sync、/ops/waste）
# ------------------------------------------------------------------


async def test_qa2_04_expense_blocked_after_close_and_released_on_reopen(client, db_session):
    """close 后 POST /expenses 按 expense_date 判定 → 409；reopen 后放行。"""
    today = date.today()
    await _close_settlement(client, today)

    body = {
        "amount": 12.5,
        "category": "rent",
        "expense_date": today.isoformat(),
        "description": "摊位费",
    }
    resp = await client.post("/api/v1/expenses", json=body)
    assert resp.status_code == 409, resp.text
    assert "日结已关闭" in resp.json()["detail"]
    assert today.isoformat() in resp.json()["detail"]

    from app.models.expense import Expense

    async with db_session() as session:
        count = len((await session.execute(select(Expense))).scalars().all())
    assert count == 0, "日结关闭后费用不得落库"

    # reopen → 解锁，同一笔费用可正常入账
    reopen = await client.post(f"/api/v1/pos/daily-settlement/{today.isoformat()}/reopen")
    assert reopen.status_code == 200, reopen.text
    resp2 = await client.post("/api/v1/expenses", json=body)
    assert resp2.status_code == 200, resp2.text


async def test_qa2_04_offline_sync_blocked_after_close_whole_batch(client, db_session):
    """close 后 offline-sync 整单 409（含日期），任何记录都不落库。"""
    mid = uuid.UUID(TEST_MERCHANT_ID)
    async with db_session() as session:
        await _seed_purchase_record_and_batch(
            session, mid, 1, qty=20, unit_cost="1.0", sku_id=None
        )

    today = cst_today()
    await _close_settlement(client, today)

    today_item = {
        "idempotency_key": "qa2-04-offline-today-001",
        "event_type": "sale",
        "product_name": "白菜",
        "quantity": 1,
        "unit": "斤",
        "total_amount": 3.5,
        # 今天 12:00 UTC = CST 20:00，业务日即今天
        "event_time": f"{today.isoformat()}T12:00:00",
        "source": "offline",
    }
    payload = {"items": [today_item]}
    resp = await client.post("/api/v1/inventory/offline-sync", json=payload)
    assert resp.status_code == 409, resp.text
    assert "日结已关闭" in resp.json()["detail"]
    assert today.isoformat() in resp.json()["detail"]

    from app.models.inventory import InventoryRecord

    async with db_session() as session:
        rows = (
            (
                await session.execute(
                    select(InventoryRecord).where(
                        InventoryRecord.merchant_id == mid,
                        InventoryRecord.idempotency_key == "qa2-04-offline-today-001",
                    )
                )
            )
            .scalars()
            .all()
        )
    assert rows == [], "日结关闭后离线补账不得落库（整单拒绝）"

    # 整单语义：批内一条落已关业务日（今天）、一条落未关业务日（去年）→ 整单 409
    batch_payload = {
        "items": [
            dict(today_item, idempotency_key="qa2-04-offline-mixed-001"),
            {
                "idempotency_key": "qa2-04-offline-mixed-002",
                "event_type": "sale",
                "product_name": "白菜",
                "quantity": 1,
                "unit": "斤",
                "total_amount": 3.5,
                "event_time": f"{today.year - 1}-06-01T04:00:00",
                "source": "offline",
            },
        ]
    }
    mixed = await client.post("/api/v1/inventory/offline-sync", json=batch_payload)
    assert mixed.status_code == 409
    async with db_session() as session:
        rows = (
            (
                await session.execute(
                    select(InventoryRecord).where(
                        InventoryRecord.idempotency_key.like("qa2-04-offline-mixed-%")
                    )
                )
            )
            .scalars()
            .all()
        )
    assert rows == [], "整单拒绝：批内不得有任何记录被部分落账"


async def test_qa2_04_ops_waste_blocked_after_close(client, db_session):
    """close 后 POST /ops/waste → 409，批次余量不被扣减。"""
    from app.models.batch import BatchLifecycle

    mid = uuid.UUID(TEST_MERCHANT_ID)
    async with db_session() as session:
        await _seed_purchase_record_and_batch(session, mid, 1, qty=10, unit_cost="1.0")

    await _close_settlement(client, cst_today())

    resp = await client.post(
        "/api/v1/ops/waste",
        json={"product_id": 1, "quantity": 2, "unit": "斤", "reason": "腐烂"},
    )
    assert resp.status_code == 409, resp.text
    assert "日结已关闭" in resp.json()["detail"]

    async with db_session() as session:
        batch = (
            (
                await session.execute(
                    select(BatchLifecycle).where(BatchLifecycle.merchant_id == mid)
                )
            )
            .scalars()
            .one()
        )
        assert float(batch.remaining_qty) == 10.0, "日结关闭后报损不得扣批次"


# ------------------------------------------------------------------
# QA2-07：盘点接口 avg_cost 与 /inventory/current 同源同值
# ------------------------------------------------------------------


async def test_qa2_07_stocktake_avg_cost_matches_current_inventory(client, db_session):
    """跨商户污染数据下，盘点页 avg_cost 必须与 /inventory/current 相等。"""
    mid = uuid.UUID(TEST_MERCHANT_ID)
    # 本商户：白菜 10 斤 @2.10；别家商户同商品 10 斤 @0.50
    # （修复前混算 avg_cost=(2.1*10+0.5*10)/20=1.30，正是 QA 报告的 1.30 vs 2.10）
    async with db_session() as session:
        await _seed_purchase_record_and_batch(session, mid, 1, qty=10, unit_cost="2.10")
        await _seed_purchase_record_and_batch(
            session, SECOND_MERCHANT_ID, 1, qty=10, unit_cost="0.50"
        )

    current = await client.get("/api/v1/inventory/current")
    assert current.status_code == 200
    row = next(r for r in current.json()["data"] if r["product_id"] == 1)
    assert row["avg_cost"] == 2.1

    start = await client.post("/api/v1/inventory/stocktake/start", json={})
    assert start.status_code == 200, start.text

    stocktake = await client.get("/api/v1/inventory/stocktake/current")
    assert stocktake.status_code == 200
    items = stocktake.json()["data"]["items"]
    st_item = next(i for i in items if i["product_id"] == 1)
    # 核心断言：两接口同源同值（均为本商户加权均价）
    assert st_item["avg_cost"] == row["avg_cost"] == 2.1


# ------------------------------------------------------------------
# QA2-08：/inventory/history 补 is_voided 标记与可选过滤
# ------------------------------------------------------------------


async def test_qa2_08_history_marks_voided_rows_and_can_filter(client, db_session):
    """voided 行带 is_voided/void_reason 标记；include_voided=false 可过滤。"""
    mid = uuid.UUID(TEST_MERCHANT_ID)
    async with db_session() as session:
        await _seed_purchase_record_and_batch(session, mid, 1, qty=5, unit_cost="1.0")
        await _seed_purchase_record_and_batch(session, mid, 2, qty=5, unit_cost="1.0")

    from app.models.inventory import InventoryRecord

    async with db_session() as session:
        rows = (
            (
                await session.execute(
                    select(InventoryRecord).where(InventoryRecord.merchant_id == mid)
                )
            )
            .scalars()
            .all()
        )
        target = next(r for r in rows if r.product_id == 1)
        keep = next(r for r in rows if r.product_id == 2)
        target_id = str(target.id)

    void_resp = await client.post(
        f"/api/v1/inventory/{target_id}/void",
        json={"reason": "记错数量"},
    )
    assert void_resp.status_code == 200, void_resp.text

    history = await client.get("/api/v1/inventory/history", params={"limit": 50})
    assert history.status_code == 200
    data = {row["id"]: row for row in history.json()["data"]}
    # 核心断言：默认全量返回且撤销行有标记
    assert data[target_id]["is_voided"] is True
    assert data[target_id]["void_reason"] == "记错数量"
    assert data[target_id]["voided_by"] == "manual"
    assert data[target_id]["voided_at"] is not None
    assert data[str(keep.id)]["is_voided"] is False

    filtered = await client.get(
        "/api/v1/inventory/history", params={"limit": 50, "include_voided": "false"}
    )
    assert filtered.status_code == 200
    filtered_ids = {row["id"] for row in filtered.json()["data"]}
    assert target_id not in filtered_ids
    assert str(keep.id) in filtered_ids


# ------------------------------------------------------------------
# QA2-09：盘点汇总 total_loss_amount 与落账 waste 同源（FIFO）
# ------------------------------------------------------------------


async def test_qa2_09_stocktake_loss_summary_uses_fifo_waste_amount(client, db_session):
    """两批次不同进价：汇总值必须等于落账 waste 行 total_amount 合计（FIFO），
    而非 avg_cost 估算（此前 1.5 vs 1.0 两口径）。"""
    mid = uuid.UUID(TEST_MERCHANT_ID)
    now = datetime.utcnow()
    async with db_session() as session:
        # 先进 5 斤 @1.0，后进 5 斤 @2.0 → avg_cost=1.5，盘亏 1 斤 FIFO 成本=1.0
        await _seed_purchase_record_and_batch(
            session, mid, 1, qty=5, unit_cost="1.0", purchased_at=now - timedelta(hours=2)
        )
        await _seed_purchase_record_and_batch(
            session, mid, 1, qty=5, unit_cost="2.0", purchased_at=now - timedelta(hours=1)
        )

    start = await client.post("/api/v1/inventory/stocktake/start", json={})
    assert start.status_code == 200, start.text
    start_data = start.json()["data"]
    session_id = start_data["session_id"]
    for item in start_data["items"]:
        actual = 9 if item["product_id"] == 1 else item["book_qty"]
        resp = await client.post(
            f"/api/v1/inventory/stocktake/{session_id}/submit",
            json={"product_id": item["product_id"], "actual_qty": actual},
        )
        assert resp.status_code == 200

    complete = await client.post(f"/api/v1/inventory/stocktake/{session_id}/complete", json={})
    assert complete.status_code == 200, complete.text
    data = complete.json()["data"]
    assert data["total_variance"] == -1.0

    from app.models.inventory import InventoryRecord

    async with db_session() as session:
        waste_rows = (
            (
                await session.execute(
                    select(InventoryRecord).where(
                        InventoryRecord.merchant_id == mid,
                        InventoryRecord.source == "stocktake",
                        InventoryRecord.event_type == "waste",
                    )
                )
            )
            .scalars()
            .all()
        )
        assert len(waste_rows) == 1
        booked_loss = sum(float(r.total_amount or 0) for r in waste_rows)
        # 核心断言：汇总值 == 落账 waste 的 total_amount 合计（FIFO 1.0，而非 avg_cost 1.5）
        assert booked_loss == 1.0
        assert data["total_loss_amount"] == booked_loss
        # FIFO 实扣：先进批次（@1.0）被扣 1 斤
        assert float(waste_rows[0].unit_cost) == 1.0


# ------------------------------------------------------------------
# QA2-10：/staff/permissions/check 按实际角色判定
# ------------------------------------------------------------------


async def test_qa2_10_permissions_check_owner_allowed(client):
    """owner（默认商户身份）查 change_price/view_profit → allowed=true。"""
    for action in ("change_price", "view_profit"):
        resp = await client.get(f"/api/v1/staff/permissions/check?action={action}")
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["allowed"] is True
        assert data["role"] == "owner"


async def test_qa2_10_permissions_check_cashier_denied_via_token_role(client):
    """cashier（token role claim）查 change_price/view_profit → allowed=false。"""
    for action in ("change_price", "view_profit"):
        resp = await client.get(
            f"/api/v1/staff/permissions/check?action={action}",
            headers={"X-Test-Token-Role": "cashier"},
        )
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["allowed"] is False
        assert data["role"] == "cashier"
    # cashier 自身持有权限仍回 true
    ok = await client.get(
        "/api/v1/staff/permissions/check?action=credit_sale",
        headers={"X-Test-Token-Role": "cashier"},
    )
    assert ok.json()["data"]["allowed"] is True


async def test_qa2_10_permissions_check_cashier_denied_via_staff_header(client, db_session):
    """cashier（X-Staff-Id 头兼容路径）同样按员工角色判定。"""
    from app.models.staff import StaffMember

    async with db_session() as session:
        staff = StaffMember(
            merchant_id=uuid.UUID(TEST_MERCHANT_ID),
            name="收银员",
            role="cashier",
            is_active=True,
        )
        session.add(staff)
        await session.commit()
        await session.refresh(staff)
        sid = str(staff.id)

    resp = await client.get(
        "/api/v1/staff/permissions/check?action=change_price",
        headers={"X-Staff-Id": sid},
    )
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["allowed"] is False
    assert data["role"] == "cashier"
