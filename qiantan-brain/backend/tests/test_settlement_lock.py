"""Daily settlement lock tests — §4.10: 日结关闭后锁定业务日期.

Once a daily settlement is closed, new orders on that date must be rejected
with HTTP 409. This prevents back-dating fraud and keeps the books consistent.
"""

from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import select
from tests.conftest import TEST_MERCHANT_ID

from app.core.timezone import local_now, utc_now
from app.models.batch import BatchLifecycle
from app.models.pos import DailySettlement
from app.routers import pos as pos_module
from app.services.batch import create_batch


async def _seed_stock(db_session, quantity=10):
    async with db_session() as session:
        await create_batch(
            session, uuid.UUID(TEST_MERCHANT_ID), 1, "白菜",
            f"白菜-settle-{uuid.uuid4().hex[:6]}", Decimal(str(quantity)),
        )
        await session.commit()


async def _close_today_settlement(client, db_session):
    """Helper: close today's settlement and return the response."""
    settle_date = local_now().date().isoformat()
    res = await client.post(f"/api/v1/pos/daily-settlement/{settle_date}/close")
    assert res.status_code == 200
    return res


# ═══════════════════════════════════════════════════════════════════
# Settlement Lock
# ═══════════════════════════════════════════════════════════════════


class TestSettlementLock:
    async def test_new_order_blocked_after_settlement(self, client, db_session):
        """日结关闭后创建新订单应拒绝 409."""
        await _seed_stock(db_session, quantity=20)
        settle_date = local_now().date().isoformat()

        # Close first
        await client.post(f"/api/v1/pos/daily-settlement/{settle_date}/close")

        # Try to create new order
        res = await client.post("/api/v1/pos/orders", json={
            "client_id": "after-settle-001",
            "payment_method": "cash",
            "items": [{"product_id": 1, "quantity": 2, "unit": "斤", "unit_price": 3.5}],
        })
        assert res.status_code == 409
        assert "日结已关闭" in res.json()["detail"]

    async def test_orders_allowed_before_settlement(self, client, db_session):
        """日结未关闭时正常创建订单."""
        await _seed_stock(db_session, quantity=10)
        res = await client.post("/api/v1/pos/orders", json={
            "client_id": "before-settle-001",
            "payment_method": "cash",
            "items": [{"product_id": 1, "quantity": 2, "unit": "斤", "unit_price": 3.0}],
        })
        assert res.status_code == 200

    async def test_settlement_does_not_block_future(self, client, db_session):
        """关闭今天的日结不影响其他日期的结算查询."""
        await _seed_stock(db_session, quantity=20)
        settle_date = local_now().date().isoformat()
        await _close_today_settlement(client, db_session)

        # Future date settlement should show as "open"
        future_date = date(2099, 1, 1).isoformat()
        res = await client.get(f"/api/v1/pos/daily-settlement/{future_date}")
        assert res.status_code == 200
        assert res.json()["data"]["status"] == "open"

    async def test_reclose_rejected(self, client, db_session):
        """重复关闭日结应被拒绝（H3 安全修复）."""
        await _seed_stock(db_session, quantity=20)
        settle_date = local_now().date().isoformat()
        first = await client.post(f"/api/v1/pos/daily-settlement/{settle_date}/close")
        assert first.status_code == 200
        res = await client.post(f"/api/v1/pos/daily-settlement/{settle_date}/close")
        assert res.status_code == 409
        assert "已关闭" in res.json()["detail"]

    async def test_past_date_settlement_also_blocks(self, client, db_session):
        """即使是过去的日期，关闭后也应拒绝新订单."""
        await _seed_stock(db_session, quantity=20)
        yesterday = (utc_now().date().replace(day=utc_now().day - 1)
                      if utc_now().day > 1
                      else utc_now().date().replace(month=utc_now().month - 1, day=28))

        # Close yesterday's settlement
        await client.post(f"/api/v1/pos/daily-settlement/{yesterday.isoformat()}/close")

        # Today should still work
        res = await client.post("/api/v1/pos/orders", json={
            "client_id": "today-after-yesterday-close-001",
            "payment_method": "cash",
            "items": [{"product_id": 1, "quantity": 1, "unit": "斤", "unit_price": 3.0}],
        })
        assert res.status_code == 200  # Only today is locked by today's close


# ═══════════════════════════════════════════════════════════════════
# V5-H1: 日结锁定的日界必须按 CST 业务日判定（Docker UTC 下不误锁/拒日结）
# ═══════════════════════════════════════════════════════════════════


class TestSettlementLockCstDayBoundary:
    """模拟「UTC 2026-08-15 20:00 = CST 2026-08-16 04:00」的部署时钟.

    旧实现按 local_now().date()（= 服务器/UTC 日期 08-15）查锁，会把已关闭的
    08-15 日结误套到 CST 08-16 凌晨的新单上；同理 08-16 的日结会被未来日期
    守卫拒绝。改用 cst_today() 后两者都必须正确。
    """

    CST_DAY = date(2026, 8, 16)  # CST「今天」（UTC 时钟仍停在 08-15 20:00）
    UTC_DAY = date(2026, 8, 15)  # 服务器本地（UTC）日期

    def _freeze_cst_today(self, monkeypatch):
        monkeypatch.setattr(pos_module, "cst_today", lambda: self.CST_DAY)

    async def test_utc_early_hours_do_not_mislock_previous_day(
        self, client, db_session, monkeypatch
    ):
        """CST 凌晨 0-8 点：前一 UTC 日的日结已关闭，不得锁住 CST 今天的新单."""
        await _seed_stock(db_session, quantity=10)
        self._freeze_cst_today(monkeypatch)

        closed = await client.post(f"/api/v1/pos/daily-settlement/{self.UTC_DAY.isoformat()}/close")
        assert closed.status_code == 200

        res = await client.post("/api/v1/pos/orders", json={
            "client_id": "cst-mislock-guard-001",
            "payment_method": "cash",
            "items": [{"product_id": 1, "quantity": 1, "unit": "斤", "unit_price": 3.0}],
        })
        assert res.status_code == 200  # 旧实现此处误 409（锁到 UTC 昨天）

    async def test_cst_today_close_blocks_new_orders(self, client, db_session, monkeypatch):
        """CST 今天的日结关闭后，CST 今天的新单必须被锁（409）."""
        await _seed_stock(db_session, quantity=10)
        self._freeze_cst_today(monkeypatch)

        closed = await client.post(
            f"/api/v1/pos/daily-settlement/{self.CST_DAY.isoformat()}/close"
        )
        assert closed.status_code == 200

        res = await client.post("/api/v1/pos/orders", json={
            "client_id": "cst-today-lock-001",
            "payment_method": "cash",
            "items": [{"product_id": 1, "quantity": 1, "unit": "斤", "unit_price": 3.0}],
        })
        assert res.status_code == 409
        assert "日结已关闭" in res.json()["detail"]

    async def test_future_guard_uses_cst_day(self, client, db_session, monkeypatch):
        """未来日期守卫按 CST 今天判定：CST 今天可结、CST 明天 400."""
        self._freeze_cst_today(monkeypatch)

        future = await client.post("/api/v1/pos/daily-settlement/2026-08-17/close")
        assert future.status_code == 400
        assert "未来" in future.json()["detail"]

        today = await client.post(
            f"/api/v1/pos/daily-settlement/{self.CST_DAY.isoformat()}/close"
        )
        assert today.status_code == 200


# ═══════════════════════════════════════════════════════════════════
# 鉴权
# ═══════════════════════════════════════════════════════════════════


class TestUnauthenticated:
    async def test_close_settlement_no_auth(self, auth_client):
        res = await auth_client.post("/api/v1/pos/daily-settlement/2026-01-01/close")
        assert res.status_code == 401
