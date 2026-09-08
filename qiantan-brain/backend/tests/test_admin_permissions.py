"""管理后台权限体系测试 — require_admin_permission 矩阵 / 认证链 / 越权负例。

角色矩阵依据 app/core/admin_permissions.py 的 ROLE_PERMISSIONS：
  super_admin   全权限
  ops_admin     运营：可建/改租户，但无 tenant.suspend / 订阅变更 / 套餐写 / admin.manage
  billing_admin 计费：套餐+订阅+发票+用量记账，无租户写入、无 admin.manage
  support_admin 客服：全只读，无 audit.read
  auditor       审计：只读 + audit.read + export
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import jwt as pyjwt
import pytest
import pytest_asyncio
from fastapi import HTTPException

from app.config import settings
from app.core.admin_permissions import (
    ADMIN_MANAGE,
    ALL_PERMISSIONS,
    AUDIT_READ,
    ROLE_PERMISSIONS,
    SUBSCRIPTION_CHANGE,
    TENANT_SUSPEND,
    check_suspend_permission,
    get_permission_manifest,
    require_admin_permission,
)
from app.core.admin_security import create_admin_token, hash_password
from app.models.saas import Plan, PlatformAdmin, Subscription, Tenant


STRONG_PASSWORD = "Str0ng!Pass"


@pytest_asyncio.fixture
async def make_admin(db_session):
    """创建指定角色的 PlatformAdmin 并返回 Bearer 头（真实鉴权链路）。"""

    async def _make(role: str, *, is_active: bool = True) -> dict[str, str]:
        admin_id = uuid.uuid4()
        async with db_session() as session:
            session.add(
                PlatformAdmin(
                    id=admin_id,
                    email=f"{role}-{admin_id.hex[:8]}@example.com",
                    password_hash="not-used-in-token-tests",
                    name=f"测试{role}",
                    role=role,
                    is_active=is_active,
                )
            )
            await session.commit()
        return {"Authorization": f"Bearer {create_admin_token(admin_id, role=role)}"}

    return _make


async def seed_plan(db_session, code="perm-plan") -> uuid.UUID:
    plan_id = uuid.uuid4()
    async with db_session() as session:
        session.add(Plan(id=plan_id, code=code, name=f"{code} 套餐", is_active=True))
        await session.commit()
    return plan_id


async def seed_tenant(db_session, *, status="active", slug="perm-tenant", plan_id=None):
    tenant_id = uuid.uuid4()
    async with db_session() as session:
        session.add(
            Tenant(id=tenant_id, name="权限矩阵租户", slug=slug, status=status, plan_id=plan_id)
        )
        await session.commit()
    return tenant_id


async def seed_subscription(db_session, tenant_id, plan_id, *, status="active"):
    sub_id = uuid.uuid4()
    async with db_session() as session:
        session.add(
            Subscription(
                id=sub_id,
                tenant_id=tenant_id,
                plan_id=plan_id,
                billing_cycle="monthly",
                status=status,
                auto_renew=True,
            )
        )
        await session.commit()
    return sub_id



class TestAdminAuthenticationChain:
    @pytest.mark.parametrize(
        ("method", "path"),
        [
            ("GET", "/api/admin/tenants"),
            ("POST", "/api/admin/tenants"),
            ("GET", "/api/admin/subscriptions"),
            ("POST", "/api/admin/subscriptions"),
            ("GET", "/api/admin/plans"),
            ("POST", "/api/admin/plans"),
            ("GET", "/api/admin/admins"),
            ("POST", "/api/admin/admins"),
            ("GET", "/api/admin/audit-logs"),
            ("GET", "/api/admin/usage/00000000-0000-0000-0000-000000000099/quotas"),
            ("POST", "/api/admin/usage/00000000-0000-0000-0000-000000000099/record"),
        ],
    )
    async def test_missing_credentials_returns_401(self, client, method, path):
        resp = await client.request(method, path, json={})
        assert resp.status_code == 401

    async def test_merchant_jwt_rejected_by_admin_issuer_check(self, client):
        now = datetime.now(UTC)
        merchant_token = pyjwt.encode(
            {
                "sub": str(uuid.uuid4()),
                "exp": now + timedelta(minutes=30),
                "iss": "qiantan-brain",
            },
            settings.jwt_secret,
            algorithm=settings.jwt_algorithm,
        )
        resp = await client.get(
            "/api/admin/tenants", headers={"Authorization": f"Bearer {merchant_token}"}
        )
        assert resp.status_code == 401

    async def test_garbage_and_wrong_signature_tokens_rejected(self, client):
        wrong_secret = pyjwt.encode(
            {
                "sub": str(uuid.uuid4()),
                "exp": datetime.now(UTC) + timedelta(minutes=5),
                "iss": "qiantan-admin",
            },
            "this-wrong-secret-is-definitely-longer-than-32-bytes!",
            algorithm="HS256",
        )
        for token in ("garbage.token.value", wrong_secret):
            resp = await client.get(
                "/api/admin/tenants", headers={"Authorization": f"Bearer {token}"}
            )
            assert resp.status_code == 401

    async def test_unknown_role_fails_closed(self, client, make_admin):
        headers = await make_admin("intern")
        resp = await client.get("/api/admin/tenants", headers=headers)
        assert resp.status_code == 403

    async def test_inactive_admin_rejected(self, client, make_admin):
        headers = await make_admin("super_admin", is_active=False)
        resp = await client.get("/api/admin/tenants", headers=headers)
        assert resp.status_code == 403
        assert "停用" in resp.json()["detail"]

    async def test_http_only_cookie_session_flow(self, auth_client, db_session):
        async with db_session() as session:
            session.add_all(
                [
                    PlatformAdmin(
                        email="cookie-super@example.com",
                        password_hash=hash_password(STRONG_PASSWORD),
                        name="曲奇超管",
                        role="super_admin",
                        is_active=True,
                    ),
                    PlatformAdmin(
                        email="cookie-support@example.com",
                        password_hash=hash_password(STRONG_PASSWORD),
                        name="曲奇客服",
                        role="support_admin",
                        is_active=True,
                    ),
                ]
            )
            await session.commit()

        login = await auth_client.post(
            "/api/admin/login",
            json={"email": "cookie-super@example.com", "password": STRONG_PASSWORD},
        )
        assert login.status_code == 200
        # 未显式 opt-in 时不下发 Bearer token，只种 HttpOnly cookie
        assert "token" not in login.json()
        assert settings.admin_cookie_name in login.cookies

        tenants = await auth_client.get("/api/admin/tenants")
        assert tenants.status_code == 200

        logout = await auth_client.post("/api/admin/logout")
        assert logout.status_code == 200
        # 登出吊销 jti / 清 cookie 后，同一会话不再有权限
        after_logout = await auth_client.get("/api/admin/tenants")
        assert after_logout.status_code == 401

        wrong_password = await auth_client.post(
            "/api/admin/login",
            json={"email": "cookie-support@example.com", "password": "Wrong!Pass1"},
        )
        assert wrong_password.status_code == 401

        support_login = await auth_client.post(
            "/api/admin/login",
            json={"email": "cookie-support@example.com", "password": STRONG_PASSWORD},
        )
        assert support_login.status_code == 200
        # 低权角色经 cookie 会话访问 SaaS 管理端点：读 OK，写被拒
        read = await auth_client.get("/api/admin/tenants")
        assert read.status_code == 200
        plan_id = await seed_plan(db_session, code="cookie-plan")
        write = await auth_client.post(
            "/api/admin/tenants",
            json={
                "name": "越权市场",
                "slug": "cookie-forbidden",
                "plan_id": str(plan_id),
                "merchant_name": "越权摊主",
            },
        )
        assert write.status_code == 403
        assert write.json()["detail"]["code"] == "FORBIDDEN"



class TestSaasPermissionMatrix:
    """按 ROLE_PERMISSIONS 抽测写操作矩阵：低权 403 / super_admin 成功。"""

    @pytest.mark.parametrize(
        ("role", "expected"),
        [
            ("super_admin", 201),
            ("ops_admin", 201),  # 持有 tenant.create
            ("billing_admin", 403),  # 仅有 tenant.read
            ("support_admin", 403),
            ("auditor", 403),
        ],
    )
    async def test_create_tenant_matrix(self, client, db_session, make_admin, role, expected):
        plan_id = await seed_plan(db_session)
        headers = await make_admin(role)
        resp = await client.post(
            "/api/admin/tenants",
            headers=headers,
            json={
                "name": "矩阵市场",
                "slug": "matrix-create",
                "plan_id": str(plan_id),
                "merchant_name": "矩阵摊主",
            },
        )
        assert resp.status_code == expected

    @pytest.mark.parametrize(
        ("role", "expected"),
        [
            ("super_admin", 200),
            ("ops_admin", 200),  # 持有 tenant.update
            ("billing_admin", 403),
            ("support_admin", 403),
            ("auditor", 403),
        ],
    )
    async def test_update_tenant_matrix(self, client, db_session, make_admin, role, expected):
        tenant_id = await seed_tenant(db_session)
        headers = await make_admin(role)
        resp = await client.put(
            f"/api/admin/tenants/{tenant_id}", headers=headers, json={"name": "改名市场"}
        )
        assert resp.status_code == expected

    @pytest.mark.parametrize(
        ("role", "expected"),
        [
            ("super_admin", 200),
            ("ops_admin", 403),  # inline guard：无 tenant.suspend
            ("billing_admin", 403),
            ("support_admin", 403),
            ("auditor", 403),
        ],
    )
    async def test_suspend_tenant_matrix(self, client, db_session, make_admin, role, expected):
        tenant_id = await seed_tenant(db_session, status="active")
        headers = await make_admin(role)
        resp = await client.put(
            f"/api/admin/tenants/{tenant_id}", headers=headers, json={"status": "suspended"}
        )
        assert resp.status_code == expected
        if expected == 200:
            assert resp.json()["status"] == "suspended"

    @pytest.mark.parametrize(
        ("role", "expected"),
        [
            ("super_admin", 200),
            ("ops_admin", 403),  # 无 subscription.change
            ("billing_admin", 200),  # 计费变更归口
            ("support_admin", 403),
            ("auditor", 403),
        ],
    )
    async def test_cancel_subscription_matrix(
        self, client, db_session, make_admin, role, expected
    ):
        plan_id = await seed_plan(db_session)
        tenant_id = await seed_tenant(db_session, plan_id=plan_id)
        sub_id = await seed_subscription(db_session, tenant_id, plan_id, status="active")
        headers = await make_admin(role)
        resp = await client.post(
            f"/api/admin/subscriptions/{sub_id}/cancel",
            headers=headers,
            json={"reason": "矩阵测试"},
        )
        assert resp.status_code == expected

    @pytest.mark.parametrize(
        ("role", "expected"),
        [
            ("super_admin", 204),
            ("ops_admin", 403),
            ("billing_admin", 204),  # plan.delete
            ("support_admin", 403),
            ("auditor", 403),
        ],
    )
    async def test_delete_plan_matrix(self, client, db_session, make_admin, role, expected):
        plan_id = await seed_plan(db_session, code=f"del-{role}")
        headers = await make_admin(role)
        resp = await client.delete(f"/api/admin/plans/{plan_id}", headers=headers)
        assert resp.status_code == expected

    @pytest.mark.parametrize(
        ("role", "expected"),
        [
            ("super_admin", 200),
            ("ops_admin", 403),  # usage.adjust 高危
            ("billing_admin", 200),
            ("support_admin", 403),
            ("auditor", 403),
        ],
    )
    async def test_manual_usage_record_matrix(
        self, client, db_session, make_admin, role, expected
    ):
        plan_id = await seed_plan(db_session, code=f"usage-{role}")
        tenant_id = await seed_tenant(db_session, slug=f"usage-{role}", plan_id=plan_id)
        headers = await make_admin(role)
        resp = await client.post(
            f"/api/admin/usage/{tenant_id}/record",
            headers=headers,
            json={"metric": "api_calls", "value": 1},
        )
        assert resp.status_code == expected

    @pytest.mark.parametrize(
        ("role", "expected"),
        [
            ("super_admin", 200),
            ("ops_admin", 403),  # admin.manage 仅 super_admin
            ("billing_admin", 403),
            ("support_admin", 403),
            ("auditor", 403),
        ],
    )
    async def test_admin_management_super_only_matrix(self, client, make_admin, role, expected):
        headers = await make_admin(role)
        resp = await client.get("/api/admin/admins", headers=headers)
        assert resp.status_code == expected

    @pytest.mark.parametrize(
        ("role", "expected"),
        [
            ("super_admin", 200),
            ("ops_admin", 200),  # audit.read
            ("billing_admin", 200),
            ("support_admin", 403),  # 客服无 audit.read
            ("auditor", 200),
        ],
    )
    async def test_audit_logs_read_matrix(self, client, make_admin, role, expected):
        headers = await make_admin(role)
        resp = await client.get("/api/admin/audit-logs", headers=headers)
        assert resp.status_code == expected

    async def test_forbidden_response_envelope(self, client, make_admin):
        headers = await make_admin("support_admin")
        resp = await client.post("/api/admin/tenants", headers=headers, json={})
        assert resp.status_code == 403
        detail = resp.json()["detail"]
        assert isinstance(detail, dict)
        assert detail["code"] == "FORBIDDEN"
        assert "support_admin" in detail["message"]
        assert "tenant.create" in detail["message"]
        assert "request_id" in detail

    async def test_high_risk_permission_check_is_audited(self, client, db_session, make_admin):
        """USAGE_ADJUST 属高危权限：非 super_admin 触发时须落 permission_check 审计。"""
        plan_id = await seed_plan(db_session, code="risk-audit-plan")
        tenant_id = await seed_tenant(db_session, slug="risk-audit-tenant", plan_id=plan_id)
        billing = await make_admin("billing_admin")
        record = await client.post(
            f"/api/admin/usage/{tenant_id}/record",
            headers=billing,
            json={"metric": "api_calls", "value": 1},
        )
        assert record.status_code == 200

        super_headers = await make_admin("super_admin")
        logs = await client.get("/api/admin/audit-logs?page_size=100", headers=super_headers)
        assert logs.status_code == 200
        actions = {item["action"] for item in logs.json()["items"]}
        assert "permission_check.usage.adjust" in actions



class TestAdminPermissionsCore:
    """app/core/admin_permissions.py 的单元级一致性校验。"""

    def test_require_factory_rejects_unregistered_permission(self):
        with pytest.raises(ValueError, match="未注册的权限点"):
            require_admin_permission("nonexistent.perm")

    def test_role_map_roles_and_fail_closed_invariants(self):
        assert set(ROLE_PERMISSIONS) == {
            "super_admin",
            "ops_admin",
            "billing_admin",
            "support_admin",
            "auditor",
        }
        for role, perms in ROLE_PERMISSIONS.items():
            assert perms <= ALL_PERMISSIONS
            if role != "super_admin":
                assert perms < ROLE_PERMISSIONS["super_admin"]
        # 关键边界：低权角色不得持有高危权限
        assert ADMIN_MANAGE not in ROLE_PERMISSIONS["ops_admin"]
        assert TENANT_SUSPEND not in ROLE_PERMISSIONS["ops_admin"]
        assert SUBSCRIPTION_CHANGE not in ROLE_PERMISSIONS["ops_admin"]
        assert AUDIT_READ not in ROLE_PERMISSIONS["support_admin"]

    def test_manifest_exposes_full_matrix(self):
        manifest = get_permission_manifest()
        assert set(manifest["roles"]) == set(ROLE_PERMISSIONS)
        assert set(manifest["permissions"]) == ALL_PERMISSIONS
        assert set(manifest["high_risk"]) <= ALL_PERMISSIONS

    def test_check_suspend_permission_inline_guard(self):
        def admin_of(role):
            return PlatformAdmin(
                id=uuid.uuid4(),
                email=f"{role}@example.com",
                password_hash="x",
                name=role,
                role=role,
                is_active=True,
            )

        assert check_suspend_permission(admin_of("super_admin")) is None
        for role in ("ops_admin", "billing_admin", "support_admin", "auditor"):
            with pytest.raises(HTTPException) as err:
                check_suspend_permission(admin_of(role))
            assert err.value.status_code == 403
