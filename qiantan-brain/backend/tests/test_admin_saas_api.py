"""SaaS 管理面核心 API 集成测试 — tenants / subscriptions / plans / usage / admins / audit-logs。

覆盖范围（认证 401 与权限矩阵 403 见 tests/test_admin_permissions.py）：
- 六个此前零测试路由文件的核心端点 happy path 与业务负例（404/400/409/422）
- 计费面状态流转：创建(active) → 取消(canceled 终态) → 重新订阅的合法路径；
  trialing → activate 转正式
- 用量查询的租户隔离：A/B 租户用量互不串账
- 管理员账号管理：角色白名单、密码策略（app/core/password_policy.py）生效
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
import pytest_asyncio
from sqlalchemy import select

from app.core.admin_security import create_admin_token, hash_password
from app.models.device import Device
from app.models.merchant import Merchant
from app.models.saas import Plan, PlatformAdmin, Subscription, Tenant, UsageRecord


STRONG_PASSWORD = "Str0ng!Pass"


def _naive_now() -> datetime:
    """DB 时间列为 naive datetime，种子数据统一用 naive UTC。"""
    return datetime.now(UTC).replace(tzinfo=None)


def _today() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%d")


@pytest_asyncio.fixture
async def super_admin_headers(db_session):
    """super_admin Bearer 头 — 走真实 get_current_admin 链路（仅 DB 被覆盖）。"""
    admin_id = uuid.uuid4()
    async with db_session() as session:
        session.add(
            PlatformAdmin(
                id=admin_id,
                email="saas-super@example.com",
                password_hash="not-used-in-token-tests",
                name="平台超管",
                role="super_admin",
                is_active=True,
            )
        )
        await session.commit()
    token = create_admin_token(admin_id, role="super_admin")
    return {"Authorization": f"Bearer {token}"}


async def seed_plan(db_session, **overrides) -> uuid.UUID:
    defaults = dict(
        code="saas-test-plan",
        name="SaaS 测试套餐",
        price_monthly=Decimal("99.00"),
        price_yearly=Decimal("990.00"),
        max_merchants=2,
        max_api_calls_monthly=100,
        max_storage_mb=50,
        is_active=True,
    )
    defaults.update(overrides)
    plan_id = uuid.uuid4()
    async with db_session() as session:
        session.add(Plan(id=plan_id, **defaults))
        await session.commit()
    return plan_id


async def seed_tenant(db_session, **overrides) -> uuid.UUID:
    defaults = dict(name="SaaS 测试租户", slug="saas-test-tenant", status="trial", plan_id=None)
    defaults.update(overrides)
    tenant_id = uuid.uuid4()
    async with db_session() as session:
        session.add(Tenant(id=tenant_id, **defaults))
        await session.commit()
    return tenant_id


async def seed_subscription(db_session, tenant_id, plan_id, **overrides) -> uuid.UUID:
    now = _naive_now()
    defaults = dict(
        billing_cycle="monthly",
        status="trialing",
        current_period_start=now,
        current_period_end=now + timedelta(days=30),
        auto_renew=True,
    )
    defaults.update(overrides)
    sub_id = uuid.uuid4()
    async with db_session() as session:
        session.add(Subscription(id=sub_id, tenant_id=tenant_id, plan_id=plan_id, **defaults))
        await session.commit()
    return sub_id


def tenant_create_payload(plan_id, **overrides) -> dict:
    payload = dict(
        name="城南农贸市场",
        slug="chengnan-market",
        plan_id=str(plan_id),
        contact_email="owner@example.com",
        merchant_name="张三蔬菜摊",
        trial_days=14,
    )
    payload.update(overrides)
    return payload


# ══════════════════════════════ tenants ══════════════════════════════


class TestAdminTenants:
    async def test_onboard_tenant_creates_subscription_merchant_and_usage(
        self, client, db_session, super_admin_headers
    ):
        plan_id = await seed_plan(db_session, code="onboard-plan")
        resp = await client.post(
            "/api/admin/tenants", headers=super_admin_headers, json=tenant_create_payload(plan_id)
        )
        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert body["status"] == "trial"
        assert body["plan_code"] == "onboard-plan"
        assert body["subscription_status"] == "trialing"
        assert body["trial_ends_at"] is not None

        tenant_id = uuid.UUID(body["tenant_id"])
        sub_id = uuid.UUID(body["subscription_id"])
        merchant_id = uuid.UUID(body["merchant_id"])
        async with db_session() as session:
            tenant = await session.get(Tenant, tenant_id)
            assert tenant is not None
            assert tenant.plan_id == plan_id
            assert tenant.status == "trial"
            sub = await session.get(Subscription, sub_id)
            assert sub is not None
            assert sub.status == "trialing"
            assert sub.auto_renew is True
            merchant = await session.get(Merchant, merchant_id)
            assert merchant is not None
            assert merchant.tenant_id == tenant_id
            rows = (
                await session.execute(
                    select(UsageRecord).where(UsageRecord.tenant_id == tenant_id)
                )
            ).scalars()
            assert {row.metric: row.value for row in rows} == {
                "api_calls": 0,
                "storage_mb": 0,
                "merchant_count": 1,
            }

    async def test_onboard_rejects_duplicate_slug_case_insensitive(
        self, client, db_session, super_admin_headers
    ):
        plan_id = await seed_plan(db_session, code="dup-slug-plan")
        first = await client.post(
            "/api/admin/tenants",
            headers=super_admin_headers,
            json=tenant_create_payload(plan_id, slug="dup-market"),
        )
        assert first.status_code == 201
        # slug 校验器会归一化为小写，大小写变体同样命中唯一性检查
        second = await client.post(
            "/api/admin/tenants",
            headers=super_admin_headers,
            json=tenant_create_payload(plan_id, slug="DUP-Market", name="重名市场"),
        )
        assert second.status_code == 409
        assert "已存在" in second.json()["detail"]
    async def test_onboard_missing_plan_404_and_inactive_plan_400(
        self, client, db_session, super_admin_headers
    ):
        missing = await client.post(
            "/api/admin/tenants",
            headers=super_admin_headers,
            json=tenant_create_payload(uuid.uuid4(), slug="no-plan"),
        )
        assert missing.status_code == 404
        assert "套餐不存在" in missing.json()["detail"]

        plan_id = await seed_plan(db_session, code="inactive-plan", is_active=False)
        inactive = await client.post(
            "/api/admin/tenants",
            headers=super_admin_headers,
            json=tenant_create_payload(plan_id, slug="inactive-plan-tenant"),
        )
        assert inactive.status_code == 400
        assert "已停用" in inactive.json()["detail"]

    async def test_list_tenants_pagination_search_and_status_filter(
        self, client, db_session, super_admin_headers
    ):
        plan_id = await seed_plan(db_session, code="list-plan")
        await seed_tenant(
            db_session, name="alpha 市场", slug="alpha", status="active", plan_id=plan_id
        )
        await seed_tenant(
            db_session, name="beta 市场", slug="beta", status="active", plan_id=plan_id
        )
        await seed_tenant(
            db_session, name="gamma 试用", slug="gamma", status="trial", plan_id=plan_id
        )

        page = await client.get(
            "/api/admin/tenants?page=1&page_size=2", headers=super_admin_headers
        )
        assert page.status_code == 200
        assert page.json()["total"] == 3
        assert len(page.json()["items"]) == 2
        assert page.json()["page_size"] == 2

        searched = await client.get("/api/admin/tenants?search=beta", headers=super_admin_headers)
        assert searched.json()["total"] == 1
        assert searched.json()["items"][0]["slug"] == "beta"
        assert searched.json()["items"][0]["plan_code"] == "list-plan"

        filtered = await client.get("/api/admin/tenants?status=active", headers=super_admin_headers)
        assert filtered.json()["total"] == 2
        assert {item["status"] for item in filtered.json()["items"]} == {"active"}

    async def test_get_tenant_detail_echoes_subscription_status(
        self, client, db_session, super_admin_headers
    ):
        plan_id = await seed_plan(db_session, code="detail-plan")
        tenant_id = await seed_tenant(db_session, slug="detail-tenant", plan_id=plan_id)
        await seed_subscription(db_session, tenant_id, plan_id, status="trialing")

        detail = await client.get(f"/api/admin/tenants/{tenant_id}", headers=super_admin_headers)
        assert detail.status_code == 200, detail.text
        body = detail.json()
        assert body["id"] == str(tenant_id)
        assert body["plan_code"] == "detail-plan"
        assert body["subscription_status"] == "trialing"
        assert body["merchant_count"] == 0

        unknown = await client.get(
            f"/api/admin/tenants/{uuid.uuid4()}", headers=super_admin_headers
        )
        assert unknown.status_code == 404

    async def test_update_tenant_fields_plan_and_invalid_status(
        self, client, db_session, super_admin_headers
    ):
        plan_id = await seed_plan(db_session, code="update-plan")
        other_plan = await seed_plan(db_session, code="update-plan-2")
        tenant_id = await seed_tenant(db_session, slug="update-tenant", plan_id=plan_id)

        updated = await client.put(
            f"/api/admin/tenants/{tenant_id}",
            headers=super_admin_headers,
            json={
                "name": "改名后的市场",
                "contact_email": "new@example.com",
                "admin_notes": "大客户",
            },
        )
        assert updated.status_code == 200, updated.text
        body = updated.json()
        assert body["name"] == "改名后的市场"
        assert body["contact_email"] == "new@example.com"
        assert body["admin_notes"] == "大客户"

        plan_change = await client.put(
            f"/api/admin/tenants/{tenant_id}",
            headers=super_admin_headers,
            json={"plan_id": str(other_plan)},
        )
        assert plan_change.status_code == 200
        assert plan_change.json()["plan_code"] == "update-plan-2"

        bad_status = await client.put(
            f"/api/admin/tenants/{tenant_id}",
            headers=super_admin_headers,
            json={"status": "bankrupt"},
        )
        assert bad_status.status_code == 400
        unknown_plan = await client.put(
            f"/api/admin/tenants/{tenant_id}",
            headers=super_admin_headers,
            json={"plan_id": str(uuid.uuid4())},
        )
        assert unknown_plan.status_code == 400
        unknown_tenant = await client.put(
            f"/api/admin/tenants/{uuid.uuid4()}",
            headers=super_admin_headers,
            json={"name": "幽灵租户"},
        )
        assert unknown_tenant.status_code == 404

    async def test_tenant_suspend_resume_and_same_status_409(
        self, client, db_session, super_admin_headers
    ):
        tenant_id = await seed_tenant(db_session, slug="suspend-flow", status="active")
        suspend = await client.put(
            f"/api/admin/tenants/{tenant_id}",
            headers=super_admin_headers,
            json={"status": "suspended"},
        )
        assert suspend.status_code == 200
        assert suspend.json()["status"] == "suspended"

        resume = await client.put(
            f"/api/admin/tenants/{tenant_id}",
            headers=super_admin_headers,
            json={"status": "active"},
        )
        assert resume.status_code == 200
        assert resume.json()["status"] == "active"

        # 状态机拒绝同态流转（active → active）
        same = await client.put(
            f"/api/admin/tenants/{tenant_id}",
            headers=super_admin_headers,
            json={"status": "active"},
        )
        assert same.status_code == 409

    async def test_tenant_devices_and_ai_usage(self, client, db_session, super_admin_headers):
        tenant_id = await seed_tenant(db_session, slug="device-tenant", status="active")
        async with db_session() as session:
            merchant = Merchant(name="设备测试摊主", business_type="蔬菜", tenant_id=tenant_id)
            session.add(merchant)
            await session.flush()
            session.add_all(
                [
                    Device(
                        merchant_id=merchant.id,
                        device_type="scale",
                        device_name="公平秤01",
                        serial_number="DEV-ONLINE-01",
                        last_heartbeat=_naive_now(),
                    ),
                    Device(
                        merchant_id=merchant.id,
                        device_type="camera",
                        device_name="离线摄像头",
                        serial_number="DEV-STALE-02",
                        last_heartbeat=_naive_now() - timedelta(hours=2),
                    ),
                ]
            )
            await session.commit()

        devices = await client.get(
            f"/api/admin/tenants/{tenant_id}/devices", headers=super_admin_headers
        )
        assert devices.status_code == 200, devices.text
        body = devices.json()
        assert body["total"] == 2
        by_name = {item["device_name"]: item for item in body["items"]}
        assert by_name["公平秤01"]["online_status"] is True
        assert by_name["公平秤01"]["merchant_name"] == "设备测试摊主"
        assert by_name["公平秤01"]["sync_count"] == 0
        assert by_name["离线摄像头"]["online_status"] is False

        ai_usage = await client.get(
            f"/api/admin/tenants/{tenant_id}/ai-usage", headers=super_admin_headers
        )
        assert ai_usage.status_code == 200
        usage = ai_usage.json()
        assert len(usage["dates"]) == 30
        assert usage["vision_counts"] == [0] * 30
        assert usage["voice_counts"] == [0] * 30
        assert usage["advice_counts"] == [0] * 30

        unknown = await client.get(
            f"/api/admin/tenants/{uuid.uuid4()}/devices", headers=super_admin_headers
        )
        assert unknown.status_code == 404

        unknown_ai = await client.get(
            f"/api/admin/tenants/{uuid.uuid4()}/ai-usage", headers=super_admin_headers
        )
        assert unknown_ai.status_code == 404

    async def test_tenant_risk_audit_counts(self, client, db_session, super_admin_headers):
        plan_id = await seed_plan(db_session, code="risk-plan")
        onboard = await client.post(
            "/api/admin/tenants",
            headers=super_admin_headers,
            json=tenant_create_payload(plan_id, slug="risk-audit-tenant"),
        )
        assert onboard.status_code == 201
        tenant_id = onboard.json()["tenant_id"]

        risk = await client.get(
            f"/api/admin/tenants/{tenant_id}/risk-audit", headers=super_admin_headers
        )
        assert risk.status_code == 200, risk.text
        body = risk.json()
        assert body["merchant_count"] == 1
        # 接入操作本身会写一条 resource_type=tenant 的管理员审计日志
        assert body["total_audit_events_last_30d"] >= 1
        assert body["abnormal_patterns"] == []

        unknown_risk = await client.get(
            f"/api/admin/tenants/{uuid.uuid4()}/risk-audit", headers=super_admin_headers
        )
        assert unknown_risk.status_code == 404


# ═══════════════════════════════ plans ═══════════════════════════════


class TestAdminPlans:
    async def test_plan_crud_lifecycle(self, client, super_admin_headers):
        created = await client.post(
            "/api/admin/plans",
            headers=super_admin_headers,
            json={
                "code": "crud-plan",
                "name": "CRUD 测试套餐",
                "price_monthly": "59.00",
                "price_yearly": "590.00",
                "max_merchants": 3,
                "max_api_calls_monthly": 2000,
                "max_storage_mb": 500,
                "sort_order": 10,
            },
        )
        assert created.status_code == 201, created.text
        plan = created.json()
        assert plan["code"] == "crud-plan"
        assert plan["is_active"] is True
        assert plan["is_public"] is True

        listing = await client.get("/api/admin/plans", headers=super_admin_headers)
        assert listing.status_code == 200
        assert "crud-plan" in {p["code"] for p in listing.json()}

        updated = await client.put(
            f"/api/admin/plans/{plan['id']}",
            headers=super_admin_headers,
            json={"name": "CRUD 测试套餐 V2", "price_monthly": "69.00", "is_public": False},
        )
        assert updated.status_code == 200
        assert updated.json()["name"] == "CRUD 测试套餐 V2"
        assert updated.json()["is_public"] is False
        assert updated.json()["price_monthly"] == "69.00"

        deleted = await client.delete(f"/api/admin/plans/{plan['id']}", headers=super_admin_headers)
        assert deleted.status_code == 204
        # DELETE 是软删除：列表中仍在，但 is_active=False
        after_delete = await client.get("/api/admin/plans", headers=super_admin_headers)
        target = next(p for p in after_delete.json() if p["code"] == "crud-plan")
        assert target["is_active"] is False

    async def test_create_plan_duplicate_code_400(self, client, super_admin_headers):
        payload = {"code": "dup-plan", "name": "重复套餐"}
        first = await client.post("/api/admin/plans", headers=super_admin_headers, json=payload)
        assert first.status_code == 201
        second = await client.post(
            "/api/admin/plans", headers=super_admin_headers, json={**payload, "name": "又一个"}
        )
        assert second.status_code == 400
        assert "已存在" in second.json()["detail"]

    async def test_plan_update_delete_negative_paths(self, client, super_admin_headers):
        bad_uuid = await client.put(
            "/api/admin/plans/not-a-uuid",
            headers=super_admin_headers,
            json={"name": "x"},
        )
        assert bad_uuid.status_code == 400
        unknown = await client.put(
            f"/api/admin/plans/{uuid.uuid4()}",
            headers=super_admin_headers,
            json={"name": "x"},
        )
        assert unknown.status_code == 404
        delete_unknown = await client.delete(
            f"/api/admin/plans/{uuid.uuid4()}", headers=super_admin_headers
        )
        assert delete_unknown.status_code == 404
        delete_bad = await client.delete(
            "/api/admin/plans/still-not-a-uuid", headers=super_admin_headers
        )
        assert delete_bad.status_code == 400


# ══════════════════════════ subscriptions ══════════════════════════


class TestAdminSubscriptions:
    async def test_billing_flow_create_cancel_then_resubscribe(
        self, client, db_session, super_admin_headers
    ):
        plan_basic = await seed_plan(db_session, code="billing-basic")
        plan_pro = await seed_plan(db_session, code="billing-pro")
        tenant_id = await seed_tenant(db_session, slug="billing-flow", status="trial")

        create = await client.post(
            "/api/admin/subscriptions",
            headers=super_admin_headers,
            json={
                "tenant_id": str(tenant_id),
                "plan_id": str(plan_basic),
                "billing_cycle": "monthly",
            },
        )
        assert create.status_code == 201, create.text
        created = create.json()
        assert created["status"] == "active"
        assert created["plan_code"] == "billing-basic"
        assert created["tenant_name"] == "SaaS 测试租户"
        assert created["canceled_at"] is None

        # trial 租户开通正式订阅后自动转正，且套餐落回租户
        async with db_session() as session:
            tenant = await session.get(Tenant, tenant_id)
            assert tenant is not None
            assert tenant.status == "active"
            assert tenant.plan_id == plan_basic

        # 已有有效订阅时不可重复创建
        duplicate = await client.post(
            "/api/admin/subscriptions",
            headers=super_admin_headers,
            json={"tenant_id": str(tenant_id), "plan_id": str(plan_basic)},
        )
        assert duplicate.status_code == 409

        sub_id = created["id"]
        cancel = await client.post(
            f"/api/admin/subscriptions/{sub_id}/cancel",
            headers=super_admin_headers,
            json={"reason": "客户要求退款"},
        )
        assert cancel.status_code == 200, cancel.text
        assert cancel.json()["message"] == "订阅已取消"
        assert cancel.json()["current_period_end"] is not None

        async with db_session() as session:
            sub = await session.get(Subscription, uuid.UUID(sub_id))
            assert sub is not None
            assert sub.status == "canceled"
            assert sub.canceled_at is not None
            assert sub.auto_renew is False

        # canceled 是状态机终态：直接 activate 被拒绝
        reactivate = await client.post(
            f"/api/admin/subscriptions/{sub_id}/activate", headers=super_admin_headers
        )
        assert reactivate.status_code == 409

        # 合法的"取消后重新激活"路径：为同一租户新建订阅（partial unique index 允许）
        resubscribe = await client.post(
            "/api/admin/subscriptions",
            headers=super_admin_headers,
            json={
                "tenant_id": str(tenant_id),
                "plan_id": str(plan_pro),
                "billing_cycle": "yearly",
            },
        )
        assert resubscribe.status_code == 201, resubscribe.text
        assert resubscribe.json()["status"] == "active"
        assert resubscribe.json()["billing_cycle"] == "yearly"
        assert resubscribe.json()["plan_code"] == "billing-pro"

        async with db_session() as session:
            tenant = await session.get(Tenant, tenant_id)
            assert tenant is not None
            assert tenant.plan_id == plan_pro

    async def test_activate_trialing_subscription_and_conflict_409(
        self, client, db_session, super_admin_headers
    ):
        plan_id = await seed_plan(db_session, code="activate-plan")
        tenant_id = await seed_tenant(
            db_session, slug="activate-tenant", status="trial", plan_id=plan_id
        )
        sub_id = await seed_subscription(db_session, tenant_id, plan_id, status="trialing")
        # suspended 不在 partial unique index 内，可与 active 并存
        conflicting_id = await seed_subscription(
            db_session, tenant_id, plan_id, status="suspended"
        )

        activate = await client.post(
            f"/api/admin/subscriptions/{sub_id}/activate", headers=super_admin_headers
        )
        assert activate.status_code == 200, activate.text
        assert activate.json() == {"message": "订阅已激活", "status": "active"}

        async with db_session() as session:
            sub = await session.get(Subscription, sub_id)
            assert sub is not None
            assert sub.status == "active"
            assert sub.canceled_at is None
            # 月付激活 → 新周期约 30 天
            delta = sub.current_period_end - _naive_now()
            assert timedelta(days=29) < delta < timedelta(days=31)
            tenant = await session.get(Tenant, tenant_id)
            assert tenant is not None
            assert tenant.status == "active"

        # 同租户已有一条 active 订阅时，激活另一条 → 409
        conflict = await client.post(
            f"/api/admin/subscriptions/{conflicting_id}/activate", headers=super_admin_headers
        )
        assert conflict.status_code == 409
        assert "已有活跃订阅" in conflict.json()["detail"]

    async def test_update_subscription_plan_cycle_and_negatives(
        self, client, db_session, super_admin_headers
    ):
        plan_basic = await seed_plan(db_session, code="upd-sub-basic")
        plan_pro = await seed_plan(db_session, code="upd-sub-pro")
        tenant_id = await seed_tenant(db_session, slug="upd-sub-tenant", plan_id=plan_basic)
        sub_id = await seed_subscription(db_session, tenant_id, plan_basic, status="active")

        updated = await client.put(
            f"/api/admin/subscriptions/{sub_id}",
            headers=super_admin_headers,
            json={"plan_id": str(plan_pro), "billing_cycle": "yearly", "auto_renew": False},
        )
        assert updated.status_code == 200, updated.text
        body = updated.json()
        assert body["plan_code"] == "upd-sub-pro"
        assert body["billing_cycle"] == "yearly"
        assert body["auto_renew"] is False

        async with db_session() as session:
            sub = await session.get(Subscription, sub_id)
            assert sub is not None
            assert sub.previous_plan_id == plan_basic
            tenant = await session.get(Tenant, tenant_id)
            assert tenant is not None
            assert tenant.plan_id == plan_pro

        bad_cycle = await client.put(
            f"/api/admin/subscriptions/{sub_id}",
            headers=super_admin_headers,
            json={"billing_cycle": "weekly"},
        )
        assert bad_cycle.status_code == 400
        unknown_plan = await client.put(
            f"/api/admin/subscriptions/{sub_id}",
            headers=super_admin_headers,
            json={"plan_id": str(uuid.uuid4())},
        )
        assert unknown_plan.status_code == 404
        unknown_sub = await client.put(
            f"/api/admin/subscriptions/{uuid.uuid4()}",
            headers=super_admin_headers,
            json={"auto_renew": True},
        )
        assert unknown_sub.status_code == 404

    async def test_create_subscription_negative_paths(
        self, client, db_session, super_admin_headers
    ):
        plan_id = await seed_plan(db_session, code="neg-create-plan")
        tenant_id = await seed_tenant(db_session, slug="neg-create-tenant")
        inactive_plan = await seed_plan(db_session, code="neg-inactive-plan", is_active=False)

        unknown_tenant = await client.post(
            "/api/admin/subscriptions",
            headers=super_admin_headers,
            json={"tenant_id": str(uuid.uuid4()), "plan_id": str(plan_id)},
        )
        assert unknown_tenant.status_code == 404
        unknown_plan = await client.post(
            "/api/admin/subscriptions",
            headers=super_admin_headers,
            json={"tenant_id": str(tenant_id), "plan_id": str(uuid.uuid4())},
        )
        assert unknown_plan.status_code == 404
        inactive = await client.post(
            "/api/admin/subscriptions",
            headers=super_admin_headers,
            json={"tenant_id": str(tenant_id), "plan_id": str(inactive_plan)},
        )
        assert inactive.status_code == 400
        bad_cycle = await client.post(
            "/api/admin/subscriptions",
            headers=super_admin_headers,
            json={"tenant_id": str(tenant_id), "plan_id": str(plan_id), "billing_cycle": "daily"},
        )
        assert bad_cycle.status_code == 400
        cancel_unknown = await client.post(
            f"/api/admin/subscriptions/{uuid.uuid4()}/cancel", headers=super_admin_headers
        )
        assert cancel_unknown.status_code == 404

    async def test_list_and_detail_subscriptions_with_filters(
        self, client, db_session, super_admin_headers
    ):
        plan_id = await seed_plan(db_session, code="list-sub-plan")
        tenant_a = await seed_tenant(db_session, name="A 市场", slug="list-sub-a", plan_id=plan_id)
        tenant_b = await seed_tenant(db_session, name="B 市场", slug="list-sub-b", plan_id=plan_id)
        sub_active = await seed_subscription(db_session, tenant_a, plan_id, status="active")
        await seed_subscription(db_session, tenant_b, plan_id, status="canceled")

        listing = await client.get("/api/admin/subscriptions", headers=super_admin_headers)
        assert listing.status_code == 200
        assert listing.json()["total"] == 2
        assert {item["status"] for item in listing.json()["items"]} == {"active", "canceled"}

        filtered = await client.get(
            "/api/admin/subscriptions?status=canceled", headers=super_admin_headers
        )
        assert filtered.json()["total"] == 1
        assert filtered.json()["items"][0]["status"] == "canceled"

        by_tenant = await client.get(
            f"/api/admin/subscriptions?tenant_id={tenant_a}", headers=super_admin_headers
        )
        assert by_tenant.json()["total"] == 1
        assert by_tenant.json()["items"][0]["tenant_name"] == "A 市场"

        detail = await client.get(
            f"/api/admin/subscriptions/{sub_active}", headers=super_admin_headers
        )
        assert detail.status_code == 200
        assert detail.json()["plan_code"] == "list-sub-plan"
        assert detail.json()["tenant_name"] == "A 市场"

        unknown = await client.get(
            f"/api/admin/subscriptions/{uuid.uuid4()}", headers=super_admin_headers
        )
        assert unknown.status_code == 404



# ═══════════════════════════════ usage ═══════════════════════════════


class TestAdminUsage:
    async def test_usage_tenant_isolation(self, client, db_session, super_admin_headers):
        plan_roomy = await seed_plan(db_session, code="usage-roomy", max_api_calls_monthly=100)
        plan_tiny = await seed_plan(db_session, code="usage-tiny", max_api_calls_monthly=5)
        tenant_a = await seed_tenant(
            db_session, slug="usage-a", status="active", plan_id=plan_roomy
        )
        tenant_b = await seed_tenant(db_session, slug="usage-b", status="active", plan_id=plan_tiny)
        async with db_session() as session:
            session.add_all(
                [
                    UsageRecord(
                        tenant_id=tenant_a, metric="api_calls", recorded_date=_today(), value=40
                    ),
                    UsageRecord(
                        tenant_id=tenant_b, metric="api_calls", recorded_date=_today(), value=40
                    ),
                ]
            )
            await session.commit()

        current_a = await client.get(
            f"/api/admin/usage/{tenant_a}/current/api_calls", headers=super_admin_headers
        )
        assert current_a.status_code == 200, current_a.text
        # 隔离核心断言：A 的统计只含 A 自己的 40（串账会变成 80）
        assert current_a.json() == {
            "metric": "api_calls",
            "current": 40,
            "limit": 100,
            "remaining": 60,
            "exceeded": False,
        }

        current_b = await client.get(
            f"/api/admin/usage/{tenant_b}/current/api_calls", headers=super_admin_headers
        )
        assert current_b.json()["current"] == 40
        assert current_b.json()["limit"] == 5
        assert current_b.json()["remaining"] == 0
        assert current_b.json()["exceeded"] is True

        quotas_a = await client.get(
            f"/api/admin/usage/{tenant_a}/quotas", headers=super_admin_headers
        )
        assert quotas_a.status_code == 200
        metrics = {q["metric"]: q for q in quotas_a.json()}
        assert set(metrics) == {"api_calls", "storage_mb", "merchant_count"}
        assert metrics["api_calls"]["current"] == 40
        assert metrics["api_calls"]["limit"] == 100

        overview_a = await client.get(
            f"/api/admin/usage/{tenant_a}/overview", headers=super_admin_headers
        )
        assert overview_a.status_code == 200
        assert overview_a.json()["tenant_id"] == str(tenant_a)
        assert overview_a.json()["tenant_name"] == "SaaS 测试租户"
        assert overview_a.json()["plan_code"] == "usage-roomy"
        assert len(overview_a.json()["quotas"]) == 3

    async def test_manual_record_usage_accumulates_and_validates(
        self, client, db_session, super_admin_headers
    ):
        plan_id = await seed_plan(db_session, code="record-plan", max_api_calls_monthly=100)
        tenant_id = await seed_tenant(db_session, slug="record-tenant", plan_id=plan_id)
        async with db_session() as session:
            session.add(
                UsageRecord(
                    tenant_id=tenant_id, metric="api_calls", recorded_date=_today(), value=10
                )
            )
            await session.commit()

        record = await client.post(
            f"/api/admin/usage/{tenant_id}/record",
            headers=super_admin_headers,
            json={"metric": "api_calls", "value": 5},
        )
        assert record.status_code == 200, record.text
        assert record.json() == {"message": "用量已记录", "metric": "api_calls", "added": 5}

        current = await client.get(
            f"/api/admin/usage/{tenant_id}/current/api_calls", headers=super_admin_headers
        )
        assert current.json()["current"] == 15

        # 超过配额 → 响应带 warning
        exceed = await client.post(
            f"/api/admin/usage/{tenant_id}/record",
            headers=super_admin_headers,
            json={"metric": "api_calls", "value": 95},
        )
        assert exceed.status_code == 200
        assert "warning" in exceed.json()

        invalid_metric = await client.post(
            f"/api/admin/usage/{tenant_id}/record",
            headers=super_admin_headers,
            json={"metric": "telemetry_pings", "value": 1},
        )
        assert invalid_metric.status_code == 400
        assert "指标无效" in invalid_metric.json()["detail"]

    async def test_usage_trend_and_unknown_tenant_404(
        self, client, db_session, super_admin_headers
    ):
        plan_id = await seed_plan(db_session, code="trend-plan")
        tenant_id = await seed_tenant(db_session, slug="trend-tenant", plan_id=plan_id)
        yesterday = (datetime.now(UTC) - timedelta(days=1)).strftime("%Y-%m-%d")
        async with db_session() as session:
            session.add_all(
                [
                    UsageRecord(
                        tenant_id=tenant_id, metric="api_calls", recorded_date=_today(), value=40
                    ),
                    UsageRecord(
                        tenant_id=tenant_id, metric="api_calls", recorded_date=yesterday, value=10
                    ),
                ]
            )
            await session.commit()

        trend = await client.get(
            f"/api/admin/usage/{tenant_id}/trend/api_calls?days=7", headers=super_admin_headers
        )
        assert trend.status_code == 200, trend.text
        entries = trend.json()
        by_date = {entry["date"]: entry["value"] for entry in entries}
        assert by_date[_today()] == 40
        assert by_date[yesterday] == 10
        dates = [entry["date"] for entry in entries]
        assert dates == sorted(dates)

        unknown = uuid.uuid4()
        for path in (
            f"/api/admin/usage/{unknown}/quotas",
            f"/api/admin/usage/{unknown}/current/api_calls",
            f"/api/admin/usage/{unknown}/trend/api_calls",
            f"/api/admin/usage/{unknown}/overview",
        ):
            resp = await client.get(path, headers=super_admin_headers)
            assert resp.status_code == 404, path
        record_unknown = await client.post(
            f"/api/admin/usage/{unknown}/record",
            headers=super_admin_headers,
            json={"metric": "api_calls"},
        )
        assert record_unknown.status_code == 404



# ═══════════════════════════════ admins ═══════════════════════════════


class TestAdminAdmins:
    async def test_create_list_and_login_with_created_credentials(
        self, client, auth_client, super_admin_headers
    ):
        created = await client.post(
            "/api/admin/admins",
            headers=super_admin_headers,
            json={
                "email": "new-ops@example.com",
                "password": STRONG_PASSWORD,
                "name": "运营小蒋",
                "role": "ops_admin",
            },
        )
        assert created.status_code == 201, created.text
        body = created.json()
        assert body["email"] == "new-ops@example.com"
        assert body["role"] == "ops_admin"
        assert body["is_active"] is True

        listing = await client.get("/api/admin/admins", headers=super_admin_headers)
        assert listing.status_code == 200
        assert any(admin["email"] == "new-ops@example.com" for admin in listing.json())

        # 新账号可真实登录（bcrypt 哈希端到端），权限来自服务端 RBAC 表
        login = await auth_client.post(
            "/api/admin/login",
            json={"email": "new-ops@example.com", "password": STRONG_PASSWORD},
        )
        assert login.status_code == 200
        admin_info = login.json()["admin"]
        assert admin_info["role"] == "ops_admin"
        assert "tenant.read" in admin_info["permissions"]
        assert "admin.manage" not in admin_info["permissions"]

    async def test_create_admin_role_whitelist_and_duplicate_email(
        self, client, super_admin_headers
    ):
        bad_role = await client.post(
            "/api/admin/admins",
            headers=super_admin_headers,
            json={
                "email": "hacker@example.com",
                "password": STRONG_PASSWORD,
                "name": "黑客",
                "role": "hacker",
            },
        )
        assert bad_role.status_code == 400
        assert "角色无效" in bad_role.json()["detail"]

        ok = await client.post(
            "/api/admin/admins",
            headers=super_admin_headers,
            json={"email": "dup@example.com", "password": STRONG_PASSWORD, "name": "第一个"},
        )
        assert ok.status_code == 201
        duplicate = await client.post(
            "/api/admin/admins",
            headers=super_admin_headers,
            json={"email": "dup@example.com", "password": STRONG_PASSWORD, "name": "第二个"},
        )
        assert duplicate.status_code == 409
        assert "已被注册" in duplicate.json()["detail"]

    @pytest.mark.parametrize(
        ("password", "reason"),
        [
            ("sh0rt!A", "少于 8 位"),
            ("alllowercase1!", "缺大写字母"),
            ("ALLUPPERCASE1!", "缺小写字母"),
            ("NoDigitsHere!!", "缺数字"),
            ("NoSpecial123Aa", "缺特殊字符"),
            ("A@1" + "汉" * 24, "UTF-8 超过 bcrypt 72 字节上限"),
        ],
    )
    async def test_create_admin_password_policy_enforced(
        self, client, super_admin_headers, password, reason
    ):
        resp = await client.post(
            "/api/admin/admins",
            headers=super_admin_headers,
            json={
                "email": "weak-password@example.com",
                "password": password,
                "name": "弱密码",
                "role": "ops_admin",
            },
        )
        assert resp.status_code == 422, reason

    async def test_update_admin_role_password_and_negatives(
        self, client, super_admin_headers
    ):
        created = await client.post(
            "/api/admin/admins",
            headers=super_admin_headers,
            json={"email": "target@example.com", "password": STRONG_PASSWORD, "name": "被改者"},
        )
        assert created.status_code == 201
        admin_id = created.json()["id"]

        role_change = await client.put(
            f"/api/admin/admins/{admin_id}",
            headers=super_admin_headers,
            json={"role": "support_admin"},
        )
        assert role_change.status_code == 200
        assert role_change.json()["role"] == "support_admin"

        password_change = await client.put(
            f"/api/admin/admins/{admin_id}",
            headers=super_admin_headers,
            json={"password": "N3w!Passw0rd"},
        )
        assert password_change.status_code == 200

        disable = await client.put(
            f"/api/admin/admins/{admin_id}",
            headers=super_admin_headers,
            json={"is_active": False},
        )
        assert disable.status_code == 200
        assert disable.json()["is_active"] is False

        weak_password = await client.put(
            f"/api/admin/admins/{admin_id}",
            headers=super_admin_headers,
            json={"password": "weakpassword"},
        )
        assert weak_password.status_code == 422

        bad_role = await client.put(
            f"/api/admin/admins/{admin_id}",
            headers=super_admin_headers,
            json={"role": "ceo"},
        )
        assert bad_role.status_code == 400
        unknown = await client.put(
            f"/api/admin/admins/{uuid.uuid4()}",
            headers=super_admin_headers,
            json={"name": "幽灵"},
        )
        assert unknown.status_code == 404



# ═════════════════════════════ audit-logs ═════════════════════════════


class TestAdminAuditLogs:
    async def test_admin_actions_are_audited_and_queryable(
        self, client, db_session, super_admin_headers
    ):
        plan_id = await seed_plan(db_session, code="audit-plan")
        onboard = await client.post(
            "/api/admin/tenants",
            headers=super_admin_headers,
            json=tenant_create_payload(plan_id, slug="audit-log-tenant"),
        )
        assert onboard.status_code == 201

        logs = await client.get("/api/admin/audit-logs", headers=super_admin_headers)
        assert logs.status_code == 200, logs.text
        body = logs.json()
        assert body["page"] == 1
        assert body["page_size"] == 20
        create_rows = [item for item in body["items"] if item["action"] == "create"]
        assert create_rows, "租户接入应写入管理员审计日志"
        row = create_rows[0]
        assert row["resource_type"] == "tenant"
        assert row["admin_email"] == "saas-super@example.com"
        assert row["created_at"]

        filtered = await client.get(
            "/api/admin/audit-logs?action=create", headers=super_admin_headers
        )
        assert filtered.status_code == 200
        assert all(item["action"] == "create" for item in filtered.json()["items"])

        empty = await client.get(
            "/api/admin/audit-logs?action=no-such-action", headers=super_admin_headers
        )
        assert empty.status_code == 200
        assert empty.json()["total"] == 0
        assert empty.json()["items"] == []
