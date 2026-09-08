"""对抗性测试（2026-08-16 复审轮）。

目标：验证 2026-08-16 复审报告（docs/full-project-audit-recheck-2026-08-16.md）
中残留安全发现的**真实可利用性**。

约定：
  - 以「期望的安全行为」写断言（expect 403 / expect 不落库 / expect 不共享预算）。
  - 测试失败 = 漏洞确认（当前代码存在可利用缺陷）；
  - 测试通过 = 修复有效 / 当前行为与设计一致（回归保护）。
  - 每个测试顶部注明对应复审编号（R1/R2/R3/R6）。

（原 R4「经验云查询预算全局共享」用例随 app/services/experience_cloud.py
下线一并移除——该模块已整体删除。）

运行：python -m pytest tests/test_adversarial_audit_2026_08_16.py -v
"""

from __future__ import annotations

import uuid

import pytest
from tests.conftest import TEST_MERCHANT_ID


pytestmark = pytest.mark.asyncio


# ═══════════════════════════════════════════════════════════════════
# 辅助
# ═══════════════════════════════════════════════════════════════════


async def _create_market(client, role="market_admin", name="对抗市场"):
    """以指定角色创建市场，返回 market_id。"""
    res = await client.post(
        "/api/v1/market-admin/markets",
        json={"name": name},
        headers={"X-Test-Token-Role": role},
    )
    assert res.status_code == 200, res.text
    return res.json()["data"]["market_id"]


async def _seed_membership(db_session, market_id, merchant_id=TEST_MERCHANT_ID):
    """直接把当前测试商户写入 MarketMerchant（绕过 register_merchant 角色检查）。"""
    from app.models.market import MarketMerchant

    async with db_session() as session:
        session.add(
            MarketMerchant(
                market_id=uuid.UUID(market_id),
                merchant_id=uuid.UUID(merchant_id),
                stall_number="A-01",
                category="蔬菜",
            )
        )
        await session.commit()


async def _create_merchant(db_session, name="受害者", tenant_id=None) -> str:
    """在 DB 直插一个目标商户，返回其 id。"""
    from app.models.merchant import Merchant

    async with db_session() as session:
        m = Merchant(name=name, business_type="蔬菜", tenant_id=tenant_id)
        session.add(m)
        await session.commit()
        await session.refresh(m)
        return str(m.id)


# ═══════════════════════════════════════════════════════════════════
# R1：market_admin 巡检/投诉/处置仍缺角色校验
# ═══════════════════════════════════════════════════════════════════


class TestR1InspectionForgery:
    async def test_owner_member_can_forge_inspection_for_other_merchant(
        self, client, db_session
    ):
        """普通 owner（仅市场成员）给同市场其他商户写 fail 巡检 → 应 403。

        当前实现：create_inspection 只做 _require_market_member，无角色校验，
        且对 body.merchant_id 目标不做归属校验 → 预期 200（漏洞确认）。
        """
        market_id = await _create_market(client)
        await _seed_membership(db_session, market_id)
        victim_id = await _create_merchant(db_session, "竞争对手")

        res = await client.post(
            "/api/v1/market-admin/inspections",
            json={
                "market_id": market_id,
                "merchant_id": victim_id,
                "inspector": "随便写的",
                "result": "fail",
                "notes": "伪造的不合格结论",
            },
        )
        assert res.status_code == 403, (
            f"漏洞确认：普通成员可伪造巡检（{res.status_code}）"
        )

    async def test_market_admin_can_target_merchant_outside_market(
        self, client, db_session
    ):
        """巡检目标商户不在该市场 → 应 403（目标归属校验缺失）。

        当前实现：market_id 只用于校验操作者成员身份，目标 merchant_id 未校验
        是否属于该市场 → 预期 200（漏洞确认）。
        """
        market_id = await _create_market(client)
        await _seed_membership(db_session, market_id)
        outsider_id = await _create_merchant(db_session, "外市场商户")

        res = await client.post(
            "/api/v1/market-admin/inspections",
            json={
                "market_id": market_id,
                "merchant_id": outsider_id,
                "inspector": "管理员",
                "result": "fail",
            },
            headers={"X-Test-Token-Role": "market_admin"},
        )
        assert res.status_code == 403, (
            f"漏洞确认：巡检可指向市场外商户（{res.status_code}）"
        )


class TestR1ComplaintResolution:
    async def test_owner_member_can_create_complaint_for_other(
        self, client, db_session
    ):
        """普通成员可代他人投诉 → 应 403。"""
        market_id = await _create_market(client)
        await _seed_membership(db_session, market_id)
        victim_id = await _create_merchant(db_session, "被代投诉商户")

        res = await client.post(
            "/api/v1/market-admin/complaints",
            json={
                "market_id": market_id,
                "merchant_id": victim_id,
                "complaint_type": "食品安全",
                "description": "代写的投诉",
            },
        )
        assert res.status_code == 403, (
            f"漏洞确认：普通成员可代他人投诉（{res.status_code}）"
        )

    async def test_owner_member_can_resolve_others_complaint(
        self, client, db_session
    ):
        """普通成员可处置他人投诉 → 应 403。"""
        market_id = await _create_market(client)
        await _seed_membership(db_session, market_id)
        victim_id = await _create_merchant(db_session, "被处置商户")
        # 让受害商户也属于该市场，保证 market_admin 能合法创建投诉，
        # 从而真正验证“普通成员处置他人投诉”这一越权点。
        await _seed_membership(db_session, market_id, merchant_id=victim_id)

        # 由 market_admin 先创建一条投诉
        res = await client.post(
            "/api/v1/market-admin/complaints",
            json={
                "market_id": market_id,
                "merchant_id": victim_id,
                "complaint_type": "卫生",
                "description": "真实投诉",
            },
            headers={"X-Test-Token-Role": "market_admin"},
        )
        assert res.status_code == 200, res.text
        complaint_id = res.json()["data"]["id"]

        # 普通 owner 成员尝试处置
        res = await client.put(
            f"/api/v1/market-admin/complaints/{complaint_id}/resolve",
            json={"resolution": "我直接标记处理完了"},
        )
        assert res.status_code == 403, (
            f"漏洞确认：普通成员可处置他人投诉（{res.status_code}）"
        )


class TestR1RoleGateBypass:
    async def test_owner_can_self_grant_market_admin_and_create_market(
        self, auth_client, db_session
    ):
        """owner 自助创建 market_admin 员工必须被拒绝 → 403。

        角色门禁本意是「只有市场/租户/平台管理员可建市场」，
        create_staff 也不允许 owner 授予 market_admin 角色。
        """
        from app.core.security import create_access_token

        owner_token = create_access_token(uuid.UUID(TEST_MERCHANT_ID), role="owner")
        headers = {"Authorization": f"Bearer {owner_token}"}

        # 1) owner 创建 market_admin 员工 → 必须 403
        res = await auth_client.post(
            "/api/v1/staff",
            json={"name": "自封管理员", "role": "market_admin", "pin_code": "123456"},
            headers=headers,
        )
        assert res.status_code == 403, (
            f"漏洞已修复：owner 不能再自封 market_admin（{res.status_code}）"
        )

    async def test_owner_cannot_create_market_with_owner_token(
        self, auth_client, db_session
    ):
        """回归：owner 直接建市场必须 403（真实 JWT 链路）。"""
        from app.core.security import create_access_token

        token = create_access_token(uuid.UUID(TEST_MERCHANT_ID), role="owner")
        res = await auth_client.post(
            "/api/v1/market-admin/markets",
            json={"name": "不该建成"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert res.status_code == 403


# ═══════════════════════════════════════════════════════════════════
# R2：多租户严格模式（STRICT_TENANT_REQUIRED=True）— 未绑定商户必须 403
# ═══════════════════════════════════════════════════════════════════


class TestR2TenantTransition:
    async def test_unbound_merchant_blocked_by_tenant_gate(
        self, auth_client, db_session
    ):
        """未绑定租户的商户访问租户自助接口必须 403。

        严格模式已启用（STRICT_TENANT_REQUIRED=True）：未绑定租户不再获得
        模拟免费版/999 配额，而是被租户门禁直接拒绝。
        """
        from app.models.merchant import Merchant

        # 独立创建未绑定租户的商户，验证严格门禁不依赖 TEST 商户状态。
        unbound_merchant_id = uuid.uuid4()
        async with db_session() as session:
            session.add(
                Merchant(
                    id=unbound_merchant_id,
                    name="未绑定对抗商户",
                    business_type="蔬菜",
                    role="owner",
                    tenant_id=None,
                )
            )
            await session.commit()

        from app.core.security import create_access_token

        token = create_access_token(unbound_merchant_id, role="owner")
        res = await auth_client.get(
            "/api/v1/tenant/subscription",
            headers={"Authorization": f"Bearer {token}"},
        )
        # 严格模式：未绑定租户 → 403，不再返回 mock/空数据
        assert res.status_code == 403, res.text
        assert "未绑定租户" in res.json()["detail"]


# ═══════════════════════════════════════════════════════════════════
# R6：幂等中间件把含 JWT 的响应体明文落库
# ═══════════════════════════════════════════════════════════════════


class TestR6IdempotencyJwtLeak:
    async def test_wechat_login_token_not_stored_in_idempotency_table(
        self, auth_client, monkeypatch, db_session
    ):
        """带幂等键的微信登录 → token 不应明文出现在业务库幂等表。

        当前实现：中间件对 2xx 把完整响应体存入 IdempotencyRecord.response_body
        → 预期测试失败（漏洞确认：JWT 落库）。
        """
        from sqlalchemy import select

        from app.models.idempotency import IdempotencyRecord

        async def fake_code2session(code: str) -> str:
            return "openid-adversarial-r6"

        monkeypatch.setattr("app.routers.auth.wechat_code2session", fake_code2session)

        resp = await auth_client.post(
            "/api/v1/auth/wechat-login",
            json={"code": "adv-code"},
            headers={"Idempotency-Key": "adv-r6-key-0000000001"},
        )
        assert resp.status_code == 200, resp.text
        token = resp.json()["data"]["token"]

        async with db_session() as session:
            rows = (
                (await session.execute(select(IdempotencyRecord))).scalars().all()
            )
        assert rows, "幂等记录应存在"
        leaked = [r.response_body or "" for r in rows]
        assert all(token not in body for body in leaked), (
            "漏洞确认：登录 JWT 明文存入了幂等表（数据静态泄露）"
        )


# ═══════════════════════════════════════════════════════════════════
# 回归：已修复项不应复发
# ═══════════════════════════════════════════════════════════════════


class TestRegression:
    async def test_trace_code_wildcard_rejected(self, client):
        """P1-9 修复回归：% / _ 等 LIKE 通配符必须被格式白名单拦截。"""
        res = await client.get("/api/v1/food-safety/trace/%25")
        assert res.status_code == 400, res.text

        res = await client.get("/api/v1/food-safety/trace/abcd_1234")
        assert res.status_code == 400, res.text

    async def test_register_merchant_cross_tenant_still_blocked(self, client, db_session):
        """C-10 修复回归：跨租户注册入场仍被 403。"""
        from app.models.merchant import Merchant

        tenant_a = uuid.uuid4()
        tenant_b = uuid.uuid4()

        async with db_session() as session:
            test_merchant = await session.get(
                Merchant, uuid.UUID(TEST_MERCHANT_ID)
            )
            test_merchant.tenant_id = tenant_a
            target = Merchant(name="他租户商户", business_type="水果", tenant_id=tenant_b)
            session.add(target)
            await session.commit()
            await session.refresh(target)
            target_id = str(target.id)

        market_id = await _create_market(client, name="跨租户回归市场")
        await _seed_membership(db_session, market_id)

        res = await client.post(
            "/api/v1/market-admin/merchants",
            json={"market_id": market_id, "merchant_id": target_id},
            headers={"X-Test-Token-Role": "market_admin"},
        )
        assert res.status_code == 403
        assert "跨租户" in res.json()["detail"]
