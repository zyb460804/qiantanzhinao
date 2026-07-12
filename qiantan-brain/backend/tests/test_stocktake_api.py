"""Integration tests for /api/v1/inventory/stocktake — full盘点 cycle.

Covers: start (with duplicate prevention), submit item (variance calc),
complete (adjustment generation + idempotency), history, and盘点 with
real inventory data (盘盈/盘亏).
"""

import sys
from pathlib import Path


sys.path.insert(0, str(Path(__file__).parent.parent))

import uuid
from datetime import datetime

import pytest
import pytest_asyncio
from sqlalchemy import select
from tests.conftest import TEST_MERCHANT_ID, TEST_PRODUCT_ID


pytestmark = pytest.mark.asyncio


async def _create_inventory_record(
    session, merchant_id, product_id, event_type, quantity, total_amount=None
):
    """Insert an InventoryRecord to set up book inventory."""
    from app.models.inventory import InventoryRecord

    mid = uuid.UUID(merchant_id) if isinstance(merchant_id, str) else merchant_id
    record = InventoryRecord(
        merchant_id=mid,
        product_id=product_id,
        quantity=quantity,
        unit="斤",
        total_amount=total_amount,
        event_type=event_type,
        event_time=datetime.now(),
    )
    session.add(record)
    await session.commit()


# ------------------------------------------------------------------
# POST /api/v1/inventory/stocktake/start
# ------------------------------------------------------------------


async def test_start_stocktake(client, db_session):
    """Start stocktake — returns session_id and book inventory for all products."""
    resp = await client.post(
        "/api/v1/inventory/stocktake/start",
        json={"merchant_id": TEST_MERCHANT_ID},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["code"] == 0
    assert "session_id" in data["data"]
    assert "items" in data["data"]
    # 4 seeded products, all with book_qty = 0
    assert len(data["data"]["items"]) == 4
    for item in data["data"]["items"]:
        assert item["book_qty"] == 0


async def test_start_stocktake_duplicate(client, db_session):
    """Starting a second盘点 while one is in_progress returns 400."""
    resp1 = await client.post(
        "/api/v1/inventory/stocktake/start",
        json={"merchant_id": TEST_MERCHANT_ID},
    )
    assert resp1.status_code == 200

    resp2 = await client.post(
        "/api/v1/inventory/stocktake/start",
        json={"merchant_id": TEST_MERCHANT_ID},
    )
    assert resp2.status_code == 400


# ------------------------------------------------------------------
# POST /api/v1/inventory/stocktake/{session_id}/submit
# ------------------------------------------------------------------


async def test_submit_item(client, db_session):
    """Submit actual count — variance is calculated correctly."""
    start_resp = await client.post(
        "/api/v1/inventory/stocktake/start",
        json={"merchant_id": TEST_MERCHANT_ID},
    )
    session_id = start_resp.json()["data"]["session_id"]

    resp = await client.post(
        f"/api/v1/inventory/stocktake/{session_id}/submit",
        json={"product_id": 1, "actual_qty": 5, "variance_reason": "称重误差"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["code"] == 0
    # book_qty = 0 (no inventory), actual = 5 → variance = 5
    assert data["data"]["book_qty"] == 0
    assert data["data"]["actual_qty"] == 5
    assert data["data"]["variance"] == 5


# ------------------------------------------------------------------
# POST /api/v1/inventory/stocktake/{session_id}/complete
# ------------------------------------------------------------------


async def test_complete_stocktake(client, db_session):
    """Complete盘点 — adjustment records generated for variances."""
    start_resp = await client.post(
        "/api/v1/inventory/stocktake/start",
        json={"merchant_id": TEST_MERCHANT_ID},
    )
    session_id = start_resp.json()["data"]["session_id"]

    # Submit item with variance (盘盈)
    await client.post(
        f"/api/v1/inventory/stocktake/{session_id}/submit",
        json={"product_id": 1, "actual_qty": 5},
    )

    resp = await client.post(f"/api/v1/inventory/stocktake/{session_id}/complete", json={})
    assert resp.status_code == 200
    data = resp.json()
    assert data["code"] == 0
    adjustments = data["data"]["adjustments"]
    assert len(adjustments) == 1
    assert adjustments[0]["variance"] == 5
    assert adjustments[0]["product_id"] == 1

    # Verify adjustment record in DB
    from app.models.inventory import InventoryRecord

    mid = uuid.UUID(TEST_MERCHANT_ID)
    async with db_session() as session:
        result = await session.execute(
            select(InventoryRecord).where(
                InventoryRecord.merchant_id == mid,
                InventoryRecord.event_type == "adjustment",
                InventoryRecord.source == "stocktake",
            )
        )
        records = result.scalars().all()
        assert len(records) == 1
        assert float(records[0].quantity) == 5


async def test_complete_empty_stocktake(client, db_session):
    """Completing without any submitted items returns 400."""
    start_resp = await client.post(
        "/api/v1/inventory/stocktake/start",
        json={"merchant_id": TEST_MERCHANT_ID},
    )
    session_id = start_resp.json()["data"]["session_id"]

    resp = await client.post(f"/api/v1/inventory/stocktake/{session_id}/complete", json={})
    assert resp.status_code == 400


async def test_complete_stocktake_idempotent(client, db_session):
    """Repeated complete returns original results, no duplicate adjustments."""
    start_resp = await client.post(
        "/api/v1/inventory/stocktake/start",
        json={"merchant_id": TEST_MERCHANT_ID},
    )
    session_id = start_resp.json()["data"]["session_id"]

    await client.post(
        f"/api/v1/inventory/stocktake/{session_id}/submit",
        json={"product_id": 1, "actual_qty": 5},
    )

    # First complete
    resp1 = await client.post(f"/api/v1/inventory/stocktake/{session_id}/complete", json={})
    assert resp1.status_code == 200
    assert len(resp1.json()["data"]["adjustments"]) == 1

    # Second complete — idempotent, returns empty adjustments
    resp2 = await client.post(f"/api/v1/inventory/stocktake/{session_id}/complete", json={})
    assert resp2.status_code == 200
    assert resp2.json()["code"] == 0
    assert len(resp2.json()["data"]["adjustments"]) == 0

    # Only one adjustment record in DB
    from app.models.inventory import InventoryRecord

    mid = uuid.UUID(TEST_MERCHANT_ID)
    async with db_session() as session:
        result = await session.execute(
            select(InventoryRecord).where(
                InventoryRecord.merchant_id == mid,
                InventoryRecord.event_type == "adjustment",
            )
        )
        records = result.scalars().all()
        assert len(records) == 1


# ------------------------------------------------------------------
# GET /api/v1/inventory/stocktake/history
# ------------------------------------------------------------------


async def test_stocktake_history(client, db_session):
    """Completed盘点 sessions appear in history."""
    start_resp = await client.post(
        "/api/v1/inventory/stocktake/start",
        json={"merchant_id": TEST_MERCHANT_ID},
    )
    session_id = start_resp.json()["data"]["session_id"]

    await client.post(
        f"/api/v1/inventory/stocktake/{session_id}/submit",
        json={"product_id": 1, "actual_qty": 0},
    )
    await client.post(f"/api/v1/inventory/stocktake/{session_id}/complete", json={})

    resp = await client.get(
        "/api/v1/inventory/stocktake/history",
        params={"merchant_id": TEST_MERCHANT_ID},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["code"] == 0
    assert len(data["data"]) >= 1
    entry = data["data"][0]
    assert entry["status"] == "completed"
    assert "started_at" in entry
    assert "completed_at" in entry


# ------------------------------------------------------------------
# 盘点 with real inventory data — 盘盈/盘亏
# ------------------------------------------------------------------


async def test_stocktake_with_actual_inventory(client, db_session):
    """盘点 with real stock — verify盘亏 (loss) detection."""
    mid = uuid.UUID(TEST_MERCHANT_ID)
    # Create inventory: purchase 10, sale 3 → book qty = 7
    async with db_session() as session:
        await _create_inventory_record(session, mid, 1, "purchase", quantity=10, total_amount=5.0)
        await _create_inventory_record(session, mid, 1, "sale", quantity=-3, total_amount=6.0)

    # Start盘点
    start_resp = await client.post(
        "/api/v1/inventory/stocktake/start",
        json={"merchant_id": TEST_MERCHANT_ID},
    )
    session_id = start_resp.json()["data"]["session_id"]

    # Verify book qty = 7
    items = start_resp.json()["data"]["items"]
    item1 = next(i for i in items if i["product_id"] == 1)
    assert item1["book_qty"] == 7

    # Actual count = 5 → 盘亏 (variance = -2)
    submit_resp = await client.post(
        f"/api/v1/inventory/stocktake/{session_id}/submit",
        json={"product_id": 1, "actual_qty": 5, "variance_reason": "自然损耗"},
    )
    assert submit_resp.json()["data"]["variance"] == -2

    # Complete — adjustment record with negative quantity
    complete_resp = await client.post(f"/api/v1/inventory/stocktake/{session_id}/complete", json={})
    assert complete_resp.status_code == 200
    adjustments = complete_resp.json()["data"]["adjustments"]
    assert len(adjustments) == 1
    assert adjustments[0]["variance"] == -2
    assert complete_resp.json()["data"]["total_variance"] == -2
