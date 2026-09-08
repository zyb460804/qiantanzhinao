"""死信真重投回归测试。

覆盖两条链路：
  1. admin POST /dead-letters/{id}/retry → 立即调用 replay_dead_letter 执行一次
     真实重放（成功 → resolved，失败 → 计数/退避，超限 → failed），不再是
     只改 status=pending 却无消费者的假按钮；
  2. worker.process_dead_letter_retries → 定时扫描 next_retry_at 到期的
     pending 死信并重放（注入测试 session_factory，不碰生产 DB）。
"""

from __future__ import annotations

import uuid
from datetime import timedelta

import pytest_asyncio
from sqlalchemy import select
from tests.conftest import TEST_MERCHANT_ID

from app.core.admin_security import create_admin_token
from app.core.timezone import utc_now
from app.models.dead_letter import DeadLetterEvent
from app.models.inventory import InventoryRecord
from app.models.saas import PlatformAdmin


@pytest_asyncio.fixture
async def admin_headers(db_session):
    admin_id = uuid.uuid4()
    async with db_session() as session:
        session.add(
            PlatformAdmin(
                id=admin_id,
                email="admin-dl-test@example.com",
                password_hash="not-used-in-token-tests",
                name="死信测试管理员",
                role="super_admin",
                is_active=True,
            )
        )
        await session.commit()
    token = create_admin_token(admin_id, role="super_admin")
    return {"Authorization": f"Bearer {token}"}


def _purchase_payload(key: str) -> dict:
    return {
        "idempotency_key": key,
        "event_type": "purchase",
        "product_id": None,
        "product_name": "白菜",
        "quantity": 10,
        "unit": "斤",
        "unit_cost": 1.0,
        "unit_price": None,
        "total_amount": 10.0,
        "event_time": None,
        "notes": "",
        "source": "offline",
        "client_id": None,
        "client_reference": None,
    }


def _unsellable_sale_payload(key: str) -> dict:
    """无库存批次的大额 sale —— replay 必然触发「库存不足」。"""
    payload = _purchase_payload(key)
    payload.update(
        {
            "event_type": "sale",
            "quantity": 99999,
            "unit_cost": None,
            "unit_price": 2.0,
            "total_amount": 199998.0,
        }
    )
    return payload


async def _seed_dead_letter(db_session, payload: dict, **overrides) -> DeadLetterEvent:
    async with db_session() as session:
        fields = {
            "merchant_id": uuid.UUID(TEST_MERCHANT_ID),
            "idempotency_key": payload["idempotency_key"],
            "event_type": payload["event_type"],
            "payload": payload,
            "error_message": "库存不足，需要99999，可用0",
            "retry_count": 0,
            "max_retries": 3,
            "status": "pending",
            "next_retry_at": utc_now() - timedelta(minutes=1),
        }
        fields.update(overrides)
        event = DeadLetterEvent(**fields)
        session.add(event)
        await session.commit()
        await session.refresh(event)
        return event


class TestRetryEndpointReplays:
    async def test_retry_replays_event_and_resolves(self, client, db_session, admin_headers):
        """重试按钮必须真实重放：采购事件重放成功 → 事件 resolved + 库存流水落库。"""
        key = "dl-replay-success-001"
        event = await _seed_dead_letter(db_session, _purchase_payload(key))

        response = await client.post(
            f"/api/admin/dead-letters/{event.id}/retry",
            headers=admin_headers,
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["status"] == "resolved"
        assert body["result"] == "created"

        async with db_session() as session:
            record = (
                (
                    await session.execute(
                        select(InventoryRecord).where(InventoryRecord.idempotency_key == key)
                    )
                )
                .scalars()
                .one_or_none()
            )
            assert record is not None, "重放必须重新执行原事件，写入库存流水"

            stored = await session.get(DeadLetterEvent, event.id)
            assert stored.status == "resolved"
            assert stored.resolved_at is not None
            assert stored.next_retry_at is None

    async def test_retry_failure_increments_count_and_backs_off(
        self, client, db_session, admin_headers
    ):
        """重放失败 → 计数+1、指数退避 next_retry_at、状态保持 pending。"""
        event = await _seed_dead_letter(
            db_session, _unsellable_sale_payload("dl-replay-fail-001")
        )

        response = await client.post(
            f"/api/admin/dead-letters/{event.id}/retry",
            headers=admin_headers,
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["status"] == "pending"
        assert "重试" in body["message"]

        async with db_session() as session:
            stored = await session.get(DeadLetterEvent, event.id)
            assert stored.retry_count == 1
            assert stored.next_retry_at is not None
            assert stored.next_retry_at > utc_now()
            assert stored.status == "pending"

    async def test_retry_over_limit_marks_failed(self, client, db_session, admin_headers):
        """重放失败且已达 max_retries → 终态 failed，不再安排下次重试。"""
        event = await _seed_dead_letter(
            db_session,
            _unsellable_sale_payload("dl-replay-exhaust-001"),
            retry_count=2,
            max_retries=3,
        )

        response = await client.post(
            f"/api/admin/dead-letters/{event.id}/retry",
            headers=admin_headers,
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["status"] == "failed"

        async with db_session() as session:
            stored = await session.get(DeadLetterEvent, event.id)
            assert stored.status == "failed"
            assert stored.retry_count == 3
            assert stored.next_retry_at is None

    async def test_retry_idempotent_when_event_already_booked(
        self, client, db_session, admin_headers
    ):
        """事件此前已入账（duplicate）同样视为解决，不会重复记账。"""
        key = "dl-replay-duplicate-001"
        event = await _seed_dead_letter(db_session, _purchase_payload(key))
        # 先人工把事件真正入账一次（模拟崩溃前已写库、死信未更新的窗口）
        booked = await client.post(
            "/api/v1/inventory/offline-sync",
            json={"items": [_purchase_payload(key)]},
        )
        assert booked.status_code == 200

        response = await client.post(
            f"/api/admin/dead-letters/{event.id}/retry",
            headers=admin_headers,
        )
        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "resolved"
        assert body["result"] == "duplicate"

        async with db_session() as session:
            rows = (
                (
                    await session.execute(
                        select(InventoryRecord).where(InventoryRecord.idempotency_key == key)
                    )
                )
                .scalars()
                .all()
            )
            assert len(rows) == 1, "重放已入账事件不得双写"

    async def test_retry_unknown_dead_letter_returns_404(self, client, admin_headers):
        response = await client.post(
            f"/api/admin/dead-letters/{uuid.uuid4()}/retry",
            headers=admin_headers,
        )
        assert response.status_code == 404


class TestWorkerDeadLetterScan:
    async def test_scan_replays_only_due_pending_events(self, db_session):
        """worker 只重放 next_retry_at 已到期的 pending 死信，未到期的不动。"""
        from app.worker import process_dead_letter_retries

        due = await _seed_dead_letter(db_session, _purchase_payload("dl-worker-due-001"))
        not_due = await _seed_dead_letter(
            db_session,
            _unsellable_sale_payload("dl-worker-not-due-001"),
            next_retry_at=utc_now() + timedelta(hours=1),
        )
        terminal = await _seed_dead_letter(
            db_session,
            _unsellable_sale_payload("dl-worker-failed-001"),
            status="failed",
        )

        stats = await process_dead_letter_retries(session_factory=db_session)

        assert stats["scanned"] == 1
        assert stats["resolved"] == 1

        async with db_session() as session:
            assert (await session.get(DeadLetterEvent, due.id)).status == "resolved"
            untouched = await session.get(DeadLetterEvent, not_due.id)
            assert untouched.status == "pending"
            assert untouched.retry_count == 0
            assert (await session.get(DeadLetterEvent, terminal.id)).status == "failed"

    async def test_scan_backs_off_after_failure(self, db_session):
        """到期但重放失败的死信：计数+1 且 next_retry_at 推后（本轮不再扫到）。"""
        from app.worker import process_dead_letter_retries

        event = await _seed_dead_letter(
            db_session, _unsellable_sale_payload("dl-worker-backoff-001")
        )

        stats = await process_dead_letter_retries(session_factory=db_session)

        assert stats["scanned"] == 1
        assert stats["pending"] == 1
        assert stats["resolved"] == 0

        async with db_session() as session:
            stored = await session.get(DeadLetterEvent, event.id)
            assert stored.retry_count == 1
            assert stored.next_retry_at is not None
            assert stored.next_retry_at > utc_now()

        # 退避期内第二轮扫描不应再捞到它
        second = await process_dead_letter_retries(session_factory=db_session)
        assert second["scanned"] == 0
