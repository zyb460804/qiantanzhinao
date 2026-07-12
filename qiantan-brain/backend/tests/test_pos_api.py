"""Integration tests for the POS -> inventory -> accounts -> settlement loop."""

from __future__ import annotations

import uuid
from decimal import Decimal

import pytest
from sqlalchemy import func, select
from tests.conftest import TEST_MERCHANT_ID

from app.core.timezone import utc_now
from app.models.accounts import CustomerReceivable
from app.models.batch import BatchLifecycle
from app.models.inventory import InventoryRecord
from app.models.pos import Payment, SaleOrder
from app.services.batch import create_batch


async def _seed_stock(db_session, merchant_id: str = TEST_MERCHANT_ID, quantity: int = 10):
    async with db_session() as session:
        await create_batch(
            session,
            uuid.UUID(merchant_id),
            1,
            "白菜",
            f"白菜-pos-{merchant_id[-4:]}",
            Decimal(str(quantity)),
        )
        await session.commit()


def _order_payload(client_id: str, **overrides):
    payload = {
        "client_id": client_id,
        "payment_method": "cash",
        "items": [{"product_id": 1, "quantity": 2, "unit": "斤", "unit_price": 3.5}],
    }
    payload.update(overrides)
    return payload


@pytest.mark.asyncio
async def test_paid_order_consumes_stock_and_is_idempotent(client, db_session):
    await _seed_stock(db_session)
    payload = _order_payload("pos-test-paid-001")
    first = await client.post("/api/v1/pos/orders", json=payload)
    retry = await client.post("/api/v1/pos/orders", json=payload)
    assert first.status_code == 200
    assert first.json()["data"]["status"] == "paid"
    assert first.json()["data"]["total_amount"] == 7.0
    assert retry.status_code == 200
    assert retry.json()["data"]["duplicate"] is True
    async with db_session() as session:
        assert await session.scalar(select(func.count(SaleOrder.id))) == 1
        sale_records = (
            (
                await session.execute(
                    select(InventoryRecord).where(InventoryRecord.event_type == "sale")
                )
            )
            .scalars()
            .all()
        )
        payments = (await session.execute(select(Payment))).scalars().all()
        batch = (await session.execute(select(BatchLifecycle))).scalar_one()
        assert len(sale_records) == 1 and float(sale_records[0].quantity) == -2
        assert len(payments) == 1 and float(payments[0].amount) == 7
        assert float(batch.remaining_qty) == 8


@pytest.mark.asyncio
async def test_credit_order_creates_receivable_and_partial_repayment(client, db_session):
    await _seed_stock(db_session)
    create = await client.post(
        "/api/v1/pos/orders",
        json=_order_payload(
            "pos-test-credit-001", payment_method="credit", customer_name="张记饭店"
        ),
    )
    assert create.status_code == 200
    assert create.json()["data"]["status"] == "credit"
    order_id = create.json()["data"]["order_id"]
    repayment = await client.post(
        f"/api/v1/pos/orders/{order_id}/pay",
        json={"amount": 3, "method": "wechat", "transaction_id": "wx-pos-credit-001"},
    )
    assert repayment.status_code == 200
    assert repayment.json()["data"]["status"] == "partial"
    assert repayment.json()["data"]["remaining_amount"] == 4.0
    async with db_session() as session:
        entries = (
            (
                await session.execute(
                    select(CustomerReceivable)
                    .where(CustomerReceivable.customer_name == "张记饭店")
                    .order_by(CustomerReceivable.created_at)
                )
            )
            .scalars()
            .all()
        )
        assert [(entry.direction, float(entry.amount)) for entry in entries] == [
            ("charge", 7.0),
            ("repay", 3.0),
        ]


@pytest.mark.asyncio
async def test_insufficient_stock_rejects_entire_order(client, db_session):
    await _seed_stock(db_session, quantity=1)
    response = await client.post("/api/v1/pos/orders", json=_order_payload("pos-test-short-001"))
    assert response.status_code == 409
    async with db_session() as session:
        assert await session.scalar(select(func.count(SaleOrder.id))) == 0
        assert (
            await session.scalar(
                select(func.count(InventoryRecord.id)).where(InventoryRecord.event_type == "sale")
            )
            == 0
        )
        batch = (await session.execute(select(BatchLifecycle))).scalar_one()
        assert float(batch.remaining_qty) == 1


@pytest.mark.asyncio
async def test_daily_settlement_breaks_down_payment_channels_and_recloses(client, db_session):
    await _seed_stock(db_session, quantity=20)
    cash = await client.post("/api/v1/pos/orders", json=_order_payload("pos-settle-cash-001"))
    wechat = await client.post(
        "/api/v1/pos/orders", json=_order_payload("pos-settle-wechat-001", payment_method="wechat")
    )
    assert cash.status_code == 200 and wechat.status_code == 200
    settle_date = utc_now().date().isoformat()
    first = await client.post(f"/api/v1/pos/daily-settlement/{settle_date}/close")
    second = await client.post(f"/api/v1/pos/daily-settlement/{settle_date}/close")
    assert first.status_code == 200 and second.status_code == 200
    data = second.json()["data"]
    assert data["order_count"] == 2
    assert data["total_sales"] == 14.0
    assert data["cash_amount"] == 7.0
    assert data["wechat_amount"] == 7.0
    assert data["diff_amount"] == 0.0
