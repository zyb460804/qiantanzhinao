"""发票系统幂等性测试 — 周期唯一约束 + 数据库侧发号（正确性 H6 / 并发 H6+M5）。

覆盖两个发票入口（worker generate_invoices / admin invoices 路由）：
1. 同订阅同周期重复生成（worker 与 admin 两入口）→ 只出一票，重复调用幂等返回；
2. 进程内连续开多票 → invoice_no 单调递增不重复（数据库侧 MAX+1 发号）；
3. 并发 gather 5 个同周期生成 → 恰好 1 张票；
4. 手工发票（无订阅/无周期，NULL 键）不受 (subscription_id, period_start) 唯一约束影响；
5. 手工开票指定已有 (subscription_id, period_start) → 409 而非 500。
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest_asyncio
from sqlalchemy import func, select

import app.worker as worker_module
from app.core.admin_security import create_admin_token
from app.models.saas import Invoice, Plan, PlatformAdmin, Subscription, Tenant
from app.worker import generate_invoices


@pytest_asyncio.fixture
async def admin_headers(db_session):
    admin_id = uuid.uuid4()
    async with db_session() as session:
        session.add(
            PlatformAdmin(
                id=admin_id,
                email="admin-invoice-idem@example.com",
                password_hash="not-used-in-token-tests",
                name="发票幂等测试管理员",
                role="super_admin",
                is_active=True,
            )
        )
        await session.commit()
    token = create_admin_token(admin_id, role="super_admin")
    return {"Authorization": f"Bearer {token}"}


async def _seed_subscription(
    db_session,
    *,
    current_period_start: datetime | None,
    current_period_end: datetime | None,
) -> tuple[uuid.UUID, uuid.UUID]:
    """播种 租户+套餐+订阅，返回 (tenant_id, subscription_id)。"""
    tenant_id = uuid.uuid4()
    plan_id = uuid.uuid4()
    subscription_id = uuid.uuid4()
    async with db_session() as session:
        session.add(
            Plan(
                id=plan_id,
                code=f"pro-{uuid.uuid4().hex[:8]}",
                name="专业版",
                price_monthly=Decimal("99.00"),
                price_yearly=Decimal("990.00"),
            )
        )
        session.add(Tenant(id=tenant_id, name="发票幂等租户", slug=uuid.uuid4().hex[:12]))
        session.add(
            Subscription(
                id=subscription_id,
                tenant_id=tenant_id,
                plan_id=plan_id,
                billing_cycle="monthly",
                status="active",
                current_period_start=current_period_start,
                current_period_end=current_period_end,
                auto_renew=True,
            )
        )
        await session.commit()
    return tenant_id, subscription_id


async def _count_invoices(db_session, subscription_id: uuid.UUID) -> int:
    async with db_session() as session:
        return (
            await session.scalar(
                select(func.count())
                .select_from(Invoice)
                .where(Invoice.subscription_id == subscription_id)
            )
        ) or 0


class TestWorkerInvoiceIdempotency:
    async def test_worker_same_period_runs_twice_yields_single_invoice(
        self, db_session, monkeypatch
    ):
        """worker 连跑两次同一周期 → 只出一票（周期键 = current_period_end）。"""
        now = datetime.now(UTC).replace(tzinfo=None)
        _, subscription_id = await _seed_subscription(
            db_session,
            current_period_start=now - timedelta(days=30),
            current_period_end=now + timedelta(days=1),
        )
        monkeypatch.setattr(worker_module, "async_session", db_session)

        await generate_invoices()
        await generate_invoices()

        assert await _count_invoices(db_session, subscription_id) == 1
        async with db_session() as session:
            inv = (
                await session.execute(
                    select(Invoice).where(Invoice.subscription_id == subscription_id)
                )
            ).scalar_one()
        assert inv.period_start == now + timedelta(days=1)
        assert inv.status == "draft"
        assert inv.invoice_no.startswith("INV-")

    async def test_worker_multiple_subscriptions_get_distinct_invoice_nos(
        self, db_session, monkeypatch
    ):
        """同批多票发号不重复（替代旧 ts % 10000 撞号路径）。"""
        now = datetime.now(UTC).replace(tzinfo=None)
        _, sub_a = await _seed_subscription(
            db_session,
            current_period_start=now - timedelta(days=30),
            current_period_end=now + timedelta(days=1),
        )
        _, sub_b = await _seed_subscription(
            db_session,
            current_period_start=now - timedelta(days=30),
            current_period_end=now + timedelta(days=2),
        )
        monkeypatch.setattr(worker_module, "async_session", db_session)

        await generate_invoices()

        async with db_session() as session:
            nos = [
                no
                for (no,) in (
                    await session.execute(
                        select(Invoice.invoice_no).where(
                            Invoice.subscription_id.in_([sub_a, sub_b])
                        )
                    )
                ).all()
            ]
        assert len(nos) == 2
        assert len(set(nos)) == 2

    async def test_worker_skips_non_renewing_or_expired_subscriptions(
        self, db_session, monkeypatch
    ):
        """auto_renew=False 或 period_end 已过 → 不出票（回归保护）。"""
        now = datetime.now(UTC).replace(tzinfo=None)
        tenant_id, subscription_id = await _seed_subscription(
            db_session,
            current_period_start=now - timedelta(days=35),
            current_period_end=now - timedelta(days=5),
        )
        async with db_session() as session:
            sub = await session.get(Subscription, subscription_id)
            sub.auto_renew = False
            await session.commit()
        monkeypatch.setattr(worker_module, "async_session", db_session)

        await generate_invoices()

        assert await _count_invoices(db_session, subscription_id) == 0
        assert tenant_id  # 仅避免未使用变量告警


class TestAdminGenerateIdempotency:
    async def test_generate_from_subscription_called_twice_returns_existing(
        self, client, db_session, admin_headers
    ):
        """admin 入口重复调用 → 第二次幂等返回同一张票，非 500、不重复出票。"""
        now = datetime.now(UTC).replace(tzinfo=None)
        _, subscription_id = await _seed_subscription(
            db_session,
            current_period_start=now - timedelta(days=15),
            current_period_end=now + timedelta(days=15),
        )
        url = f"/api/admin/invoices/generate-from-subscription/{subscription_id}"

        first = await client.post(url, headers=admin_headers)
        assert first.status_code == 200, first.text
        assert first.json()["created"] is True

        second = await client.post(url, headers=admin_headers)
        assert second.status_code == 200, second.text
        body = second.json()
        assert body["created"] is False
        assert body["invoice_id"] == first.json()["invoice_id"]

        assert await _count_invoices(db_session, subscription_id) == 1

    async def test_concurrent_generate_yields_exactly_one_invoice(
        self, client, db_session, admin_headers
    ):
        """并发 gather 5 个同周期生成 → 恰好 1 张票，全部请求 200。"""
        now = datetime.now(UTC).replace(tzinfo=None)
        _, subscription_id = await _seed_subscription(
            db_session,
            current_period_start=now - timedelta(days=15),
            current_period_end=now + timedelta(days=15),
        )
        url = f"/api/admin/invoices/generate-from-subscription/{subscription_id}"

        responses = await asyncio.gather(
            *(client.post(url, headers=admin_headers) for _ in range(5))
        )

        for response in responses:
            assert response.status_code == 200, response.text
        invoice_ids = {response.json()["invoice_id"] for response in responses}
        assert len(invoice_ids) == 1
        assert await _count_invoices(db_session, subscription_id) == 1


class TestInvoiceNumbering:
    async def test_sequential_invoice_nos_monotonically_increase(
        self, client, db_session, admin_headers
    ):
        """进程内连续开多票 → 票号单调递增不重复（数据库侧发号，重启安全）。"""
        tenant_id = uuid.uuid4()
        async with db_session() as session:
            session.add(Tenant(id=tenant_id, name="发号测试租户", slug=uuid.uuid4().hex[:12]))
            await session.commit()

        invoice_nos = []
        for i in range(3):
            response = await client.post(
                "/api/admin/invoices",
                headers=admin_headers,
                json={"tenant_id": str(tenant_id), "amount": f"{10 + i}.00"},
            )
            assert response.status_code == 201, response.text
            invoice_nos.append(response.json()["invoice_no"])

        assert len(set(invoice_nos)) == 3
        assert invoice_nos == sorted(invoice_nos)
        # 同月前缀下序号严格递增：INV-YYYYMM-0001 → 0002 → 0003
        suffixes = [int(no.rsplit("-", 1)[1]) for no in invoice_nos]
        assert suffixes == sorted(suffixes)
        assert suffixes[1] == suffixes[0] + 1
        assert suffixes[2] == suffixes[1] + 1

    async def test_manual_invoices_without_subscription_are_unaffected(
        self, client, db_session, admin_headers
    ):
        """手工发票（无订阅/无周期，NULL 幂等键）不受唯一约束影响。"""
        tenant_id = uuid.uuid4()
        async with db_session() as session:
            session.add(
                Tenant(id=tenant_id, name="手工发票租户", slug=uuid.uuid4().hex[:12])
            )
            await session.commit()

        invoice_ids = []
        for _ in range(2):
            response = await client.post(
                "/api/admin/invoices",
                headers=admin_headers,
                json={"tenant_id": str(tenant_id), "amount": "58.00"},
            )
            assert response.status_code == 201, response.text
            invoice_ids.append(response.json()["id"])
        assert len(set(invoice_ids)) == 2

    async def test_manual_create_duplicate_period_returns_409(
        self, client, db_session, admin_headers
    ):
        """手工开票指定已有 (subscription_id, period_start) → 409 冲突而非 500。"""
        now = datetime.now(UTC).replace(tzinfo=None)
        _, subscription_id = await _seed_subscription(
            db_session,
            current_period_start=now - timedelta(days=15),
            current_period_end=now + timedelta(days=15),
        )
        payload = {
            "tenant_id": str(await _invoice_tenant_id(db_session, subscription_id)),
            "subscription_id": str(subscription_id),
            "amount": "99.00",
            "period_start": (now - timedelta(days=15)).isoformat(),
        }

        first = await client.post("/api/admin/invoices", headers=admin_headers, json=payload)
        assert first.status_code == 201, first.text

        second = await client.post("/api/admin/invoices", headers=admin_headers, json=payload)
        assert second.status_code == 409
        assert "已存在发票" in second.json()["detail"]
        assert await _count_invoices(db_session, subscription_id) == 1


async def _invoice_tenant_id(db_session, subscription_id: uuid.UUID) -> uuid.UUID:
    async with db_session() as session:
        sub = await session.get(Subscription, subscription_id)
        assert sub is not None
        return sub.tenant_id
