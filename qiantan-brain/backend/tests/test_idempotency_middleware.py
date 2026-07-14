"""Integration tests for persistent Idempotency-Key handling."""

import pytest
from sqlalchemy import func, select

from app.models.feedback import MerchantFeedback


pytestmark = pytest.mark.asyncio


async def _login(client, monkeypatch, openid: str) -> str:
    async def fake_code2session(code: str) -> str:
        return openid

    monkeypatch.setattr("app.routers.auth.wechat_code2session", fake_code2session)
    response = await client.post(
        "/api/v1/auth/wechat-login",
        json={"code": openid},
    )
    assert response.status_code == 200
    return response.json()["data"]["token"]


async def test_same_key_returns_first_result_without_duplicate(
    auth_client,
    db_session,
    monkeypatch,
):
    token = await _login(auth_client, monkeypatch, "idempotency-one")
    headers = {
        "Authorization": f"Bearer {token}",
        "Idempotency-Key": "feedback-retry-0001",
    }
    payload = {"content": "同一条反馈只保存一次"}

    first = await auth_client.post("/api/v1/feedback", json=payload, headers=headers)
    second = await auth_client.post("/api/v1/feedback", json=payload, headers=headers)

    assert first.status_code == 200
    assert second.status_code == 200
    assert second.json() == first.json()
    async with db_session() as session:
        count = await session.scalar(select(func.count(MerchantFeedback.id)))
    assert count == 1


async def test_same_key_with_different_body_returns_conflict(
    auth_client,
    monkeypatch,
):
    token = await _login(auth_client, monkeypatch, "idempotency-conflict")
    headers = {
        "Authorization": f"Bearer {token}",
        "Idempotency-Key": "feedback-retry-0002",
    }

    first = await auth_client.post(
        "/api/v1/feedback",
        json={"content": "第一次内容"},
        headers=headers,
    )
    second = await auth_client.post(
        "/api/v1/feedback",
        json={"content": "第二次内容"},
        headers=headers,
    )

    assert first.status_code == 200
    assert second.status_code == 409
    assert "不同请求" in second.json()["message"]


async def test_same_key_is_scoped_by_authenticated_principal(
    auth_client,
    monkeypatch,
):
    first_token = await _login(auth_client, monkeypatch, "idempotency-merchant-a")
    second_token = await _login(auth_client, monkeypatch, "idempotency-merchant-b")
    key = "feedback-retry-shared"
    payload = {"content": "不同商户可以使用相同幂等键"}

    first = await auth_client.post(
        "/api/v1/feedback",
        json=payload,
        headers={"Authorization": f"Bearer {first_token}", "Idempotency-Key": key},
    )
    second = await auth_client.post(
        "/api/v1/feedback",
        json=payload,
        headers={"Authorization": f"Bearer {second_token}", "Idempotency-Key": key},
    )

    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json()["data"]["feedback_id"] != second.json()["data"]["feedback_id"]
