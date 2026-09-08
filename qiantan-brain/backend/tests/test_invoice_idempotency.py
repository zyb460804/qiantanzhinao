"""发票系统幂等性测试 — 周期唯一约束 + 数据库侧发号（正确性 H6 / 并发 H6+M5）。

覆盖两个发票入口（worker generate_invoices / admin invoices 路由）：
1. 同订阅同周期重复生成（worker 与 admin 两入口）→ 只出一票，重复调用幂等返回；
2. 进程内连续开多票 → invoice_no 单调递增不重复（数据库侧 MAX+1 发号）；
3. 并发 gather 5 个同周期生成 → 恰好 1 张票；
4. 手工发票（无订阅/无周期，NULL 键）不受 (subscription_id, period_start) 唯一约束影响；
5. 手工开票指定已有 (subscription_id, period_start) → 409 而非 500；
6. 跨入口幂等（V1-H1）：两入口键统一为「票覆盖周期（下一计费周期）的起点
   = current_period_end」，worker 出过下期票后 admin 再生成返回同一张票（反之亦然）；
   周期起点漂移（activate 续期）形成的重叠期票由区间重叠守卫拒绝（409 / 跳过）；
7. 发号抗脏数据（V1-M1）：库中 Unicode 数字后缀票号不毒化 MAX+1；
8. yearly 续期票覆盖 365 天（V1-M2），不再固定 +30 天。
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
from app.core.invoicing import next_invoice_no
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
    billing_cycle: str = "monthly",
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
                billing_cycle=billing_cycle,
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


async def _single_invoice(db_session, subscription_id: uuid.UUID) -> Invoice:
    async with db_session() as session:
        return (
            await session.execute(
                select(Invoice).where(Invoice.subscription_id == subscription_id)
            )
        ).scalar_one()


async def _drift_period_end(db_session, subscription_id: uuid.UUID, new_end: datetime) -> None:
    """模拟 activate 续期后周期起点漂移：直接改 current_period_end。"""
    async with db_session() as session:
        sub = await session.get(Subscription, subscription_id)
        assert sub is not None
        sub.current_period_end = new_end
        await session.commit()


class TestCrossEntryIdempotency:
    """V1-H1 跨入口双票修复。

    语义决策：worker 与 admin generate-from-subscription 两入口的幂等键统一
    归一化到「票覆盖周期的起点」。两个入口都开出「下一计费周期」的续费票，
    period_start = current_period_end —— 当期费用已随开通/激活收取（不落
    Invoice 行），admin 原来锚定 current_period_start 的「当期补开」本身就是
    第二次就同一费用出票；且 activate 续期会重置 current_period_start 使键
    漂移、与既有票形成区间重叠但键不等，唯一约束拦不住。
    """

    async def test_worker_then_admin_same_subscription_single_invoice(
        self, client, db_session, admin_headers, monkeypatch
    ):
        """worker 先出下期票 → admin 同订阅再生成 → 幂等返回同一张票，不新增第二张。"""
        now = datetime.now(UTC).replace(tzinfo=None)
        _, subscription_id = await _seed_subscription(
            db_session,
            current_period_start=now - timedelta(days=29),
            current_period_end=now + timedelta(days=1),
        )
        monkeypatch.setattr(worker_module, "async_session", db_session)
        await generate_invoices()

        worker_invoice = await _single_invoice(db_session, subscription_id)
        assert worker_invoice.period_start == now + timedelta(days=1)

        response = await client.post(
            f"/api/admin/invoices/generate-from-subscription/{subscription_id}",
            headers=admin_headers,
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["created"] is False
        assert body["invoice_id"] == str(worker_invoice.id)
        assert await _count_invoices(db_session, subscription_id) == 1

    async def test_admin_then_worker_same_subscription_single_invoice(
        self, client, db_session, admin_headers, monkeypatch
    ):
        """admin 先出下期票 → worker 再跑 → ON CONFLICT 吸收，不新增第二张。"""
        now = datetime.now(UTC).replace(tzinfo=None)
        _, subscription_id = await _seed_subscription(
            db_session,
            current_period_start=now - timedelta(days=15),
            current_period_end=now + timedelta(days=2),
        )
        first = await client.post(
            f"/api/admin/invoices/generate-from-subscription/{subscription_id}",
            headers=admin_headers,
        )
        assert first.status_code == 200, first.text
        assert first.json()["created"] is True
        admin_invoice = await _single_invoice(db_session, subscription_id)
        # admin 出的也是「下一计费周期」票：period_start 锚定 current_period_end
        assert admin_invoice.period_start == now + timedelta(days=2)

        monkeypatch.setattr(worker_module, "async_session", db_session)
        await generate_invoices()

        assert await _count_invoices(db_session, subscription_id) == 1
        assert (await _single_invoice(db_session, subscription_id)).id == admin_invoice.id

    async def test_admin_rejects_overlapping_invoice_after_period_drift(
        self, client, db_session, admin_headers
    ):
        """周期起点漂移（activate 续期重置周期）→ admin 目标区间与既有票重叠 → 409 拒开。"""
        now = datetime.now(UTC).replace(tzinfo=None)
        _, subscription_id = await _seed_subscription(
            db_session,
            current_period_start=now - timedelta(days=29),
            current_period_end=now + timedelta(days=1),
        )
        first = await client.post(
            f"/api/admin/invoices/generate-from-subscription/{subscription_id}",
            headers=admin_headers,
        )
        assert first.status_code == 200, first.text

        # 模拟 activate 续期：period_end 漂移 1 天，新目标区间与既有票重叠
        await _drift_period_end(db_session, subscription_id, now + timedelta(days=2))
        second = await client.post(
            f"/api/admin/invoices/generate-from-subscription/{subscription_id}",
            headers=admin_headers,
        )
        assert second.status_code == 409, second.text
        assert "重叠" in second.json()["detail"]
        assert await _count_invoices(db_session, subscription_id) == 1

    async def test_worker_skips_overlapping_invoice_after_period_drift(
        self, db_session, monkeypatch
    ):
        """周期起点漂移后 worker 目标区间与既有票重叠 → 跳过不出票。"""
        now = datetime.now(UTC).replace(tzinfo=None)
        _, subscription_id = await _seed_subscription(
            db_session,
            current_period_start=now - timedelta(days=29),
            current_period_end=now + timedelta(days=1),
        )
        monkeypatch.setattr(worker_module, "async_session", db_session)
        await generate_invoices()
        assert await _count_invoices(db_session, subscription_id) == 1

        await _drift_period_end(db_session, subscription_id, now + timedelta(days=2))
        await generate_invoices()
        # 漂移后目标 [now+2d, now+32d) 与既有票 [now+1d, now+31d) 重叠 → 不出第二张
        assert await _count_invoices(db_session, subscription_id) == 1

    async def test_admin_requires_period_end(self, client, db_session, admin_headers):
        """订阅缺少 current_period_end → 400（无法确定续期周期），而非错键出票。"""
        now = datetime.now(UTC).replace(tzinfo=None)
        _, subscription_id = await _seed_subscription(
            db_session,
            current_period_start=now - timedelta(days=15),
            current_period_end=None,
        )
        response = await client.post(
            f"/api/admin/invoices/generate-from-subscription/{subscription_id}",
            headers=admin_headers,
        )
        assert response.status_code == 400, response.text
        assert "计费周期" in response.json()["detail"]

    async def test_worker_yearly_renewal_covers_365_days(self, db_session, monkeypatch):
        """yearly 续期票 period_end = period_start + 365d（V1-M2，原固定 +30d）。"""
        now = datetime.now(UTC).replace(tzinfo=None)
        _, subscription_id = await _seed_subscription(
            db_session,
            current_period_start=now - timedelta(days=360),
            current_period_end=now + timedelta(days=1),
            billing_cycle="yearly",
        )
        monkeypatch.setattr(worker_module, "async_session", db_session)
        await generate_invoices()

        invoice = await _single_invoice(db_session, subscription_id)
        assert invoice.period_end == invoice.period_start + timedelta(days=365)
        assert invoice.amount == Decimal("990.00")


class TestInvoiceNumberingRobustness:
    async def test_unicode_digit_suffix_does_not_poison_numbering(self, db_session):
        """库中存在 Unicode 数字后缀脏票号 → 发号正常（V1-M1）。

        '²'.isdigit() 为 True 但 int('²') 抛 ValueError —— 一行脏数据不得
        毒化 MAX+1 发号。
        """
        assert "²".isdigit() and not "²".isascii()  # 锁定陷阱本身
        tenant_id = uuid.uuid4()
        async with db_session() as session:
            session.add(Tenant(id=tenant_id, name="脏票号租户", slug=uuid.uuid4().hex[:12]))
            session.add_all(
                [
                    Invoice(
                        tenant_id=tenant_id,
                        invoice_no="INV-202608-0007",
                        amount=Decimal("1.00"),
                        currency="CNY",
                        status="draft",
                    ),
                    Invoice(
                        tenant_id=tenant_id,
                        invoice_no="INV-202608-0042²",
                        amount=Decimal("1.00"),
                        currency="CNY",
                        status="draft",
                    ),
                ]
            )
            await session.commit()
            # 脏行 "0042²" 被跳过，MAX 取 7 → 下一号 0008
            assert await next_invoice_no(session, now=datetime(2026, 8, 15)) == "INV-202608-0008"
