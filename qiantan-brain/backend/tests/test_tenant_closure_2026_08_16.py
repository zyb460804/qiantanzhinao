"""多租户收口回归（2026-08-16）：微信登录自动绑定默认租户 + 严格门禁。

覆盖：
  - 新微信登录商户自动绑定默认租户（不再落为 tenant_id=None）
  - 存量未绑定商户登录时幂等补绑默认租户
  - 默认租户全局唯一（slug=default），不会重复创建
  - 未绑定租户访问租户自助接口 → 403（STRICT_TENANT_REQUIRED=True）
  - 已绑定默认租户的商户仍可正常访问租户自助接口
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import func, select
from tests.conftest import DEFAULT_TENANT_SLUG

from app.models.merchant import Merchant
from app.models.saas import Tenant


pytestmark = pytest.mark.asyncio


async def _login(auth_client, monkeypatch, openid: str) -> str:
    """模拟微信 code2session 返回指定 openid，完成登录并返回 JWT。"""

    async def fake_code2session(code: str) -> str:
        return openid

    monkeypatch.setattr("app.routers.auth.wechat_code2session", fake_code2session)
    resp = await auth_client.post("/api/v1/auth/wechat-login", json={"code": "any-code"})
    assert resp.status_code == 200, resp.text
    return resp.json()["data"]["token"]


async def test_wechat_login_creates_and_binds_default_tenant(auth_client, monkeypatch, db_session):
    """新商户微信登录后必须绑定一个 active 的默认租户。"""
    openid = "openid-closure-new"
    await _login(auth_client, monkeypatch, openid)

    async with db_session() as session:
        merchant = await session.scalar(
            select(Merchant).where(Merchant.wechat_openid == openid)
        )
        assert merchant is not None
        assert merchant.tenant_id is not None, "新商户登录后必须绑定 tenant_id"

        tenant = await session.get(Tenant, merchant.tenant_id)
        assert tenant is not None
        assert tenant.slug == DEFAULT_TENANT_SLUG
        assert tenant.status == "active"


async def test_wechat_login_backfills_old_unbound_merchant(auth_client, monkeypatch, db_session):
    """存量未绑定租户的老商户登录时自动补绑默认租户（幂等）。"""
    openid = "openid-closure-old"
    async with db_session() as session:
        session.add(Merchant(name="老商户", wechat_openid=openid, role="owner", tenant_id=None))
        await session.commit()

    await _login(auth_client, monkeypatch, openid)

    async with db_session() as session:
        merchant = await session.scalar(
            select(Merchant).where(Merchant.wechat_openid == openid)
        )
        assert merchant is not None
        assert merchant.tenant_id is not None, "老商户登录后必须补绑 tenant_id"

        tenant = await session.get(Tenant, merchant.tenant_id)
        assert tenant is not None
        assert tenant.status == "active"


async def test_wechat_login_default_tenant_is_idempotent(
    auth_client, monkeypatch, db_session
):
    """多个新商户登录只创建一个默认租户，且共享同一 tenant_id。"""
    await _login(auth_client, monkeypatch, "openid-closure-idem-1")
    await _login(auth_client, monkeypatch, "openid-closure-idem-2")

    async with db_session() as session:
        tenant_count = await session.scalar(
            select(func.count()).select_from(Tenant).where(Tenant.slug == DEFAULT_TENANT_SLUG)
        )
        assert tenant_count == 1

        m1 = await session.scalar(
            select(Merchant).where(Merchant.wechat_openid == "openid-closure-idem-1")
        )
        m2 = await session.scalar(
            select(Merchant).where(Merchant.wechat_openid == "openid-closure-idem-2")
        )
        assert m1 is not None and m2 is not None
        assert m1.tenant_id == m2.tenant_id


async def test_unbound_merchant_tenant_subscription_returns_403(
    auth_client, db_session
):
    """严格模式下未绑定租户访问 /tenant/subscription 必须 403。"""
    from app.core.security import create_access_token

    unbound_merchant_id = uuid.uuid4()
    async with db_session() as session:
        session.add(
            Merchant(
                id=unbound_merchant_id,
                name="未绑定收口商户",
                business_type="蔬菜",
                role="owner",
                tenant_id=None,
            )
        )
        await session.commit()

    token = create_access_token(unbound_merchant_id, role="owner")
    resp = await auth_client.get(
        "/api/v1/tenant/subscription",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 403, resp.text
    assert "未绑定租户" in resp.json()["detail"]


async def test_bound_default_tenant_can_access_tenant_portal(
    auth_client, monkeypatch, db_session
):
    """已绑定默认租户的商户可以正常访问租户自助接口（不被严格门禁误伤）。"""
    token = await _login(auth_client, monkeypatch, "openid-closure-bound")
    resp = await auth_client.get(
        "/api/v1/tenant/subscription",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200, resp.text
