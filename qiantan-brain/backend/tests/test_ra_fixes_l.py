"""RA 复查修复回归（L 批）— recheck-A-findings RA-03/06/07/08/10/11/12。

覆盖：
- RA-06：/inventory/{id}/void 补日结锁（按被撤流水 event_time 的 CST 业务日）
- RA-07：DELETE /expenses/{id} 补日结锁（按被删费用 expense_date）
- RA-08：/inventory/stocktake/{sid}/complete 补今日日结锁
- RA-03：/ops/waste 带 sku_id 开无主批次回退（与 QA2-02 POS/语音同口径）
- RA-10：offline-sync 金额符号归一（total_amount/unit_cost/unit_price）
- RA-11：offline-sync event_type 白名单（大小写归一，非法 422 列合法值）
- RA-12：/inventory/history 与 /stocktake/history limit≤0 → 空列表
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select
from tests.conftest import TEST_MERCHANT_ID

from app.core.timezone import cst_today, utc_now



# ------------------------------------------------------------------
# 公共夹具
# ------------------------------------------------------------------


async def _close(client, day: date):
    res = await client.post(f"/api/v1/pos/daily-settlement/{day.isoformat()}/close")
    assert res.status_code == 200, res.text


async def _reopen(client, day: date):
    res = await client.post(f"/api/v1/pos/daily-settlement/{day.isoformat()}/reopen")
    assert res.status_code == 200, res.text


async def _seed_purchase(session_factory, product_id=1, qty=10, unit_cost="1.0"):
    """落一笔采购流水（让盘点 start 有账面商品；也可当普通库存来源）。"""
    from app.models.inventory import InventoryRecord

    mid = uuid.UUID(TEST_MERCHANT_ID)
    now = utc_now()
    async with session_factory() as session:
        session.add(
            InventoryRecord(
                merchant_id=mid,
                product_id=product_id,
                quantity=Decimal(str(qty)),
                unit="斤",
                unit_cost=Decimal(unit_cost),
                total_amount=Decimal(str(qty)) * Decimal(unit_cost),
                event_type="purchase",
                event_time=now,
            )
        )
        await session.commit()


# ------------------------------------------------------------------
# RA-06：/inventory/{id}/void 日结锁
# ------------------------------------------------------------------


class TestRA06VoidSettlementLock:
    async def _seed_voidable_record(self, db_session):
        from app.models.inventory import InventoryRecord

        mid = uuid.UUID(TEST_MERCHANT_ID)
        async with db_session() as session:
            record = InventoryRecord(
                merchant_id=mid,
                product_id=1,
                quantity=Decimal("-2"),
                unit="斤",
                unit_price=Decimal("3.0"),
                total_amount=Decimal("6"),
                event_type="sale",
                event_time=utc_now(),
                source="offline",
                notes="RA-06 探针流水",
            )
            session.add(record)
            await session.commit()
            return record.id

    async def test_void_blocked_after_close(self, client, db_session):
        """close 当日日结后，撤销当日流水必须 409（此前 200 改写 closed 台账）。"""
        record_id = await self._seed_voidable_record(db_session)
        await _close(client, cst_today())

        res = await client.post(
            f"/api/v1/inventory/{record_id}/void", json={"reason": "RA-06 探针"}
        )
        assert res.status_code == 409, res.text
        assert "日结已关闭" in res.json()["detail"]
        assert cst_today().isoformat() in res.json()["detail"]

        from app.models.inventory import InventoryRecord

        async with db_session() as session:
            row = await session.get(InventoryRecord, record_id)
        assert row.is_voided is False, "日结关闭后流水不得被撤销"

    async def test_void_released_after_reopen(self, client, db_session):
        """reopen 后撤销放行（与 QA2-04 锁语义一致）。"""
        record_id = await self._seed_voidable_record(db_session)
        await _close(client, cst_today())
        await _reopen(client, cst_today())

        res = await client.post(
            f"/api/v1/inventory/{record_id}/void", json={"reason": "reopen 后撤销"}
        )
        assert res.status_code == 200, res.text
        assert res.json()["message"] == "记录已撤销，库存和批次已回滚"

    async def test_void_past_day_lock_uses_event_time_business_day(self, client, db_session):
        """锁按被撤流水 event_time 所属业务日判定：close 昨天 → 撤昨天流水 409，
        撤今天流水不受影响（离线补账跨日场景）。"""
        from app.core.timezone import cst_date_of_utc_naive
        from app.models.inventory import InventoryRecord

        mid = uuid.UUID(TEST_MERCHANT_ID)
        yesterday_utc = utc_now() - timedelta(days=1)
        async with db_session() as session:
            old_record = InventoryRecord(
                merchant_id=mid,
                product_id=1,
                quantity=Decimal("-1"),
                unit="斤",
                total_amount=Decimal("3"),
                event_type="sale",
                event_time=yesterday_utc,
                source="offline",
            )
            today_record = InventoryRecord(
                merchant_id=mid,
                product_id=1,
                quantity=Decimal("-1"),
                unit="斤",
                total_amount=Decimal("3"),
                event_type="sale",
                event_time=utc_now(),
                source="offline",
            )
            session.add_all([old_record, today_record])
            await session.commit()
            old_id, today_id = old_record.id, today_record.id

        await _close(client, cst_date_of_utc_naive(yesterday_utc))

        old_res = await client.post(
            f"/api/v1/inventory/{old_id}/void", json={"reason": "昨日流水"}
        )
        assert old_res.status_code == 409, old_res.text

        today_res = await client.post(
            f"/api/v1/inventory/{today_id}/void", json={"reason": "今日流水"}
        )
        assert today_res.status_code == 200, today_res.text


# ------------------------------------------------------------------
# RA-08：盘点 complete 日结锁
# ------------------------------------------------------------------


class TestRA08StocktakeCompleteLock:
    async def _start_and_submit(self, client, db_session, actual_qty=5.0):
        await _seed_purchase(db_session, product_id=1, qty=10)
        start = await client.post("/api/v1/inventory/stocktake/start", json={})
        assert start.status_code == 200, start.text
        session_id = start.json()["data"]["session_id"]
        submit = await client.post(
            f"/api/v1/inventory/stocktake/{session_id}/submit",
            json={"product_id": 1, "actual_qty": actual_qty, "variance_reason": "RA-08"},
        )
        assert submit.status_code == 200, submit.text
        return session_id

    async def test_complete_blocked_after_close(self, client, db_session):
        """close 后 complete 必须 409（此前落 waste/盘盈校准 closed 日台账）。"""
        session_id = await self._start_and_submit(client, db_session)
        await _close(client, cst_today())

        res = await client.post(
            f"/api/v1/inventory/stocktake/{session_id}/complete", json={}
        )
        assert res.status_code == 409, res.text
        assert "日结已关闭" in res.json()["detail"]

        from app.models.inventory import InventoryRecord

        async with db_session() as session:
            waste_rows = (
                (
                    await session.execute(
                        select(InventoryRecord).where(
                            InventoryRecord.merchant_id == uuid.UUID(TEST_MERCHANT_ID),
                            InventoryRecord.event_type == "waste",
                            InventoryRecord.source == "stocktake",
                        )
                    )
                )
                .scalars()
                .all()
            )
        assert waste_rows == [], "日结关闭后盘点不得落 waste 流水"

    async def test_complete_released_after_reopen(self, client, db_session):
        """reopen 后 complete 放行且正常落盘亏。"""
        session_id = await self._start_and_submit(client, db_session)
        await _close(client, cst_today())
        await _reopen(client, cst_today())

        res = await client.post(
            f"/api/v1/inventory/stocktake/{session_id}/complete", json={}
        )
        assert res.status_code == 200, res.text
        assert res.json()["data"]["total_variance"] == -5.0


# ------------------------------------------------------------------
# RA-07：DELETE /expenses 日结锁
# ------------------------------------------------------------------


class TestRA07ExpenseDeleteLock:
    async def _seed_expense(self, db_session, expense_date):
        from app.models.expense import Expense

        async with db_session() as session:
            e = Expense(
                merchant_id=uuid.UUID(TEST_MERCHANT_ID),
                category="rent",
                amount=Decimal("12.5"),
                description="RA-07 探针",
                expense_date=expense_date,
            )
            session.add(e)
            await session.commit()
            return e.id

    async def test_delete_blocked_after_close(self, client, db_session):
        today = cst_today()
        expense_id = await self._seed_expense(db_session, today)
        await _close(client, today)

        res = await client.delete(f"/api/v1/expenses/{expense_id}")
        assert res.status_code == 409, res.text
        assert "日结已关闭" in res.json()["detail"]

        from app.models.expense import Expense

        async with db_session() as session:
            row = await session.get(Expense, expense_id)
        assert row is not None, "日结关闭后费用不得被删除"

    async def test_delete_released_after_reopen(self, client, db_session):
        today = cst_today()
        expense_id = await self._seed_expense(db_session, today)
        await _close(client, today)
        await _reopen(client, today)

        res = await client.delete(f"/api/v1/expenses/{expense_id}")
        assert res.status_code == 200, res.text


# ------------------------------------------------------------------
# RA-03：/ops/waste 带 sku_id 的无主批次回退
# ------------------------------------------------------------------


class TestRA03WasteFallbackToUnowned:
    async def test_waste_with_sku_consumes_unowned_batch(self, client, db_session):
        """语音/POS 兜底产生的 sku_id=NULL 批次：带 sku_id 报损必须能回退消耗
        （此前按 sku 过滤「可用 0」误 409）。"""
        from app.models.batch import BatchLifecycle
        from app.models.catalog import ProductSKU

        mid = uuid.UUID(TEST_MERCHANT_ID)
        async with db_session() as session:
            now = utc_now()
            session.add(
                BatchLifecycle(
                    merchant_id=mid,
                    product_id=1,
                    sku_id=None,  # 无主批次
                    batch_label=f"null-{uuid.uuid4().hex[:8]}",
                    purchase_date=now,
                    purchase_qty=Decimal("10"),
                    remaining_qty=Decimal("10"),
                    expiry_date=now + timedelta(hours=72),
                    status="sellable",
                    unit_cost=Decimal("2.0"),
                )
            )
            sku = ProductSKU(merchant_id=mid, name="白菜", category_group="叶菜类")
            session.add(sku)
            await session.commit()
            sku_id = sku.id

        res = await client.post(
            "/api/v1/ops/waste",
            json={
                "product_id": 1,
                "sku_id": str(sku_id),
                "quantity": 1,
                "reason": "腐烂",
            },
        )
        assert res.status_code == 200, res.text
        assert res.json()["data"]["consumed"] == 1.0

        async with db_session() as session:
            batch = (
                (
                    await session.execute(
                        select(BatchLifecycle).where(
                            BatchLifecycle.merchant_id == mid,
                            BatchLifecycle.sku_id.is_(None),
                        )
                    )
                )
                .scalars()
                .one()
            )
            assert float(batch.remaining_qty) == 9.0

    async def test_waste_without_sku_still_works(self, client, db_session):
        """不带 sku_id 的报损行为不变（回归护栏）。"""
        from app.models.batch import BatchLifecycle

        mid = uuid.UUID(TEST_MERCHANT_ID)
        async with db_session() as session:
            now = utc_now()
            session.add(
                BatchLifecycle(
                    merchant_id=mid,
                    product_id=2,
                    sku_id=None,
                    batch_label=f"null-{uuid.uuid4().hex[:8]}",
                    purchase_date=now,
                    purchase_qty=Decimal("5"),
                    remaining_qty=Decimal("5"),
                    expiry_date=now + timedelta(hours=72),
                    status="sellable",
                    unit_cost=Decimal("1.0"),
                )
            )
            await session.commit()

        res = await client.post(
            "/api/v1/ops/waste", json={"product_id": 2, "quantity": 2, "reason": "碰伤"}
        )
        assert res.status_code == 200, res.text


# ------------------------------------------------------------------
# RA-10：offline-sync 金额符号归一
# ------------------------------------------------------------------


class TestRA10AmountSignNormalization:
    def _build(self, **kwargs):
        from app.schemas.inventory import OfflineSyncItem
        from app.services.offline_sync import _build_inventory_record

        item = OfflineSyncItem(idempotency_key="ra10-unit", **kwargs)
        return _build_inventory_record(uuid.uuid4(), item, 1)

    def test_refund_negative_total_flips_positive(self):
        """recheck 复现：refund quantity=-1/total=-6 → 落库 +1/+6（日报/日结同源）。"""
        record = self._build(
            event_type="refund", quantity=-1, unit_price=-6, total_amount=-6
        )
        assert record.quantity == Decimal("1")
        assert record.total_amount == Decimal("6")
        assert record.unit_price == Decimal("6")

    def test_purchase_negative_total_and_cost_flip_positive(self):
        record = self._build(
            event_type="purchase", quantity=5, unit_cost=-2, total_amount=-10
        )
        assert record.quantity == Decimal("5")
        assert record.total_amount == Decimal("10")
        assert record.unit_cost == Decimal("2")

    def test_sale_total_stays_positive(self):
        record = self._build(
            event_type="sale", quantity=2, unit_price=3.5, total_amount=7
        )
        assert record.quantity == Decimal("-2")
        assert record.total_amount == Decimal("7")

    def test_missing_total_computed_from_qty_times_unit_price(self):
        """无 total_amount 时按 |qty|×unit_price 补算（sale）。"""
        record = self._build(event_type="sale", quantity=-2, unit_price=3.5)
        assert record.quantity == Decimal("-2")
        assert record.total_amount == Decimal("7.00")

    def test_missing_total_computed_from_unit_cost_for_waste(self):
        record = self._build(event_type="waste", quantity=2, unit_cost=1.5)
        assert record.quantity == Decimal("-2")
        assert record.total_amount == Decimal("3.00")

    def test_cash_sale_amount_only_untouched(self):
        """纯现金收款（quantity 缺省 → -1 笔）：total_amount 原值恒正。"""
        record = self._build(event_type="sale", total_amount=8, product_name="现金收款")
        assert record.quantity == Decimal("-1")
        assert record.total_amount == Decimal("8")

    async def test_refund_endpoint_row_matches_settlement_sign(self, client, db_session):
        """端到端：offline refund 负数量/负金额 → 落库行 +1/+6，
        日结 refunds 与日报减项共用同一正数口径。"""
        from app.models.inventory import InventoryRecord

        key = "ra10-refund-e2e-001"
        resp = await client.post(
            "/api/v1/inventory/offline-sync",
            json={
                "items": [
                    {
                        "idempotency_key": key,
                        "event_type": "refund",
                        "product_id": 1,
                        "quantity": -1,
                        "unit": "斤",
                        "unit_price": -6,
                        "total_amount": -6,
                        "source": "offline",
                    }
                ]
            },
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["data"]["created"] == 1

        async with db_session() as session:
            row = (
                (
                    await session.execute(
                        select(InventoryRecord).where(
                            InventoryRecord.idempotency_key == key
                        )
                    )
                )
                .scalars()
                .one()
            )
        assert float(row.quantity) == 1.0
        assert float(row.total_amount) == 6.0


# ------------------------------------------------------------------
# RA-11：offline-sync event_type 白名单
# ------------------------------------------------------------------


class TestRA11EventTypeWhitelist:
    async def test_mixed_case_normalized_and_batch_consumed(self, client, db_session):
        """recheck 复现："Sale" 不再原样落库 —— 归一为 sale 且批次同步扣减。"""
        from app.models.batch import BatchLifecycle
        from app.models.inventory import InventoryRecord

        mid = uuid.UUID(TEST_MERCHANT_ID)
        async with db_session() as session:
            now = utc_now()
            session.add(
                BatchLifecycle(
                    merchant_id=mid,
                    product_id=1,
                    sku_id=None,
                    batch_label=f"null-{uuid.uuid4().hex[:8]}",
                    purchase_date=now,
                    purchase_qty=Decimal("10"),
                    remaining_qty=Decimal("10"),
                    expiry_date=now + timedelta(hours=72),
                    status="sellable",
                    unit_cost=Decimal("1.0"),
                )
            )
            await session.commit()

        key = "ra11-mixed-case-001"
        resp = await client.post(
            "/api/v1/inventory/offline-sync",
            json={
                "items": [
                    {
                        "idempotency_key": key,
                        "event_type": "Sale",
                        "product_id": 1,
                        "quantity": 3,
                        "unit": "斤",
                    }
                ]
            },
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["data"]["created"] == 1

        async with db_session() as session:
            row = (
                (
                    await session.execute(
                        select(InventoryRecord).where(
                            InventoryRecord.idempotency_key == key
                        )
                    )
                )
                .scalars()
                .one()
            )
            batch = (
                (
                    await session.execute(
                        select(BatchLifecycle).where(
                            BatchLifecycle.merchant_id == mid,
                            BatchLifecycle.sku_id.is_(None),
                        )
                    )
                )
                .scalars()
                .one()
            )
        assert row.event_type == "sale", "大小写变体必须归一为小写字面值"
        assert float(row.quantity) == -3.0
        assert float(batch.remaining_qty) == 7.0, "归一后批次必须同步消耗"

    async def test_unknown_type_rejected_422_listing_legal_values(self, client, db_session):
        """recheck 复现："mystery_type" → 422 并列出合法值（此前原样落库 +qty）。"""
        resp = await client.post(
            "/api/v1/inventory/offline-sync",
            json={
                "items": [
                    {
                        "idempotency_key": "ra11-mystery-001",
                        "event_type": "mystery_type",
                        "product_id": 1,
                        "quantity": 7,
                    }
                ]
            },
        )
        assert resp.status_code == 422, resp.text
        assert "sale / purchase / refund / waste" in resp.text

        from app.models.inventory import InventoryRecord

        async with db_session() as session:
            rows = (
                (
                    await session.execute(
                        select(InventoryRecord).where(
                            InventoryRecord.idempotency_key == "ra11-mystery-001"
                        )
                    )
                )
                .scalars()
                .all()
            )
        assert rows == [], "非法 event_type 不得落库"

    def test_schema_level_case_folding(self):
        from pydantic import ValidationError

        from app.schemas.inventory import OfflineSyncItem

        assert OfflineSyncItem(idempotency_key="k", event_type=" Refund ").event_type == "refund"
        with pytest.raises(ValidationError):
            OfflineSyncItem(idempotency_key="k", event_type="adjustment")


# ------------------------------------------------------------------
# RA-12：history 类端点 limit≤0 → 空列表
# ------------------------------------------------------------------


class TestRA12NegativeLimit:
    async def test_inventory_history_negative_limit_returns_empty(self, client, db_session):
        await _seed_purchase(db_session)
        for limit in (-5, 0):
            res = await client.get(
                "/api/v1/inventory/history", params={"limit": limit}
            )
            assert res.status_code == 200, res.text
            # AnyResponse 信封只保留 code/message/data（meta 不外露）
            assert res.json()["data"] == [], f"limit={limit} 应返回空列表（此前泄全量）"

    async def test_stocktake_history_negative_limit_returns_empty(self, client):
        for limit in (-5, 0):
            res = await client.get(
                "/api/v1/inventory/stocktake/history", params={"limit": limit}
            )
            assert res.status_code == 200, res.text
            assert res.json()["data"] == []

    async def test_positive_limit_still_works(self, client, db_session):
        """正 limit 行为不变（回归护栏）。"""
        await _seed_purchase(db_session)
        res = await client.get("/api/v1/inventory/history", params={"limit": 10})
        assert res.status_code == 200, res.text
        assert len(res.json()["data"]) >= 1
