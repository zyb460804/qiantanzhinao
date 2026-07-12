"""Food safety state machine integration tests — lock, unlock, recall, destroy.

Covers §4.14, §5.7: batch state transitions, illegal jump rejection,
cross-tenant isolation, and POS integration.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

import pytest
from sqlalchemy import func, select
from tests.conftest import TEST_MERCHANT_ID

from app.models.audit import AuditLog
from app.models.batch import BatchLifecycle
from app.models.inventory import InventoryRecord
from app.services.batch import (
    consume_batches_fifo,
    create_batch,
    destroy_batch,
    lock_batch,
    recall_batch,
    unlock_batch,
)


async def _seed_batch(db_session, merchant_id=TEST_MERCHANT_ID, quantity=10,
                      product_id=1, product_name="白菜", batch_label=None,
                      status="sellable"):
    async with db_session() as session:
        batch = await create_batch(
            session, uuid.UUID(merchant_id), product_id, product_name,
            batch_label or f"{product_name}-foodsafe-{uuid.uuid4().hex[:6]}",
            Decimal(str(quantity)),
        )
        if status != "sellable":
            batch.status = status
        await session.commit()
        await session.refresh(batch)
        return batch.id


# ═══════════════════════════════════════════════════════════════════
# 状态机合法转换
# ═══════════════════════════════════════════════════════════════════


class TestStateMachineHappyPath:
    async def test_lock_sellable_batch(self, client, db_session):
        """§4.14: 锁定可售批次."""
        bid = await _seed_batch(db_session, batch_label="lock-me-001")
        res = await client.post(f"/api/v1/food-safety/batches/{bid}/lock",
                                json={"reason": "快检不合格"})
        assert res.status_code == 200
        async with db_session() as session:
            batch = await session.get(BatchLifecycle, bid)
            assert batch.status == "locked"
            assert batch.locked_reason == "快检不合格"

    async def test_unlock_locked_batch(self, client, db_session):
        """解锁后恢复正常销售."""
        bid = await _seed_batch(db_session, batch_label="unlock-me-001")
        mid = uuid.UUID(TEST_MERCHANT_ID)
        async with db_session() as session:
            await lock_batch(session, bid, mid, "快检不合格", "merchant")
            await session.commit()
        res = await client.post(f"/api/v1/food-safety/batches/{bid}/unlock")
        assert res.status_code == 200
        async with db_session() as session:
            batch = await session.get(BatchLifecycle, bid)
            assert batch.status == "sellable"

    async def test_recall_locked_batch(self, client, db_session):
        """召回已锁定批次."""
        bid = await _seed_batch(db_session, batch_label="recall-me-001")
        mid = uuid.UUID(TEST_MERCHANT_ID)
        async with db_session() as session:
            await lock_batch(session, bid, mid, "快检不合格", "merchant")
            await session.commit()
        res = await client.post(f"/api/v1/food-safety/batches/{bid}/recall",
                                json={"reason": "食品安全召回"})
        assert res.status_code == 200
        async with db_session() as session:
            batch = await session.get(BatchLifecycle, bid)
            assert batch.status == "recalled"

    async def test_destroy_recalled_batch(self, client, db_session):
        """销毁已召回批次 → 写入报损流水."""
        bid = await _seed_batch(db_session, batch_label="destroy-me-001")
        mid = uuid.UUID(TEST_MERCHANT_ID)
        async with db_session() as session:
            await lock_batch(session, bid, mid, "快检不合格", "merchant")
            await recall_batch(session, bid, mid, "食品安全召回")
            await session.commit()
        res = await client.post(f"/api/v1/food-safety/batches/{bid}/destroy",
                                json={"reason": "不合格销毁"})
        assert res.status_code == 200
        async with db_session() as session:
            batch = await session.get(BatchLifecycle, bid)
            assert batch.status == "destroyed"
            # 报损流水已写入
            waste_records = (await session.execute(
                select(InventoryRecord).where(
                    InventoryRecord.event_type == "waste",
                    InventoryRecord.source == "food_safety",
                )
            )).scalars().all()
            assert len(waste_records) >= 1
            assert float(waste_records[0].quantity) < 0  # negative = deduction


class TestStateMachineIllegalTransitions:
    async def test_cannot_lock_destroyed(self, client, db_session):
        """已销毁批次不能锁定."""
        bid = await _seed_batch(db_session, batch_label="destroyed-x-001", status="destroyed")
        res = await client.post(f"/api/v1/food-safety/batches/{bid}/lock",
                                json={"reason": "test"})
        assert res.status_code == 409

    async def test_cannot_recall_sellable(self, client, db_session):
        """可售批次不能直接召回 — 必须先锁定."""
        bid = await _seed_batch(db_session, batch_label="sellable-x-001")
        res = await client.post(f"/api/v1/food-safety/batches/{bid}/recall",
                                json={"reason": "test"})
        assert res.status_code == 409

    async def test_cannot_destroy_sellable(self, client, db_session):
        """可售批次不能直接销毁."""
        bid = await _seed_batch(db_session, batch_label="sellable-x-002")
        res = await client.post(f"/api/v1/food-safety/batches/{bid}/destroy",
                                json={"reason": "test"})
        assert res.status_code == 409

    async def test_cannot_unlock_sellable(self, client, db_session):
        """已解锁的批次不能再次解锁."""
        bid = await _seed_batch(db_session, batch_label="sellable-x-003")
        res = await client.post(f"/api/v1/food-safety/batches/{bid}/unlock")
        assert res.status_code == 409

    async def test_cannot_destroy_locked_directly(self, client, db_session):
        """锁定批次不能直接销毁 — 必须先召回."""
        bid = await _seed_batch(db_session, batch_label="locked-x-001")
        mid = uuid.UUID(TEST_MERCHANT_ID)
        async with db_session() as session:
            await lock_batch(session, bid, mid, "test", "merchant")
            await session.commit()
        res = await client.post(f"/api/v1/food-safety/batches/{bid}/destroy",
                                json={"reason": "test"})
        assert res.status_code == 409


# ═══════════════════════════════════════════════════════════════════
# 商户隔离
# ═══════════════════════════════════════════════════════════════════


class TestCrossMerchantIsolation:
    async def test_cannot_lock_other_merchant_batch(self, client, db_session):
        """不能锁定其他商户的批次."""
        bid = await _seed_batch(db_session, batch_label="other-m-001")
        other_id = str(uuid.uuid4())
        res = await client.post(
            f"/api/v1/food-safety/batches/{bid}/lock",
            json={"reason": "test"},
            headers={"X-Test-Merchant-Id": other_id},
        )
        assert res.status_code in (404, 409)

    async def test_cannot_unlock_other_merchant_batch(self, client, db_session):
        """不能解锁其他商户的批次."""
        bid = await _seed_batch(db_session, batch_label="other-m-002")
        other_id = str(uuid.uuid4())
        res = await client.post(
            f"/api/v1/food-safety/batches/{bid}/unlock",
            headers={"X-Test-Merchant-Id": other_id},
        )
        assert res.status_code in (404, 409)


# ═══════════════════════════════════════════════════════════════════
# 审计日志
# ═══════════════════════════════════════════════════════════════════


class TestAuditTrail:
    async def test_lock_writes_audit_log(self, client, db_session):
        """锁定操作写入审计日志."""
        bid = await _seed_batch(db_session, batch_label="audit-lock-001")
        res = await client.post(f"/api/v1/food-safety/batches/{bid}/lock",
                                json={"reason": "快检不合格"})
        assert res.status_code == 200
        async with db_session() as session:
            logs = (await session.execute(
                select(AuditLog).where(
                    AuditLog.action == "batch_lock",
                    AuditLog.target_id == str(bid),
                )
            )).scalars().all()
            assert len(logs) == 1

    async def test_unlock_writes_audit_log(self, client, db_session):
        """解锁操作写入审计日志."""
        bid = await _seed_batch(db_session, batch_label="audit-unlock-001")
        mid = uuid.UUID(TEST_MERCHANT_ID)
        async with db_session() as session:
            await lock_batch(session, bid, mid, "快检不合格", "merchant")
            await session.commit()
        res = await client.post(f"/api/v1/food-safety/batches/{bid}/unlock")
        assert res.status_code == 200
        async with db_session() as session:
            logs = (await session.execute(
                select(AuditLog).where(
                    AuditLog.action == "batch_unlock",
                    AuditLog.target_id == str(bid),
                )
            )).scalars().all()
            assert len(logs) == 1

    async def test_destroy_writes_audit_log(self, client, db_session):
        """销毁写入审计日志."""
        bid = await _seed_batch(db_session, batch_label="audit-destroy-001")
        mid = uuid.UUID(TEST_MERCHANT_ID)
        async with db_session() as session:
            await lock_batch(session, bid, mid, "快检不合格", "merchant")
            await recall_batch(session, bid, mid, "食品安全召回")
            await session.commit()
        res = await client.post(f"/api/v1/food-safety/batches/{bid}/destroy",
                                json={"reason": "不合格销毁"})
        assert res.status_code == 200
        async with db_session() as session:
            logs = (await session.execute(
                select(AuditLog).where(
                    AuditLog.action == "batch_destroy",
                    AuditLog.target_id == str(bid),
                )
            )).scalars().all()
            assert len(logs) == 1


# ═══════════════════════════════════════════════════════════════════
# FIFO跳过锁定批次
# ═══════════════════════════════════════════════════════════════════


class TestFIFOSkipsLocked:
    async def test_fifo_skips_locked_batches(self, db_session):
        """FIFO消费自动跳过locked/recalled/destroyed批次."""
        mid = uuid.UUID(TEST_MERCHANT_ID)
        async with db_session() as session:
            await create_batch(session, mid, 1, "白菜", "locked-skip-001",
                               Decimal("5"))
            locked = await create_batch(session, mid, 1, "白菜", "fifo-locked",
                                        Decimal("5"))
            locked.status = "locked"
            await session.commit()

        # 只消费 sellable 的那 5 斤
        async with db_session() as session:
            consumed = await consume_batches_fifo(session, mid, 1, Decimal("5"))
            assert consumed == Decimal("5")
            await session.commit()

        # 锁定批次剩余不变
        async with db_session() as session:
            locked = await session.get(BatchLifecycle, locked.id)
            assert float(locked.remaining_qty) == 5  # 跳过


# ═══════════════════════════════════════════════════════════════════
# 鉴权
# ═══════════════════════════════════════════════════════════════════


class TestUnauthenticated:
    async def test_lock_no_auth(self, auth_client, db_session):
        bid = await _seed_batch(db_session, batch_label="noauth-lock-001")
        res = await auth_client.post(f"/api/v1/food-safety/batches/{bid}/lock")
        assert res.status_code == 401

    async def test_recall_no_auth(self, auth_client, db_session):
        bid = await _seed_batch(db_session, batch_label="noauth-recall-001")
        res = await auth_client.post(f"/api/v1/food-safety/batches/{bid}/recall")
        assert res.status_code == 401

    async def test_destroy_no_auth(self, auth_client, db_session):
        bid = await _seed_batch(db_session, batch_label="noauth-destroy-001")
        res = await auth_client.post(f"/api/v1/food-safety/batches/{bid}/destroy")
        assert res.status_code == 401
