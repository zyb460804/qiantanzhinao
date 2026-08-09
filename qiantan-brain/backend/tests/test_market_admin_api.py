"""Market admin router tests — 角色检查与跨租户校验 (审计 C-3 / C-10).

覆盖：
  - create_market / create_notice 的角色门禁（owner 403, market_admin 200）
  - _require_market_member：非该市场成员即便有 market_admin 角色也无权发通知
  - register_merchant 跨租户保护（不同 tenant_id 的目标商户被 403 拒绝）
  - list_markets 仅返回当前商户关联的市场（不泄露全平台市场）
"""

from __future__ import annotations

import uuid

import pytest

from tests.conftest import TEST_MERCHANT_ID


pytestmark = pytest.mark.asyncio


# ═══════════════════════════════════════════════════════════════════
# 辅助
# ═══════════════════════════════════════════════════════════════════


async def _create_market(client, name="测试市场"):
    """以 market_admin 角色创建市场，返回 market_id。"""
    res = await client.post(
        "/api/v1/market-admin/markets",
        json={"name": name},
        headers={"X-Test-Token-Role": "market_admin"},
    )
    assert res.status_code == 200, res.text
    return res.json()["data"]["market_id"]


async def _seed_market_membership(db_session, market_id, merchant_id=TEST_MERCHANT_ID):
    """直接在 DB 写入 MarketMerchant 关联（绕过 register_merchant 的角色检查）。"""
    from app.models.market import MarketMerchant

    async with db_session() as session:
        mm = MarketMerchant(
            market_id=uuid.UUID(market_id),
            merchant_id=uuid.UUID(merchant_id),
            stall_number="A-01",
            category="蔬菜",
        )
        session.add(mm)
        await session.commit()


# ═══════════════════════════════════════════════════════════════════
# 角色门禁
# ═══════════════════════════════════════════════════════════════════


class TestMarketAdminRoles:
    async def test_owner_cannot_create_market(self, client):
        """普通 owner（无 X-Test-Token-Role）→ 403。"""
        res = await client.post(
            "/api/v1/market-admin/markets", json={"name": "测试市场"}
        )
        assert res.status_code == 403

    async def test_market_admin_can_create_market(self, client):
        """market_admin 角色 → 200。"""
        res = await client.post(
            "/api/v1/market-admin/markets",
            json={"name": "测试市场"},
            headers={"X-Test-Token-Role": "market_admin"},
        )
        assert res.status_code == 200
        assert "market_id" in res.json()["data"]

    async def test_owner_cannot_post_notice(self, client):
        """owner 角色发通知 → 403（角色门禁先于 body 字段校验）。"""
        res = await client.post(
            "/api/v1/market-admin/notices",
            json={
                "market_id": str(uuid.uuid4()),
                "title": "t",
                "content": "c",
            },
        )
        assert res.status_code == 403

    async def test_owner_cannot_register_merchant(self, client):
        """owner 角色注册商户入场 → 403。"""
        res = await client.post(
            "/api/v1/market-admin/merchants",
            json={
                "market_id": str(uuid.uuid4()),
                "merchant_id": str(uuid.uuid4()),
            },
        )
        assert res.status_code == 403

    async def test_tenant_admin_can_create_market(self, client):
        """tenant_admin 同样在允许列表中。"""
        res = await client.post(
            "/api/v1/market-admin/markets",
            json={"name": "租户管理员市场"},
            headers={"X-Test-Token-Role": "tenant_admin"},
        )
        assert res.status_code == 200

    async def test_platform_admin_can_create_market(self, client):
        """platform_admin 同样在允许列表中。"""
        res = await client.post(
            "/api/v1/market-admin/markets",
            json={"name": "平台管理员市场"},
            headers={"X-Test-Token-Role": "platform_admin"},
        )
        assert res.status_code == 200


# ═══════════════════════════════════════════════════════════════════
# 市场成员校验 (_require_market_member)
# ═══════════════════════════════════════════════════════════════════


class TestMarketMembership:
    async def test_market_admin_cannot_post_notice_without_membership(
        self, client, db_session
    ):
        """market_admin 角色但非该市场成员 → 403（_require_market_member）。"""
        market_id = await _create_market(client, "无成员市场")
        res = await client.post(
            "/api/v1/market-admin/notices",
            json={
                "market_id": market_id,
                "title": "通知标题",
                "content": "通知内容",
            },
            headers={"X-Test-Token-Role": "market_admin"},
        )
        assert res.status_code == 403
        assert "不属于此市场" in res.json()["detail"]

    async def test_market_admin_can_post_notice_with_membership(
        self, client, db_session
    ):
        """market_admin 角色且为该市场成员 → 200。"""
        market_id = await _create_market(client, "有成员市场")
        await _seed_market_membership(db_session, market_id)

        res = await client.post(
            "/api/v1/market-admin/notices",
            json={
                "market_id": market_id,
                "title": "停水通知",
                "content": "明天全天停水",
            },
            headers={"X-Test-Token-Role": "market_admin"},
        )
        assert res.status_code == 200, res.text
        assert res.json()["data"]["title"] == "停水通知"

    async def test_list_markets_only_returns_member_markets(self, client, db_session):
        """list_markets 不泄露非关联市场（审计 P1-加载通知列表数据泄露）。"""
        # 创建两个市场，只加入其中一个
        member_market_id = await _create_market(client, "我所在的市场")
        other_market_id = await _create_market(client, "别人的市场")
        await _seed_market_membership(db_session, member_market_id)

        res = await client.get("/api/v1/market-admin/markets")
        assert res.status_code == 200
        market_ids = [m["market_id"] for m in res.json()["data"]]
        assert member_market_id in market_ids
        assert other_market_id not in market_ids


# ═══════════════════════════════════════════════════════════════════
# 跨租户保护 (审计 C-10)
# ═══════════════════════════════════════════════════════════════════


class TestCrossTenantProtection:
    async def test_register_merchant_cross_tenant_blocked(self, client, db_session):
        """market_admin 不能把其他租户的商户塞进自己市场 → 403。"""
        from app.models.merchant import Merchant

        tenant_a = uuid.uuid4()
        tenant_b = uuid.uuid4()

        async with db_session() as session:
            # 把测试商户绑定到 tenant_a
            test_merchant = await session.get(
                Merchant, uuid.UUID(TEST_MERCHANT_ID)
            )
            test_merchant.tenant_id = tenant_a

            # 创建一个属于 tenant_b 的目标商户
            target = Merchant(
                name="其他租户商户",
                business_type="水果",
                tenant_id=tenant_b,
            )
            session.add(target)
            await session.commit()
            await session.refresh(target)
            target_id = str(target.id)

        market_id = await _create_market(client, "跨租户测试市场")
        await _seed_market_membership(db_session, market_id)

        res = await client.post(
            "/api/v1/market-admin/merchants",
            json={
                "market_id": market_id,
                "merchant_id": target_id,
                "stall_number": "B-99",
            },
            headers={"X-Test-Token-Role": "market_admin"},
        )
        assert res.status_code == 403
        assert "跨租户" in res.json()["detail"]

    async def test_register_merchant_same_tenant_allowed(self, client, db_session):
        """同租户商户可以被注册到市场 → 200。"""
        from app.models.merchant import Merchant

        tenant_id = uuid.uuid4()

        async with db_session() as session:
            test_merchant = await session.get(
                Merchant, uuid.UUID(TEST_MERCHANT_ID)
            )
            test_merchant.tenant_id = tenant_id

            target = Merchant(
                name="同租户商户",
                business_type="水产",
                tenant_id=tenant_id,
            )
            session.add(target)
            await session.commit()
            await session.refresh(target)
            target_id = str(target.id)

        market_id = await _create_market(client, "同租户测试市场")
        await _seed_market_membership(db_session, market_id)

        res = await client.post(
            "/api/v1/market-admin/merchants",
            json={
                "market_id": market_id,
                "merchant_id": target_id,
                "stall_number": "C-01",
            },
            headers={"X-Test-Token-Role": "market_admin"},
        )
        assert res.status_code == 200, res.text

    async def test_register_nonexistent_merchant_404(self, client, db_session):
        """目标商户不存在 → 404（不是 500）。"""
        from app.models.merchant import Merchant

        async with db_session() as session:
            test_merchant = await session.get(
                Merchant, uuid.UUID(TEST_MERCHANT_ID)
            )
            test_merchant.tenant_id = uuid.uuid4()

        market_id = await _create_market(client, "404测试市场")
        await _seed_market_membership(db_session, market_id)

        res = await client.post(
            "/api/v1/market-admin/merchants",
            json={
                "market_id": market_id,
                "merchant_id": str(uuid.uuid4()),
            },
            headers={"X-Test-Token-Role": "market_admin"},
        )
        assert res.status_code == 404
