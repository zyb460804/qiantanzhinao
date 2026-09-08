"""Security regression tests for the 2026-08-16 adversarial audit fixes.

These tests pin the explicitly requested security behavior that the existing
adversarial audit file may still encode with old/contradictory setup assertions.
"""

from __future__ import annotations

import uuid

import pytest
from tests.conftest import TEST_MERCHANT_ID


pytestmark = pytest.mark.asyncio


async def _create_staff(db_session, role: str = "cashier") -> str:
    from app.models.staff import StaffMember

    async with db_session() as session:
        staff = StaffMember(
            merchant_id=uuid.UUID(TEST_MERCHANT_ID),
            name=f"安全测试-{role}",
            role=role,
        )
        session.add(staff)
        await session.commit()
        await session.refresh(staff)
        return str(staff.id)


class TestStaffMarketAdminRoleGate:
    async def test_owner_cannot_create_market_admin(self, client):
        res = await client.post(
            "/api/v1/staff",
            json={"name": "自封管理员", "role": "market_admin"},
        )
        assert res.status_code == 403
        assert "租户/平台管理员" in res.json()["detail"]

    async def test_owner_cannot_update_staff_to_market_admin(self, client, db_session):
        staff_id = await _create_staff(db_session, "cashier")
        res = await client.put(
            f"/api/v1/staff/{staff_id}",
            json={"role": "market_admin"},
        )
        assert res.status_code == 403
        assert "租户/平台管理员" in res.json()["detail"]

    async def test_tenant_admin_can_create_market_admin(self, client):
        res = await client.post(
            "/api/v1/staff",
            json={"name": "合法市场管理员", "role": "market_admin"},
            headers={"X-Test-Token-Role": "tenant_admin"},
        )
        assert res.status_code == 200, res.text
        assert res.json()["data"]["role"] == "market_admin"
