"""Tenant portal access and nullable tenant boundary regression tests."""

import uuid

from tests.conftest import TEST_MERCHANT_ID

from app.models.merchant import Merchant
from app.models.saas import Plan, Tenant


async def test_unbound_merchant_cannot_access_tenant_portal(client, db_session):
    async with db_session() as session:
        merchant = await session.get(Merchant, uuid.UUID(TEST_MERCHANT_ID))
        assert merchant is not None
        merchant.tenant_id = None
        merchant.role = "owner"
        await session.commit()

    response = await client.get("/api/tenant/usage/quotas")
    assert response.status_code == 403
    assert response.json()["detail"] == "商户未绑定租户"


async def test_bound_tenant_quota_service_receives_concrete_uuid(client, db_session):
    tenant_id = uuid.uuid4()
    plan_id = uuid.uuid4()
    async with db_session() as session:
        session.add(
            Plan(
                id=plan_id,
                code="tenant-portal-test",
                name="门户测试版",
                max_merchants=7,
                max_api_calls_monthly=1234,
                max_storage_mb=256,
            )
        )
        session.add(
            Tenant(
                id=tenant_id,
                name="门户测试租户",
                slug="tenant-portal-test",
                plan_id=plan_id,
            )
        )
        merchant = await session.get(Merchant, uuid.UUID(TEST_MERCHANT_ID))
        assert merchant is not None
        merchant.tenant_id = tenant_id
        merchant.role = "owner"
        await session.commit()

    response = await client.get("/api/tenant/usage/quotas")
    assert response.status_code == 200, response.text
    quotas = {item["metric"]: item for item in response.json()["quotas"]}
    assert quotas["api_calls"]["limit"] == 1234
    assert quotas["storage_mb"]["limit"] == 256
    assert quotas["merchant_count"]["limit"] == 7
