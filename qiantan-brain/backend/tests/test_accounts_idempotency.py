"""Tests for merchant-scoped account ledger idempotency."""

from __future__ import annotations

import uuid
from decimal import Decimal

import pytest
from sqlalchemy import func, select
from tests.conftest import TEST_MERCHANT_ID

from app.models.accounts import CustomerReceivable
from app.models.merchant import Merchant


@pytest.mark.asyncio
async def test_customer_receivable_idempotency_is_scoped_per_merchant(db_session):
    other_merchant_id = uuid.UUID("00000000-0000-0000-0000-000000000002")
    shared_key = "customer-ledger-shared-key"

    async with db_session() as session:
        session.add(Merchant(id=other_merchant_id, name="另一摊位", business_type="蔬菜"))
        session.add_all(
            [
                CustomerReceivable(
                    merchant_id=uuid.UUID(TEST_MERCHANT_ID),
                    customer_name="张记饭店",
                    direction="charge",
                    amount=Decimal("80.00"),
                    idempotency_key=shared_key,
                ),
                CustomerReceivable(
                    merchant_id=other_merchant_id,
                    customer_name="张记饭店",
                    direction="charge",
                    amount=Decimal("50.00"),
                    idempotency_key=shared_key,
                ),
            ]
        )
        await session.commit()

        count = await session.scalar(
            select(func.count(CustomerReceivable.id)).where(
                CustomerReceivable.idempotency_key == shared_key
            )
        )
        assert count == 2
