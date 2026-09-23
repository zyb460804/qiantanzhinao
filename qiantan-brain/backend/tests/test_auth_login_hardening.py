"""微信登录加固回归测试（QA P2 修复）。

历史黑盒 QA 发现：POST /api/v1/auth/wechat-login 对 code 零校验（空串/
纯空格/1000 字符/SQL 注入串/XSS 串全部 200），且无频控（1 分钟内可批量
创建 10+ 商户）。本文件固化两项修复：

  - code 形态校验（schemas/auth.py）：trim 后非空、长度 ≤128、字符白名单
    （字母/数字/-/_/~/.），非法输入 422 + 中文报错
  - IP 维度滑动窗口频控（core/rate_limiter.py）：60 秒内最多 10 次登录
    尝试（成功与失败都计数），第 11 次 → 429

dev 环境 mock 登录是有意特性，全部保留：合法 code 仍可正常换 openid 登录。

测试禁用/重置限流的手段：`app.core.rate_limiter._backend = None`（conftest
的 client/auth_client fixture 每个测试已自动重置，等效于"测试默认限流
状态全新"），需要显式重置时调用 `_reset_rate_limit()`。
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest
from fastapi import HTTPException
from sqlalchemy import func, select

from app.core import rate_limiter as rl
from app.models.merchant import Merchant

pytestmark = pytest.mark.asyncio


def _reset_rate_limit() -> None:
    """重置限流后端：测试中禁用/隔离限流状态的 fixture 手段。"""
    rl._backend = None


@pytest.fixture(autouse=True)
def _fresh_rate_limit():
    """每个测试开始时限流状态全新，避免跨用例串扰。"""
    _reset_rate_limit()
    yield
    _reset_rate_limit()


@pytest.fixture
def mock_code2session(monkeypatch):
    """code2session 换成 code→openid 一一映射的假实现，隔离微信网络。"""

    async def fake_code2session(code: str) -> str:
        return f"openid-{code}"

    monkeypatch.setattr("app.routers.auth.wechat_code2session", fake_code2session)
    return fake_code2session


# ── code 形态校验（422）────────────────────────────────────


async def test_empty_code_rejected_422(auth_client):
    """空串 code → 422，中文报错。"""
    resp = await auth_client.post("/api/v1/auth/wechat-login", json={"code": ""})
    assert resp.status_code == 422
    assert "不能为空" in resp.json()["detail"]


async def test_blank_code_rejected_422(auth_client):
    """纯空格 code（trim 后为空）→ 422。"""
    resp = await auth_client.post("/api/v1/auth/wechat-login", json={"code": "   "})
    assert resp.status_code == 422
    assert "不能为空" in resp.json()["detail"]


async def test_overlong_code_rejected_422(auth_client):
    """129 / 1000 字符 code → 422，中文报错（原版本会 200 并建商户）。"""
    for length in (129, 1000):
        resp = await auth_client.post("/api/v1/auth/wechat-login", json={"code": "a" * length})
        assert resp.status_code == 422, f"长度 {length} 应被拒绝"
        assert "长度" in resp.json()["detail"]


async def test_sql_injection_code_rejected_422(auth_client, db_session):
    """SQL 注入串 code → 422，且不落库任何商户。"""
    resp = await auth_client.post(
        "/api/v1/auth/wechat-login",
        json={"code": "'; DROP TABLE merchants;--"},
    )
    assert resp.status_code == 422
    assert "非法字符" in resp.json()["detail"]
    async with db_session() as session:
        count = await session.scalar(select(func.count(Merchant.id)))
    assert count == 1  # 仅 conftest 预置的测试商户，无新增


async def test_xss_code_rejected_422(auth_client):
    """XSS 串 code → 422。"""
    resp = await auth_client.post(
        "/api/v1/auth/wechat-login",
        json={"code": "<script>alert(1)</script>"},
    )
    assert resp.status_code == 422
    assert "非法字符" in resp.json()["detail"]


async def test_code_with_surrounding_spaces_is_trimmed(auth_client, mock_code2session):
    """首尾空白自动 trim 后按白名单校验，下游拿到 trim 后的 code。"""
    resp = await auth_client.post("/api/v1/auth/wechat-login", json={"code": " devCode01 "})
    assert resp.status_code == 200, resp.text
    assert resp.json()["data"]["merchant"]["id"]  # 登录成功


# ── 合法 code 正常登录（dev mock 特性保留）──────────────────


async def test_valid_code_login_still_works(auth_client, mock_code2session, db_session):
    """合法白名单字符的 code 正常登录并创建新商户（is_new=True）。"""
    resp = await auth_client.post(
        "/api/v1/auth/wechat-login", json={"code": "0812Abc~-_.09"}
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert data["token"]
    assert data["is_new"] is True
    async with db_session() as session:
        merchant = await session.scalar(
            select(Merchant).where(Merchant.wechat_openid == "openid-0812Abc~-_.09")
        )
    assert merchant is not None


async def test_invalid_codes_do_not_consume_rate_limit_budget(auth_client, mock_code2session):
    """422 形态校验在 schema 层完成，不消耗频控预算（频控保护的是业务路径）。"""
    for _ in range(12):
        bad = await auth_client.post("/api/v1/auth/wechat-login", json={"code": "@@@@"})
        assert bad.status_code == 422
    ok = await auth_client.post("/api/v1/auth/wechat-login", json={"code": "goodCode1"})
    assert ok.status_code == 200, ok.text


# ── IP 维度滑动窗口频控（429）───────────────────────────────


async def test_11th_login_attempt_in_window_rejected_429(
    auth_client, mock_code2session, db_session
):
    """同 IP 60 秒窗口内前 10 次成功，第 11 次 → 429，商户数量封顶。

    回归 QA 场景：原版本 1 分钟内可创建 10+ 商户；现在第 11 次被拦，
    商户数量恒为 10。
    """
    for i in range(10):
        resp = await auth_client.post("/api/v1/auth/wechat-login", json={"code": f"code{i:02d}"})
        assert resp.status_code == 200, f"第 {i + 1} 次应成功: {resp.text}"

    async with db_session() as session:
        count_before = await session.scalar(select(func.count(Merchant.id))) - 1  # 扣除预置商户
    assert count_before == 10

    flooded = await auth_client.post("/api/v1/auth/wechat-login", json={"code": "code10"})
    assert flooded.status_code == 429
    assert "登录尝试过于频繁" in flooded.json()["detail"]

    async with db_session() as session:
        count_after = (await session.scalar(select(func.count(Merchant.id)))) - 1
    assert count_after == 10, "429 请求不得创建商户"


async def test_failed_attempts_also_count_toward_rate_limit(auth_client, monkeypatch):
    """失败的登录尝试同样计入窗口（防绕过：换着 code 打也不会超量打后端）。"""
    calls = {"n": 0}

    async def failing_code2session(code: str) -> str:
        calls["n"] += 1
        raise HTTPException(status_code=400, detail="微信登录失败: invalid code")

    monkeypatch.setattr("app.routers.auth.wechat_code2session", failing_code2session)

    for _ in range(10):
        resp = await auth_client.post("/api/v1/auth/wechat-login", json={"code": "badcode"})
        assert resp.status_code == 400

    # 第 11 次：频控在 code2session 之前生效 → 429，不再触碰微信接口
    resp = await auth_client.post("/api/v1/auth/wechat-login", json={"code": "badcode"})
    assert resp.status_code == 429
    assert calls["n"] == 10


async def test_rate_limit_is_per_ip_key(auth_client):
    """限流 key 按来源 IP 维度生成（wechat-login:{ip}）。"""
    from starlette.requests import Request as StarletteRequest

    scope = {
        "type": "http",
        "method": "POST",
        "path": "/api/v1/auth/wechat-login",
        "headers": [],
        "query_string": b"",
        "client": ("203.0.113.7", 51000),
    }
    request = StarletteRequest(scope)
    assert rl.get_wechat_login_key(request) == "wechat-login:203.0.113.7"


async def test_rate_limit_state_reset_restores_login(auth_client, mock_code2session):
    """重置限流后端（测试禁用手段）后，被打满的 IP 立即恢复可登录。"""
    for i in range(10):
        await auth_client.post("/api/v1/auth/wechat-login", json={"code": f"rst{i:02d}"})
    assert (
        await auth_client.post("/api/v1/auth/wechat-login", json={"code": "rst10"})
    ).status_code == 429

    _reset_rate_limit()  # conftest fixture 每测试自动执行的同款手段

    ok = await auth_client.post("/api/v1/auth/wechat-login", json={"code": "rst11"})
    assert ok.status_code == 200, ok.text


async def test_memory_hit_sliding_window_expires_without_lock():
    """MemoryBackend.hit 单元级：窗口滑过自动恢复，无锁定残留。

    区别于登录失败限流的"锁 15 分钟"语义：微信登录频控是纯滑动窗口，
    只需等最早一次命中滑出窗口即可恢复，无需任何解锁操作。
    """
    import time

    backend = rl.MemoryBackend()
    key = "wechat-login:1.2.3.4"
    for _ in range(10):
        await backend.hit(key, 10, 60)
    with pytest.raises(HTTPException) as exc_info:
        await backend.hit(key, 10, 60)
    assert exc_info.value.status_code == 429

    # 手动把窗口内命中时间戳推到 61 秒前，模拟窗口自然滑过
    backend._attempts[key] = [t - 61 for t in backend._attempts[key]]
    await backend.hit(key, 10, 60)  # 不应抛出
    assert len(backend._attempts[key]) == 1
