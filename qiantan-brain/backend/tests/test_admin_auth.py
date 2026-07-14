"""Administrator browser-cookie and Bearer compatibility tests."""

import pytest

from app.core.admin_security import hash_password
from app.models.saas import PlatformAdmin


pytestmark = pytest.mark.asyncio


async def _seed_admin(db_session, email: str = "admin@example.com") -> None:
    async with db_session() as db:
        db.add(
            PlatformAdmin(
                email=email,
                password_hash=hash_password("StrongPass123!"),
                name="测试管理员",
                role="super_admin",
                is_active=True,
            )
        )
        await db.commit()


async def test_admin_cookie_login_me_and_logout(auth_client, db_session):
    await _seed_admin(db_session)

    login = await auth_client.post(
        "/api/admin/login",
        json={"email": "admin@example.com", "password": "StrongPass123!"},
    )
    assert login.status_code == 200, login.text
    assert "token" not in login.json()
    cookie = login.headers["set-cookie"].lower()
    assert "admin_session=" in cookie
    assert "httponly" in cookie
    assert "samesite=strict" in cookie
    assert "path=/api/admin" in cookie

    me = await auth_client.get("/api/admin/me")
    assert me.status_code == 200, me.text
    assert me.json()["email"] == "admin@example.com"

    logout = await auth_client.post("/api/admin/logout")
    assert logout.status_code == 200, logout.text
    cleared = logout.headers["set-cookie"].lower()
    assert "max-age=0" in cleared

    after_logout = await auth_client.get("/api/admin/me")
    assert after_logout.status_code == 401


async def test_admin_bearer_opt_in_remains_supported(auth_client, db_session):
    await _seed_admin(db_session, "api-admin@example.com")

    login = await auth_client.post(
        "/api/admin/login",
        headers={"X-Admin-Token-Response": "true"},
        json={"email": "api-admin@example.com", "password": "StrongPass123!"},
    )
    assert login.status_code == 200, login.text
    token = login.json()["token"]
    auth_client.cookies.clear()

    headers = {"Authorization": f"Bearer {token}"}
    me = await auth_client.get("/api/admin/me", headers=headers)
    assert me.status_code == 200, me.text

    logout = await auth_client.post("/api/admin/logout", headers=headers)
    assert logout.status_code == 200, logout.text
    revoked = await auth_client.get("/api/admin/me", headers=headers)
    assert revoked.status_code == 401


async def test_admin_logout_clears_invalid_cookie(auth_client):
    auth_client.cookies.set(
        "admin_session",
        "invalid-token",
        domain="test.local",
        path="/api/admin",
    )
    response = await auth_client.post("/api/admin/logout")
    assert response.status_code == 200
    assert "max-age=0" in response.headers["set-cookie"].lower()
