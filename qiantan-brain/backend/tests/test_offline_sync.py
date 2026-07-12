"""Unit tests for the offline sync service (no DB required)."""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture
def fake_models():
    """Return lightweight stand-ins for the two model classes."""

    class FakeInventoryRecord:
        _instances = []

        def __init__(self, **kwargs):
            self.id = kwargs.get("id", uuid.uuid4())
            self._data = kwargs
            FakeInventoryRecord._instances.append(self)

    class FakeProductCategory:
        _instances = []

        def __init__(self, **kwargs):
            self.id = kwargs.get("id", 42)
            self._data = kwargs
            FakeProductCategory._instances.append(self)

    FakeInventoryRecord._instances.clear()
    FakeProductCategory._instances.clear()
    return FakeInventoryRecord, FakeProductCategory


@pytest.fixture
def fake_session(fake_models):
    """Fake AsyncSession that records adds/flushes/scalars without a real DB."""
    FakeInventoryRecord, FakeProductCategory = fake_models
    sess = MagicMock()
    sess.added = []
    sess.flushed = 0
    sess.rolled_back = 0

    def add(obj):
        sess.added.append(obj)

    async def flush():
        sess.flushed += 1

    async def rollback():
        sess.rolled_back += 1

    async def scalar(query):
        # First call: look up existing InventoryRecord by idempotency_key.
        # Second call: look up ProductCategory by name.
        # We simulate no existing record and one category by default.
        return None

    sess.add.side_effect = add
    sess.flush.side_effect = flush
    sess.rollback.side_effect = rollback
    sess.scalar.side_effect = scalar
    return sess


def test_build_inventory_record_uses_quantity(fake_models):
    """Core record builder should map fields without DB."""
    FakeInventoryRecord, FakeProductCategory = fake_models
    merchant_id = uuid.uuid4()

    Item = MagicMock
    item = Item()
    item.idempotency_key = "pos-abc-123"
    item.event_type = "sale"
    item.product_name = None
    item.quantity = 5.0
    item.unit = "斤"
    item.unit_cost = None
    item.unit_price = 2.0
    item.total_amount = 10.0
    item.event_time = None
    item.notes = "test"
    item.source = "offline"

    with patch("app.services.offline_sync.InventoryRecord", FakeInventoryRecord):
        from app.services.offline_sync import _build_inventory_record

        record = _build_inventory_record(merchant_id, item, 7)

    assert record._data["merchant_id"] == merchant_id
    assert record._data["product_id"] == 7
    assert record._data["quantity"] == Decimal("5")
    assert record._data["total_amount"] == Decimal("10")
    assert record._data["idempotency_key"] == "pos-abc-123"


@pytest.mark.asyncio
async def test_offline_sync_endpoint_commits_and_is_idempotent(client, db_session):
    """A successful response must be durable, and a retry must not double-book."""
    from sqlalchemy import select

    from app.models.inventory import InventoryRecord

    payload = {
        "items": [
            {
                "idempotency_key": "offline-integration-001",
                "event_type": "sale",
                "product_name": "白菜",
                "quantity": 2,
                "unit": "斤",
                "unit_price": 3.5,
                "total_amount": 7,
                "source": "offline",
            }
        ]
    }
    first = await client.post("/api/v1/inventory/offline-sync", json=payload)
    assert first.status_code == 200
    assert first.json()["data"]["created"] == 1

    async with db_session() as session:
        rows = (
            (
                await session.execute(
                    select(InventoryRecord).where(
                        InventoryRecord.idempotency_key == "offline-integration-001"
                    )
                )
            )
            .scalars()
            .all()
        )
        assert len(rows) == 1

    retry = await client.post("/api/v1/inventory/offline-sync", json=payload)
    assert retry.status_code == 200
    assert retry.json()["data"]["duplicate"] == 1

    async with db_session() as session:
        rows = (
            (
                await session.execute(
                    select(InventoryRecord).where(
                        InventoryRecord.idempotency_key == "offline-integration-001"
                    )
                )
            )
            .scalars()
            .all()
        )
        assert len(rows) == 1


@pytest.mark.asyncio
async def test_same_idempotency_key_is_allowed_for_different_merchants(client, db_session):
    """Client-generated keys are unique inside a merchant, not globally."""
    from sqlalchemy import select

    from app.models.inventory import InventoryRecord

    shared_key = "offline-shared-device-sequence-001"
    payload = {
        "items": [
            {
                "idempotency_key": shared_key,
                "event_type": "sale",
                "product_name": "白菜",
                "quantity": 1,
                "unit": "斤",
                "unit_price": 3.5,
                "total_amount": 3.5,
                "source": "offline",
            }
        ]
    }
    first = await client.post("/api/v1/inventory/offline-sync", json=payload)
    second_merchant_id = "00000000-0000-0000-0000-000000000002"
    second = await client.post(
        "/api/v1/inventory/offline-sync",
        json=payload,
        headers={"X-Test-Merchant-Id": second_merchant_id},
    )

    assert first.status_code == 200
    assert first.json()["data"]["created"] == 1
    assert second.status_code == 200
    assert second.json()["data"]["created"] == 1

    async with db_session() as session:
        rows = (
            (
                await session.execute(
                    select(InventoryRecord).where(InventoryRecord.idempotency_key == shared_key)
                )
            )
            .scalars()
            .all()
        )
        assert len(rows) == 2
        assert {str(row.merchant_id) for row in rows} == {
            "00000000-0000-0000-0000-000000000001",
            second_merchant_id,
        }
