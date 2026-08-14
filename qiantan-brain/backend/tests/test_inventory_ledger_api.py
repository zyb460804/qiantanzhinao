"""Tests for inventory router — stock ledger summary (§4.4), current inventory.

Covers §4.4 and §6: normal flow, boundary, auth, cross-tenant isolation.
"""

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select
from tests.conftest import TEST_MERCHANT_ID

from app.models.batch import BatchLifecycle
from app.models.inventory import InventoryRecord


class TestCurrentInventory:
    async def test_get_current_inventory(self, client):
        res = await client.get("/api/v1/inventory/current")
        assert res.status_code == 200
        data = res.json()
        assert data["code"] == 0
        assert isinstance(data["data"], list)

    async def test_current_inventory_no_auth(self, auth_client):
        res = await auth_client.get("/api/v1/inventory/current")
        assert res.status_code == 401


class TestStockLedgerSummary:
    """§4.4: 库存统一流水报告。"""

    async def test_ledger_summary_empty(self, client):
        res = await client.get("/api/v1/inventory/ledger/summary")
        assert res.status_code == 200
        data = res.json()["data"]
        assert "inventory_states" in data
        states = data["inventory_states"]
        assert "book" in states
        assert "sellable" in states
        assert "locked" in states
        assert "held" in states
        assert "waste_this_month" in states

    async def test_ledger_summary_has_by_event_type(self, client):
        res = await client.get("/api/v1/inventory/ledger/summary")
        data = res.json()["data"]
        assert "by_event_type" in data
        assert isinstance(data["by_event_type"], dict)

    async def test_ledger_summary_has_active_products(self, client):
        res = await client.get("/api/v1/inventory/ledger/summary")
        data = res.json()["data"]
        assert data["active_products"] >= 4  # 白菜/土豆/豆腐/猪肉

    async def test_ledger_summary_cross_merchant(self, client):
        other = str(uuid.uuid4())
        res = await client.get("/api/v1/inventory/ledger/summary",
                               headers={"X-Test-Merchant-Id": other})
        assert res.status_code == 200
        data = res.json()["data"]
        assert data["inventory_states"]["book"]["quantity"] == 0


class TestInventoryAlerts:
    async def test_alerts_empty(self, client):
        res = await client.get("/api/v1/inventory/alerts")
        assert res.status_code == 200
        data = res.json()["data"]
        assert "expiry_alerts" in data
        assert data["expiring_count"] == 0

    async def test_alerts_with_active_batch_naive_purchase_date(self, client, db_session):
        """带活跃批次（purchase_date 为 SQLite 读回的 naive 时间）→ 200 并产出告警。

        回归（HIGH）：calc_batch_status 曾用 aware utc_now() 减 naive
        purchase_date → TypeError → /inventory/alerts 必 500。
        """
        # 白菜（叶菜类）：40h 前入库 → attention 档（36-54h）
        naive_purchase = datetime.now(UTC).replace(tzinfo=None) - timedelta(hours=40)
        async with db_session() as session:
            session.add(
                BatchLifecycle(
                    merchant_id=uuid.UUID(TEST_MERCHANT_ID),
                    product_id=1,  # 白菜
                    batch_label="白菜-naive-40h",
                    purchase_date=naive_purchase,
                    purchase_qty=Decimal("10"),
                    remaining_qty=Decimal("8"),
                    status="sellable",
                )
            )
            await session.commit()

        res = await client.get("/api/v1/inventory/alerts")
        assert res.status_code == 200
        data = res.json()["data"]
        assert data["expiring_count"] == 1
        alert = data["expiry_alerts"][0]
        assert alert["product_name"] == "白菜"
        assert alert["status"] == "attention"
        assert alert["hours_remaining"] is not None

    async def test_twin_inventory_mirror_with_active_batch_naive_purchase_date(
        self, client, db_session
    ):
        """/twin/inventory-mirror 同链路：活跃批次 naive purchase_date → 200 非 500。"""
        naive_purchase = datetime.now(UTC).replace(tzinfo=None) - timedelta(hours=60)
        async with db_session() as session:
            # twin 端点从库存流水聚合解析商品名（无流水时回退“商品N”→未知品类，
            # 不会进入时间运算），需补一条 purchase 流水使名称解析到“白菜”。
            session.add(
                InventoryRecord(
                    merchant_id=uuid.UUID(TEST_MERCHANT_ID),
                    product_id=1,
                    quantity=Decimal("10"),
                    unit="斤",
                    event_type="purchase",
                    event_time=datetime.now(),
                )
            )
            session.add(
                BatchLifecycle(
                    merchant_id=uuid.UUID(TEST_MERCHANT_ID),
                    product_id=1,  # 白菜
                    batch_label="白菜-naive-60h",
                    purchase_date=naive_purchase,
                    purchase_qty=Decimal("10"),
                    remaining_qty=Decimal("6"),
                    status="sellable",
                )
            )
            await session.commit()

        res = await client.get("/api/v1/twin/inventory-mirror")
        assert res.status_code == 200
        data = res.json()["data"]
        heatmap = data["lifecycle_heatmap"]
        entry = next(item for item in heatmap if item["batch_label"] == "白菜-naive-60h")
        assert entry["product_name"] == "白菜"
        assert entry["status"] == "expiring"  # 叶菜类 54-72h


class TestVoidRecord:
    async def test_void_nonexistent_record(self, client):
        fake_id = str(uuid.uuid4())
        res = await client.post(f"/api/v1/inventory/{fake_id}/void",
                                json={"reason": "test"})
        assert res.status_code == 404

    async def test_void_already_voided_record_rejected(self, client, db_session):
        """二次撤销同一记录 → 409，且不产生第二次批次回滚。

        void 入口 SELECT ... FOR UPDATE 锚点行锁后，is_voided 守卫语义完整：
        并发/重复撤销恰好落地一次回滚（SQLite 忽略 FOR UPDATE 属预期，
        此处验证守卫逻辑无回归）。
        """
        async with db_session() as session:
            record = InventoryRecord(
                merchant_id=uuid.UUID(TEST_MERCHANT_ID),
                product_id=1,
                quantity=Decimal("10"),
                unit="斤",
                event_type="sale",
                event_time=datetime.now(),
            )
            session.add(record)
            await session.commit()
            record_id = str(record.id)

        first = await client.post(
            f"/api/v1/inventory/{record_id}/void", json={"reason": "第一次撤销"}
        )
        assert first.status_code == 200
        assert first.json()["code"] == 0

        second = await client.post(
            f"/api/v1/inventory/{record_id}/void", json={"reason": "重复撤销"}
        )
        assert second.status_code == 409

        # 守卫拦截后审计日志也只有一条（撤销动作未重复落库）。
        from app.models.audit import AuditLog

        async with db_session() as session:
            audits = (
                await session.execute(
                    select(AuditLog).where(
                        AuditLog.target_id == record_id,
                        AuditLog.action == "void",
                    )
                )
            ).scalars().all()
            assert len(audits) == 1
