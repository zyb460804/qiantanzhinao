"""Edge 设备接入鉴权测试（P0）。

验证边缘同步接口不再信任请求体中的 merchant_id，
身份必须来自 JWT token。
"""

import sys
from pathlib import Path


sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest


pytestmark = pytest.mark.asyncio


async def _login(auth_client, monkeypatch, openid: str) -> str:
    """模拟微信登录并返回 JWT。"""

    async def fake_code2session(code: str) -> str:
        return openid

    monkeypatch.setattr("app.routers.auth.wechat_code2session", fake_code2session)
    resp = await auth_client.post("/api/v1/auth/wechat-login", json={"code": "any-code"})
    assert resp.status_code == 200, resp.text
    return resp.json()["data"]["token"]


async def test_edge_ingest_requires_auth(auth_client):
    """无 token 访问 /api/v1/edge/ingest 必须 401。"""
    resp = await auth_client.post("/api/v1/edge/ingest", json={"detections": [], "weight_g": 100})
    assert resp.status_code == 401


async def test_edge_ingest_accepts_valid_token(auth_client, monkeypatch):
    """携带有效 token 时正常回显。"""
    token = await _login(auth_client, monkeypatch, "openid-edge-ok")
    resp = await auth_client.post(
        "/api/v1/edge/ingest",
        json={"detections": ["tomato"], "weight_g": 250},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["accepted"] is True
    assert data["detection_count"] == 1
    assert data["weight_g"] == 250
    assert "merchant_id" in data


async def test_edge_ingest_rejects_body_merchant_id_mismatch(auth_client, monkeypatch):
    """body 中 merchant_id 与 token 商户不一致必须 403。"""
    token = await _login(auth_client, monkeypatch, "openid-edge-mismatch")
    # 先获取正确商户 ID
    me = await auth_client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"})
    real_id = me.json()["data"]["id"]

    resp = await auth_client.post(
        "/api/v1/edge/ingest",
        json={"merchant_id": "00000000-0000-0000-0000-000000000000", "detections": []},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 403

    # 传正确的商户 ID 则通过
    resp = await auth_client.post(
        "/api/v1/edge/ingest",
        json={"merchant_id": real_id, "detections": []},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
