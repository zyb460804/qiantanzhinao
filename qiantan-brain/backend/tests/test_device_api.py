"""设备管理接口输入校验、租户隔离和状态转换回归测试。"""

import uuid

import pytest


pytestmark = pytest.mark.asyncio


async def test_register_rejects_unknown_device_type(client):
    res = await client.post("/api/v1/devices", json={
        "device_type": "unknown",
        "device_name": "异常设备",
    })
    assert res.status_code == 422


async def test_register_rejects_blank_device_name(client):
    res = await client.post("/api/v1/devices", json={
        "device_type": "scale",
        "device_name": "   ",
    })
    assert res.status_code == 422


async def test_duplicate_serial_rejected_within_merchant(client):
    payload = {
        "device_type": "scale",
        "device_name": "一号秤",
        "serial_number": "SN-001",
    }
    first = await client.post("/api/v1/devices", json=payload)
    assert first.status_code == 200

    payload["device_name"] = "二号秤"
    second = await client.post("/api/v1/devices", json=payload)
    assert second.status_code == 409


async def test_same_serial_allowed_for_different_merchants(client):
    payload = {
        "device_type": "printer",
        "device_name": "票据打印机",
        "serial_number": "SHARED-SN",
    }
    first = await client.post("/api/v1/devices", json=payload)
    assert first.status_code == 200

    second = await client.post(
        "/api/v1/devices",
        json=payload,
        headers={"X-Test-Merchant-Id": "00000000-0000-0000-0000-000000000002"},
    )
    assert second.status_code == 200


async def test_deactivated_device_cannot_heartbeat(client):
    created = await client.post("/api/v1/devices", json={
        "device_type": "camera",
        "device_name": "入口摄像头",
    })
    device_id = created.json()["data"]["device_id"]
    assert (await client.delete(f"/api/v1/devices/{device_id}")).status_code == 200

    heartbeat = await client.post(
        f"/api/v1/devices/{device_id}/heartbeat",
        json={},
    )
    assert heartbeat.status_code == 409


async def test_heartbeat_payload_length_is_validated(client):
    created = await client.post("/api/v1/devices", json={
        "device_type": "esl",
        "device_name": "电子价签",
    })
    device_id = created.json()["data"]["device_id"]
    res = await client.post(
        f"/api/v1/devices/{device_id}/heartbeat",
        json={"error": "x" * 201},
    )
    assert res.status_code == 422


async def test_price_sync_rejects_invalid_sku_uuid(client):
    res = await client.post(
        "/api/v1/devices/price-display/sync",
        json={"sku_ids": ["not-a-uuid"]},
    )
    assert res.status_code == 422
