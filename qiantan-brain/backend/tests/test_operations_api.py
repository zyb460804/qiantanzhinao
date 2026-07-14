"""Tests for operations router — waste, clearance, customers, credit profiles.

Covers §4.8, §4.12 and §6: normal flow, boundary, auth, isolation.
"""

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select
from tests.conftest import TEST_MERCHANT_ID

from app.models.accounts import CustomerCreditProfile, CustomerReceivable
from app.models.batch import BatchLifecycle
from app.models.inventory import InventoryRecord
from app.services.batch import create_batch


class TestWasteRecording:
    async def test_list_waste_reasons(self, client):
        res = await client.get("/api/v1/ops/waste-reasons")
        assert res.status_code == 200
        reasons = res.json()["data"]
        assert len(reasons) >= 10
        assert "腐烂" in reasons
        assert "试吃" in reasons

    async def test_record_waste_product_not_found(self, client):
        res = await client.post(
            "/api/v1/ops/waste",
            json={
                "product_id": 9999,
                "quantity": 5,
                "reason": "腐烂",
            },
        )
        assert res.status_code == 404

    async def test_record_waste_zero_qty(self, client):
        res = await client.post(
            "/api/v1/ops/waste",
            json={
                "product_id": 1,
                "quantity": 0,
                "reason": "腐烂",
            },
        )
        assert res.status_code == 400

    async def test_record_waste_invalid_reason(self, client):
        res = await client.post(
            "/api/v1/ops/waste",
            json={
                "product_id": 1,
                "quantity": 5,
                "reason": "不存在的理由",
            },
        )
        assert res.status_code == 400

    async def test_record_waste_over_stock_rejected_without_partial_deduction(
        self, client, db_session
    ):
        async with db_session() as session:
            await create_batch(
                session,
                uuid.UUID(TEST_MERCHANT_ID),
                1,
                "白菜",
                "waste-limit-batch",
                Decimal("3"),
            )
            await session.commit()

        res = await client.post(
            "/api/v1/ops/waste",
            json={
                "product_id": 1,
                "quantity": 5,
                "reason": "腐烂",
            },
        )
        assert res.status_code == 409

        async with db_session() as session:
            batch = (
                await session.execute(
                    select(BatchLifecycle).where(BatchLifecycle.batch_label == "waste-limit-batch")
                )
            ).scalar_one()
            waste_records = (
                (
                    await session.execute(
                        select(InventoryRecord).where(InventoryRecord.event_type == "waste")
                    )
                )
                .scalars()
                .all()
            )
            assert float(batch.remaining_qty) == 3
            assert waste_records == []

    async def test_list_waste(self, client):
        res = await client.get("/api/v1/ops/waste")
        assert res.status_code == 200
        assert "data" in res.json()

    async def test_waste_analysis(self, client):
        res = await client.get("/api/v1/ops/waste/analysis?days=30")
        assert res.status_code == 200
        data = res.json()["data"]
        assert "by_reason" in data
        assert "by_product" in data
        assert data["period_days"] == 30


class TestClearance:
    async def test_expiry_clearance(self, client):
        res = await client.get("/api/v1/ops/expiry/clearance?within_hours=48")
        assert res.status_code == 200
        data = res.json()["data"]
        assert "items" in data
        assert data["within_hours"] == 48

    async def test_clearance_default_window(self, client):
        res = await client.get("/api/v1/ops/expiry/clearance")
        assert res.status_code == 200
        assert res.json()["data"]["within_hours"] == 24

    async def test_clearance_invalid_hours(self, client):
        res = await client.get("/api/v1/ops/expiry/clearance?within_hours=0")
        assert res.status_code in (422, 200)  # 0 may be clamped or rejected

    async def test_batch_promotion_does_not_change_sku_base_price(self, client, db_session):
        async with db_session() as session:
            batch = await create_batch(
                session,
                uuid.UUID(TEST_MERCHANT_ID),
                1,
                "白菜",
                "promotion-batch",
                Decimal("5"),
            )
            batch.expiry_date = datetime.now(UTC).replace(tzinfo=None) + timedelta(hours=12)
            await session.commit()
            batch_id = str(batch.id)

        response = await client.post(
            f"/api/v1/ops/expiry/clearance/{batch_id}/promotion",
            json={"promotion_price": 1.8, "end_at": batch.expiry_date.isoformat()},
        )
        assert response.status_code == 200
        assert "不会修改常规售价" in response.json()["message"]

        async with db_session() as session:
            saved = await session.get(BatchLifecycle, uuid.UUID(batch_id))
            assert float(saved.promotion_price) == 1.8
            assert saved.promotion_end_at is not None

    async def test_batch_promotion_rejects_end_after_expiry(self, client, db_session):
        async with db_session() as session:
            batch = await create_batch(
                session,
                uuid.UUID(TEST_MERCHANT_ID),
                1,
                "白菜",
                "promotion-expiry-boundary",
                Decimal("5"),
            )
            batch.expiry_date = datetime.now(UTC).replace(tzinfo=None) + timedelta(hours=12)
            await session.commit()
            batch_id = str(batch.id)
            expiry = batch.expiry_date

        response = await client.post(
            f"/api/v1/ops/expiry/clearance/{batch_id}/promotion",
            json={
                "promotion_price": 1.8,
                "end_at": (expiry + timedelta(hours=1)).isoformat(),
            },
        )
        assert response.status_code == 400
        assert "过期时间" in response.json()["detail"]


class TestCustomerListing:
    async def test_nullable_credit_days_and_naive_transaction_time(self, client, db_session):
        merchant_id = uuid.UUID(TEST_MERCHANT_ID)
        async with db_session() as session:
            session.add_all(
                [
                    CustomerCreditProfile(
                        merchant_id=merchant_id,
                        customer_name="默认账期客户",
                        credit_limit=Decimal("500"),
                        default_credit_days=None,
                    ),
                    CustomerReceivable(
                        merchant_id=merchant_id,
                        customer_name="默认账期客户",
                        direction="charge",
                        amount=Decimal("100"),
                        created_at=datetime.now(UTC).replace(tzinfo=None) - timedelta(days=45),
                    ),
                ]
            )
            await session.commit()

        response = await client.get("/api/v1/ops/customers")
        assert response.status_code == 200, response.text
        customer = next(
            item for item in response.json()["data"] if item["customer_name"] == "默认账期客户"
        )
        assert customer["overdue_days"] == 15
        assert customer["remaining_credit"] == 400

    async def test_list_customers(self, client):
        res = await client.get("/api/v1/ops/customers")
        assert res.status_code == 200
        data = res.json()
        assert data["code"] == 0

    async def test_customer_ledger_not_found_returns_empty(self, client):
        res = await client.get("/api/v1/ops/customers/nonexistent/ledger")
        assert res.status_code == 200
        data = res.json()["data"]
        assert data["balance"] == 0

    async def test_customer_repay_empty_name(self, client):
        res = await client.post(
            "/api/v1/ops/customers/repay",
            json={
                "customer_name": "",
                "amount": 100,
            },
        )
        assert res.status_code == 400

    async def test_customer_repay_zero_amount(self, client):
        res = await client.post(
            "/api/v1/ops/customers/repay",
            json={
                "customer_name": "张记饭店",
                "amount": 0,
            },
        )
        assert res.status_code == 400

    async def test_customer_repay_without_debt_rejected(self, client):
        res = await client.post(
            "/api/v1/ops/customers/repay",
            json={
                "customer_name": "无欠款客户",
                "amount": 10,
            },
        )
        assert res.status_code == 400
        assert "没有欠款" in res.json()["detail"]


class TestCreditProfiles:
    """§4.8: 客户信用档案 — 创建、查询、信用检查。"""

    async def test_get_default_profile(self, client):
        res = await client.get("/api/v1/ops/customers/test-customer/credit-profile")
        assert res.status_code == 200
        data = res.json()["data"]
        assert data["customer_name"] == "test-customer"
        assert data["is_default"] == True  # noqa: E712
        assert data["is_blocked"] == False  # noqa: E712

    async def test_create_credit_profile(self, client):
        res = await client.post(
            "/api/v1/ops/customers/credit-profile",
            json={
                "customer_name": "张记饭店",
                "credit_limit": 5000,
                "default_credit_days": 30,
            },
        )
        assert res.status_code == 200

        res2 = await client.get("/api/v1/ops/customers/张记饭店/credit-profile")
        assert res2.status_code == 200
        data = res2.json()["data"]
        assert data["credit_limit"] == 5000
        assert data["default_credit_days"] == 30
        assert "id" in data  # has DB record, not default

    async def test_update_credit_profile(self, client):
        await client.post(
            "/api/v1/ops/customers/credit-profile",
            json={
                "customer_name": "老客户",
                "credit_limit": 3000,
            },
        )
        res = await client.post(
            "/api/v1/ops/customers/credit-profile",
            json={
                "customer_name": "老客户",
                "credit_limit": 8000,
            },
        )
        assert res.status_code == 200

        res2 = await client.get("/api/v1/ops/customers/老客户/credit-profile")
        assert res2.json()["data"]["credit_limit"] == 8000

    async def test_block_customer(self, client):
        await client.post(
            "/api/v1/ops/customers/credit-profile",
            json={
                "customer_name": "风险客户",
                "is_blocked": True,
                "block_reason": "多次逾期",
            },
        )
        res = await client.get("/api/v1/ops/customers/风险客户/credit-profile")
        assert res.json()["data"]["is_blocked"] == True  # noqa: E712

    async def test_check_credit_allowed(self, client):
        res = await client.post(
            "/api/v1/ops/customers/check-credit",
            json={
                "customer_name": "新客户",
                "amount": 100,
            },
        )
        assert res.status_code == 200
        assert res.json()["data"]["allowed"] == True  # noqa: E712

    async def test_check_credit_blocked(self, client):
        await client.post(
            "/api/v1/ops/customers/credit-profile",
            json={
                "customer_name": "黑名单客户",
                "is_blocked": True,
                "block_reason": "长期欠款",
            },
        )
        res = await client.post(
            "/api/v1/ops/customers/check-credit",
            json={
                "customer_name": "黑名单客户",
                "amount": 100,
            },
        )
        assert res.status_code == 200
        assert res.json()["data"]["allowed"] == False  # noqa: E712

    async def test_create_profile_empty_name(self, client):
        res = await client.post(
            "/api/v1/ops/customers/credit-profile",
            json={
                "customer_name": "",
            },
        )
        assert res.status_code == 400


class TestUnauthenticated:
    async def test_waste_no_auth(self, auth_client):
        res = await auth_client.post(
            "/api/v1/ops/waste",
            json={
                "product_id": 1,
                "quantity": 1,
                "reason": "腐烂",
            },
        )
        assert res.status_code == 401

    async def test_clearance_no_auth(self, auth_client):
        res = await auth_client.get("/api/v1/ops/expiry/clearance")
        assert res.status_code == 401

    async def test_customers_no_auth(self, auth_client):
        res = await auth_client.get("/api/v1/ops/customers")
        assert res.status_code == 401

    async def test_credit_profile_no_auth(self, auth_client):
        res = await auth_client.post(
            "/api/v1/ops/customers/credit-profile",
            json={
                "customer_name": "test",
            },
        )
        assert res.status_code == 401


class TestExport:
    async def test_export_sales_csv(self, client):
        res = await client.get("/api/v1/ops/export/sales?start_date=2026-01-01&end_date=2026-07-12")
        assert res.status_code == 200
        assert "text/csv" in res.headers.get("content-type", "")

    async def test_export_inventory_csv(self, client):
        res = await client.get("/api/v1/ops/export/inventory")
        assert res.status_code == 200

    async def test_export_waste_csv(self, client):
        res = await client.get("/api/v1/ops/export/waste?start_date=2026-01-01&end_date=2026-07-12")
        assert res.status_code == 200

    async def test_export_accounts_csv(self, client):
        res = await client.get("/api/v1/ops/export/accounts")
        assert res.status_code == 200
