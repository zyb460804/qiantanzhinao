"""Edge 设备接入鉴权测试（P0）。

验证边缘同步接口不再信任请求体中的 merchant_id，
身份必须来自 JWT token。
"""

import sys
import time
import uuid
from pathlib import Path


sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest
from sqlalchemy import select

from app.core.device_auth import _seen_nonces, generate_api_key
from app.models.device import Device
from app.models.merchant import Merchant
from app.models.saas import ApiKey, Tenant


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


async def _register_device(db_session, *, scopes=None, device_active: bool = True):
    tenant_id = uuid.uuid4()
    serial_number = f"edge-test-{uuid.uuid4().hex[:12]}"
    plain_key, key_hash = generate_api_key()
    merchant_id = uuid.UUID("00000000-0000-0000-0000-000000000001")

    async with db_session() as db:
        merchant = await db.get(Merchant, merchant_id)
        tenant = Tenant(
            id=tenant_id,
            name="设备测试租户",
            slug=f"edge-test-{tenant_id.hex[:12]}",
            status="active",
        )
        db.add(tenant)
        merchant.tenant_id = tenant_id
        db.add_all([
            Device(
                merchant_id=merchant_id,
                device_type="camera",
                device_name="测试边缘设备",
                serial_number=serial_number,
                is_active=device_active,
            ),
            ApiKey(
                tenant_id=tenant_id,
                name="测试设备密钥",
                key_hash=key_hash,
                key_prefix=plain_key[:20],
                scopes=(
                    scopes
                    if scopes is not None
                    else ["edge:ingest", "edge:heartbeat"]
                ),
                is_active=True,
            ),
        ])
        await db.commit()

    _seen_nonces.clear()
    return plain_key, serial_number, merchant_id


def _device_headers(
    plain_key: str,
    serial_number: str,
    nonce: str | None = None,
    timestamp: int | None = None,
):
    return {
        "X-Api-Key": plain_key,
        "X-Device-Id": serial_number,
        "X-Timestamp": str(timestamp if timestamp is not None else int(time.time())),
        "X-Nonce": nonce or str(uuid.uuid4()),
    }


async def test_device_ingest_derives_merchant_from_registered_device(
    auth_client, db_session
):
    plain_key, serial_number, merchant_id = await _register_device(db_session)
    response = await auth_client.post(
        "/api/v1/edge/ingest/device",
        headers=_device_headers(plain_key, serial_number),
        json={
            "event_id": str(uuid.uuid4()),
            "detections": ["tomato"],
            "weight_g": 320,
        },
    )

    assert response.status_code == 200, response.text
    assert response.json()["data"]["merchant_id"] == str(merchant_id)


async def test_device_ingest_rejects_spoofed_merchant(auth_client, db_session):
    plain_key, serial_number, _ = await _register_device(db_session)
    response = await auth_client.post(
        "/api/v1/edge/ingest/device",
        headers=_device_headers(plain_key, serial_number),
        json={
            "event_id": str(uuid.uuid4()),
            "merchant_id": str(uuid.uuid4()),
            "detections": [],
        },
    )

    assert response.status_code == 403


async def test_device_ingest_rejects_unregistered_device(auth_client, db_session):
    plain_key, _, _ = await _register_device(db_session)
    response = await auth_client.post(
        "/api/v1/edge/ingest/device",
        headers=_device_headers(plain_key, "unknown-device"),
        json={"event_id": str(uuid.uuid4()), "detections": []},
    )

    assert response.status_code == 403


async def test_device_ingest_rejects_replayed_nonce(auth_client, db_session):
    plain_key, serial_number, _ = await _register_device(db_session)
    headers = _device_headers(plain_key, serial_number, nonce=str(uuid.uuid4()))

    first = await auth_client.post(
        "/api/v1/edge/ingest/device",
        headers=headers,
        json={"event_id": str(uuid.uuid4()), "detections": []},
    )
    replay = await auth_client.post(
        "/api/v1/edge/ingest/device",
        headers=headers,
        json={"event_id": str(uuid.uuid4()), "detections": []},
    )

    assert first.status_code == 200, first.text
    assert replay.status_code == 401


async def test_device_heartbeat_updates_registered_device(auth_client, db_session):
    plain_key, serial_number, merchant_id = await _register_device(db_session)
    response = await auth_client.post(
        "/api/v1/edge/heartbeat",
        headers=_device_headers(plain_key, serial_number),
    )

    assert response.status_code == 200, response.text
    async with db_session() as db:
        device = await db.scalar(
            select(Device).where(
                Device.merchant_id == merchant_id,
                Device.serial_number == serial_number,
            )
        )
        assert device.last_heartbeat is not None


async def test_device_ingest_rejects_inactive_device(auth_client, db_session):
    plain_key, serial_number, _ = await _register_device(
        db_session, device_active=False
    )
    response = await auth_client.post(
        "/api/v1/edge/ingest/device",
        headers=_device_headers(plain_key, serial_number),
        json={"event_id": str(uuid.uuid4()), "detections": []},
    )

    assert response.status_code == 403


async def test_device_ingest_rejects_expired_timestamp(auth_client, db_session):
    plain_key, serial_number, _ = await _register_device(db_session)
    response = await auth_client.post(
        "/api/v1/edge/ingest/device",
        headers=_device_headers(
            plain_key, serial_number, timestamp=int(time.time()) - 301
        ),
        json={"event_id": str(uuid.uuid4()), "detections": []},
    )

    assert response.status_code == 401


async def test_device_ingest_rejects_missing_scope(auth_client, db_session):
    plain_key, serial_number, _ = await _register_device(
        db_session, scopes=["edge:heartbeat"]
    )
    response = await auth_client.post(
        "/api/v1/edge/ingest/device",
        headers=_device_headers(plain_key, serial_number),
        json={"event_id": str(uuid.uuid4()), "detections": []},
    )

    assert response.status_code == 403
