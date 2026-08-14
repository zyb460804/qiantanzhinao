"""Tests for inventory router — stock ledger summary (§4.4), current inventory.

Covers §4.4 and §6: normal flow, boundary, auth, cross-tenant isolation.
"""

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select
from tests.conftest import TEST_MERCHANT_ID

from app.models.accounts import CustomerReceivable
from app.models.batch import BatchLifecycle
from app.models.inventory import InventoryRecord
from app.models.stocktake import StocktakeSession
from app.models.voice import VoiceLog


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


class TestVoidRecordSourceDispatch:
    """第五轮 V2-H1：inventory void 按 source 分发，消除跨路径双撤销竞态。"""

    async def test_void_pos_source_rejected(self, client, db_session):
        """source="pos" 的流水直接撤销 → 409 引导订单退款链路，记录不动。"""
        async with db_session() as session:
            record = InventoryRecord(
                merchant_id=uuid.UUID(TEST_MERCHANT_ID),
                product_id=1,
                quantity=Decimal("-5"),
                unit="斤",
                event_type="sale",
                event_time=datetime.now(),
                source="pos",
            )
            session.add(record)
            await session.commit()
            record_id = str(record.id)

        resp = await client.post(
            f"/api/v1/inventory/{record_id}/void", json={"reason": "误操作"}
        )

        assert resp.status_code == 409
        assert "订单退款" in resp.json()["detail"]
        async with db_session() as session:
            row = await session.get(InventoryRecord, uuid.UUID(record_id))
            assert row.is_voided is False
            assert row.voided_at is None

    async def test_void_voice_source_delegates_and_is_idempotent_across_paths(
        self, client, db_session
    ):
        """source="voice" 的流水经 inventory 撤销 → 走共享核心：
        批次回滚 + VoiceLog 状态翻转 + 往来账冲销一次落地；
        之后 voice 侧再撤销同一条单 → 409（跨路径幂等，无双回滚）。"""
        # 采购 20 斤（造批次），再赊账卖出 5 斤（造应收 + 批次消耗）
        purchase = await client.post(
            "/api/v1/voice/parse-text",
            json={"merchant_id": TEST_MERCHANT_ID, "text": "进了白菜20斤，三毛钱一斤"},
        )
        purchase_log = purchase.json()["data"]["parsed"]["voice_log_id"]
        assert (
            await client.post("/api/v1/voice/confirm", json={"voice_log_id": purchase_log})
        ).status_code == 200

        async with db_session() as session:
            log = VoiceLog(
                merchant_id=uuid.UUID(TEST_MERCHANT_ID),
                asr_text="seed",
                parsed_event={
                    "event_type": "sale",
                    "product": "白菜",
                    "product_id": 1,
                    "quantity": 5,
                    "unit": "斤",
                    "unit_price": 3,
                    "total_amount": 15,
                    "party_name": "张记饭店",
                    "is_credit": True,
                    "is_repay": False,
                },
                status="parsed",
            )
            session.add(log)
            await session.commit()
            sale_log_id = log.id

        confirm = await client.post(
            "/api/v1/voice/confirm", json={"voice_log_id": str(sale_log_id)}
        )
        assert confirm.status_code == 200

        async with db_session() as session:
            sale_record = (
                await session.execute(
                    select(InventoryRecord).where(
                        InventoryRecord.voice_log_id == sale_log_id,
                        InventoryRecord.is_voided.is_(False),
                    )
                )
            ).scalar_one()
            record_id = str(sale_record.id)
            batch = (
                await session.execute(
                    select(BatchLifecycle).where(
                        BatchLifecycle.merchant_id == uuid.UUID(TEST_MERCHANT_ID),
                        BatchLifecycle.product_id == 1,
                    )
                )
            ).scalar_one()
            assert float(batch.remaining_qty) == 15.0  # 20 - 5

        void = await client.post(
            f"/api/v1/inventory/{record_id}/void", json={"reason": "记错了"}
        )
        assert void.status_code == 200
        assert void.json()["code"] == 0

        async with db_session() as session:
            row = await session.get(InventoryRecord, uuid.UUID(record_id))
            log = await session.get(VoiceLog, sale_log_id)
            assert row.is_voided is True
            assert row.voided_by == "manual"
            assert log.status == "voided"
            # 批次回滚恰好一次：15 → 20
            batch = (
                await session.execute(
                    select(BatchLifecycle).where(
                        BatchLifecycle.merchant_id == uuid.UUID(TEST_MERCHANT_ID),
                        BatchLifecycle.product_id == 1,
                    )
                )
            ).scalar_one()
            assert float(batch.remaining_qty) == 20.0
            # 往来账冲销恰好一次（charge 15 + void repay 15，净额 0）
            receivables = (
                (
                    await session.execute(
                        select(CustomerReceivable).where(
                            CustomerReceivable.merchant_id == uuid.UUID(TEST_MERCHANT_ID)
                        )
                    )
                )
                .scalars()
                .all()
            )
            keys = sorted(r.idempotency_key for r in receivables)
            assert keys == [f"voice:{sale_log_id}:charge", f"voice:{sale_log_id}:void:repay"]

        # voice 侧再撤销同一单 → 409，不产生第二轮回滚/冲销
        voice_void = await client.post(
            f"/api/v1/voice/{sale_log_id}/void", json={"reason": "重复撤销"}
        )
        assert voice_void.status_code == 409
        assert "已撤销" in voice_void.json()["detail"]

    async def test_void_voice_record_without_log_uses_manual_path(self, client, db_session):
        """source="voice" 但无 voice_log_id（历史数据）→ 走手动路径正常撤销。"""
        async with db_session() as session:
            record = InventoryRecord(
                merchant_id=uuid.UUID(TEST_MERCHANT_ID),
                product_id=1,
                quantity=Decimal("10"),
                unit="斤",
                event_type="sale",
                event_time=datetime.now(),
                source="voice",
                voice_log_id=None,
            )
            session.add(record)
            await session.commit()
            record_id = str(record.id)

        resp = await client.post(f"/api/v1/inventory/{record_id}/void", json={"reason": "历史"})
        assert resp.status_code == 200

        again = await client.post(f"/api/v1/inventory/{record_id}/void", json={"reason": "再点"})
        assert again.status_code == 409


class TestStocktakeAnchorLocks:
    """第五轮 V2-H3：submit / submit-batch / cancel 入口锚点行锁 + 状态守卫。

    SQLite 忽略 FOR UPDATE（生产 PG16 生效），此处验证守卫语义：
    已结束（completed/cancelled）的会话不再接受提交/取消。
    """

    async def _start_and_complete(self, client):
        start = (await client.post("/api/v1/inventory/stocktake/start", json={})).json()["data"]
        session_id = start["session_id"]
        for item in start["items"]:
            resp = await client.post(
                f"/api/v1/inventory/stocktake/{session_id}/submit",
                json={"product_id": item["product_id"], "actual_qty": item["book_qty"]},
            )
            assert resp.status_code == 200
        complete = await client.post(
            f"/api/v1/inventory/stocktake/{session_id}/complete", json={}
        )
        assert complete.status_code == 200
        return session_id

    async def test_submit_after_complete_rejected(self, client, db_session):
        session_id = await self._start_and_complete(client)

        resp = await client.post(
            f"/api/v1/inventory/stocktake/{session_id}/submit",
            json={"product_id": 1, "actual_qty": 3},
        )
        assert resp.status_code == 400
        assert "已结束" in resp.json()["detail"]

    async def test_submit_batch_after_complete_rejected(self, client, db_session):
        session_id = await self._start_and_complete(client)

        resp = await client.post(
            f"/api/v1/inventory/stocktake/{session_id}/submit-batch",
            json={"items": [{"product_id": 1, "actual_qty": 3}]},
        )
        assert resp.status_code == 400
        assert "已结束" in resp.json()["detail"]

    async def test_cancel_after_complete_keeps_completed(self, client, db_session):
        """completed 不会被 cancel 覆写为 cancelled（锚点锁 + 状态守卫）。"""
        session_id = await self._start_and_complete(client)

        resp = await client.post(f"/api/v1/inventory/stocktake/{session_id}/cancel")
        assert resp.status_code == 400

        async with db_session() as session:
            row = await session.get(StocktakeSession, uuid.UUID(session_id))
            assert row.status == "completed"
