"""V3-H1：员工表 role='owner' 行在 staff_login 被应用层断言直接拒绝。

staff CRUD 已禁止创建/修改 owner 角色、迁移 n6d7e8f9a0b1 清理存量、seed
不再生成 —— 但「未来任何写入路径」（脚本 / 手工 SQL / 新路由）复活该角色时，
登录入口仍是不签发 owner 全权限员工 token 的最后防线。
"""

from __future__ import annotations

import uuid

import pytest
from tests.conftest import TEST_MERCHANT_ID

from app.models.staff import StaffMember


pytestmark = pytest.mark.asyncio


async def test_owner_role_staff_login_rejected_with_correct_pin(client, db_session):
    """直插 role='owner' 员工行（绕过 CRUD 校验）+ 正确 PIN → 403，不发 token。"""
    async with db_session() as session:
        staff = StaffMember(
            merchant_id=uuid.UUID(TEST_MERCHANT_ID),
            name="越权owner行",
            role="owner",
            pin_code="4321",
            is_active=True,
        )
        session.add(staff)
        await session.commit()
        staff_id = staff.id

    res = await client.post(
        "/api/v1/staff/login", json={"staff_id": str(staff_id), "pin_code": "4321"}
    )
    assert res.status_code == 403, res.text
    assert "owner" in res.json()["detail"]
    assert "token" not in res.json().get("data", {})


async def test_manager_role_staff_login_still_works(client, db_session):
    """回归保护：普通角色（manager）PIN 登录不受 owner 断言影响。"""
    async with db_session() as session:
        staff = StaffMember(
            merchant_id=uuid.UUID(TEST_MERCHANT_ID),
            name="正常经理",
            role="manager",
            pin_code="1357",
            is_active=True,
        )
        session.add(staff)
        await session.commit()
        staff_id = staff.id

    res = await client.post(
        "/api/v1/staff/login", json={"staff_id": str(staff_id), "pin_code": "1357"}
    )
    assert res.status_code == 200, res.text
    assert res.json()["data"]["token"]
    assert res.json()["data"]["role"] == "manager"
