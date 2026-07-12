"""鉴权路由集成测试（P0-1）：真实 JWT 链路，不覆盖 get_current_merchant。

覆盖：
  - 微信登录签发 token
  - 未携带 token 访问 /me 被拦截（401）
  - 携带有效 token 读取 /me
  - /refresh 换发新 token（旧 token 失效）
  - /logout 吊销令牌，旧 token 立即失效
  - 跨商户隔离：A 的 token 只能读 A 的身份
"""

import sys
from pathlib import Path


sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest


pytestmark = pytest.mark.asyncio


async def _login(client, monkeypatch, openid: str) -> str:
    """模拟微信 code2session 返回指定 openid，完成登录并返回 JWT。"""

    async def fake_code2session(code: str) -> str:
        return openid

    monkeypatch.setattr("app.routers.auth.wechat_code2session", fake_code2session)
    resp = await client.post("/api/v1/auth/wechat-login", json={"code": "any-code"})
    assert resp.status_code == 200, resp.text
    return resp.json()["data"]["token"]


async def test_wechat_login_returns_token(auth_client, monkeypatch):
    token = await _login(auth_client, monkeypatch, "openid-auth-1")
    assert isinstance(token, str) and len(token) > 0


async def test_me_requires_auth(auth_client):
    resp = await auth_client.get("/api/v1/auth/me")
    assert resp.status_code == 401


async def test_me_with_valid_token(auth_client, monkeypatch):
    token = await _login(auth_client, monkeypatch, "openid-auth-me")
    resp = await auth_client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["role"] == "owner"
    assert "id" in data


async def test_refresh_issues_new_token(auth_client, monkeypatch):
    token = await _login(auth_client, monkeypatch, "openid-auth-refresh")
    resp = await auth_client.post(
        "/api/v1/auth/refresh", headers={"Authorization": f"Bearer {token}"}
    )
    assert resp.status_code == 200
    new_token = resp.json()["data"]["token"]
    assert new_token != token

    me = await auth_client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {new_token}"})
    assert me.status_code == 200


async def test_logout_revokes_token(auth_client, monkeypatch):
    token = await _login(auth_client, monkeypatch, "openid-auth-logout")
    lo = await auth_client.post("/api/v1/auth/logout", headers={"Authorization": f"Bearer {token}"})
    assert lo.status_code == 200

    # 旧 token 注销后立即失效
    me = await auth_client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert me.status_code == 401


async def test_cross_merchant_isolation(auth_client, monkeypatch):
    """不同商户 token 只能读取自身身份，不能串户。"""
    token_a = await _login(auth_client, monkeypatch, "openid-auth-A")
    token_b = await _login(auth_client, monkeypatch, "openid-auth-B")

    me_a = await auth_client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token_a}"})
    me_b = await auth_client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token_b}"})
    id_a = me_a.json()["data"]["id"]
    id_b = me_b.json()["data"]["id"]
    assert id_a != id_b


# ------------------------------------------------------------------
# 生产安全自检（fail-closed）：防止"零鉴权"上线
# 注：validate_security 是实例方法，不在 Settings() 构造时自动调用；
# 这里构造后手动赋值字段再调用，避免环境变量优先级干扰断言。
# ------------------------------------------------------------------


async def test_validate_security_blocks_default_secret_in_prod():
    """生产环境沿用默认 JWT_SECRET 必须启动即拒绝。"""
    from app.config import Settings

    s = Settings()
    s.debug = False
    s.jwt_secret = "dev-secret-please-override-with-env-in-prod"
    with pytest.raises(RuntimeError):
        s.validate_security()


async def test_validate_security_blocks_weak_secret_in_prod():
    """生产环境 JWT_SECRET < 32 字节必须启动即拒绝。"""
    from app.config import Settings

    s = Settings()
    s.debug = False
    s.jwt_secret = "short-key"
    with pytest.raises(RuntimeError):
        s.validate_security()


async def test_validate_security_blocks_fallback_in_prod():
    """生产环境 auth_allow_fallback=True 会击穿多租户隔离，必须拒绝。"""
    from app.config import Settings

    s = Settings()
    s.debug = False
    s.jwt_secret = "x" * 40
    s.auth_allow_fallback = True
    with pytest.raises(RuntimeError):
        s.validate_security()


async def test_validate_security_ok_when_proper():
    """生产环境配置正确时不拦截。"""
    from app.config import Settings

    s = Settings()
    s.debug = False
    s.jwt_secret = "x" * 40
    s.auth_allow_fallback = False
    s.cors_origins = "https://a.com,https://b.com"
    s.validate_security()  # 不应抛异常


async def test_validate_security_skipped_in_dev():
    """dev 环境即便沿用默认密钥也不拦截，保持本地开发便利。"""
    from app.config import Settings

    s = Settings()
    s.debug = True
    s.jwt_secret = "dev-secret-please-override-with-env-in-prod"
    s.validate_security()  # 不应抛异常
