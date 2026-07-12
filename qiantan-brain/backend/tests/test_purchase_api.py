"""Integration tests for /api/v1/purchase — AI recommendation to inventory loop.

Covers: from-advice generation, today list retrieval, item update/cancel
with merchant_id query param (multi-tenant isolation), batch confirm with
idempotency.
"""

import sys
from pathlib import Path


sys.path.insert(0, str(Path(__file__).parent.parent))

import uuid

import pytest
import pytest_asyncio
from sqlalchemy import select
from tests.conftest import TEST_MERCHANT_ID, TEST_PRODUCT_ID


SECOND_MERCHANT_ID = "00000000-0000-0000-0000-000000000002"

pytestmark = pytest.mark.asyncio


async def _create_recommendation(session, merchant_id, product_id):
    """Insert a Recommendation record for generating purchase lists."""
    from app.models.recommendation import Recommendation

    mid = uuid.UUID(merchant_id) if isinstance(merchant_id, str) else merchant_id
    rec = Recommendation(
        merchant_id=mid,
        product_id=product_id,
        suggestion=f"建议采购{product_id}",
        basis=[],
        recommended_qty=20,
        confidence=0.8,
    )
    session.add(rec)
    await session.commit()
    return rec


# ------------------------------------------------------------------
# POST /api/v1/purchase/from-advice
# ------------------------------------------------------------------


async def test_create_from_advice(client, db_session):
    """Generate purchase list from today's recommendations."""
    mid = uuid.UUID(TEST_MERCHANT_ID)
    async with db_session() as session:
        await _create_recommendation(session, mid, 1)
        await _create_recommendation(session, mid, 2)

    resp = await client.post(
        "/api/v1/purchase/from-advice",
        json={"merchant_id": TEST_MERCHANT_ID},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["code"] == 0
    assert data["data"]["item_count"] == 2
    assert "list_id" in data["data"]


async def test_create_from_manual_calendar_items(client):
    """Calendar items are matched by product name and become editable purchase items."""
    resp = await client.post(
        "/api/v1/purchase/from-advice",
        json={"items": [{"name": "白菜", "qty": 5, "unit": "斤", "from": "时令建议"}]},
    )
    assert resp.status_code == 200
    payload = resp.json()["data"]
    assert payload["added_count"] == 1
    assert payload["item_count"] == 1
    assert payload["unmatched_items"] == []

    today = await client.get("/api/v1/purchase/today")
    item = today.json()["data"]["items"][0]
    assert item["product_name"] == "白菜"
    assert item["actual_qty"] == 5.0
    assert item["reason"] == "时令建议"


async def test_manual_calendar_items_do_not_duplicate_existing_product(client):
    """Repeated calendar import succeeds idempotently without duplicate rows."""
    body = {"items": [{"product_id": 1, "name": "白菜", "qty": 5, "unit": "斤"}]}
    first = await client.post("/api/v1/purchase/from-advice", json=body)
    second = await client.post("/api/v1/purchase/from-advice", json=body)
    assert first.status_code == 200
    assert second.status_code == 200
    assert second.json()["data"]["added_count"] == 0
    assert second.json()["data"]["item_count"] == 1


async def test_manual_calendar_items_report_unmatched_names(client):
    """Matched items are imported while unknown names are returned to the caller."""
    resp = await client.post(
        "/api/v1/purchase/from-advice",
        json={"items": [
            {"name": "白菜", "qty": 5, "unit": "斤"},
            {"name": "不存在的时令菜", "qty": 3, "unit": "斤"},
        ]},
    )
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["added_count"] == 1
    assert data["unmatched_items"] == ["不存在的时令菜"]


async def test_manual_calendar_items_keep_draft_when_nothing_matches(client):
    """An all-unmatched import fails clearly so the mini-program retains its local draft."""
    resp = await client.post(
        "/api/v1/purchase/from-advice",
        json={"items": [{"name": "不存在的时令菜", "qty": 3, "unit": "斤"}]},
    )
    assert resp.status_code == 400
    assert "商品目录中未找到" in resp.json()["detail"]


# ------------------------------------------------------------------
# GET /api/v1/purchase/today
# ------------------------------------------------------------------


async def test_get_today_list_empty(client, db_session):
    """No purchase list → data is null."""
    resp = await client.get("/api/v1/purchase/today", params={"merchant_id": TEST_MERCHANT_ID})
    assert resp.status_code == 200
    data = resp.json()
    assert data["code"] == 0
    assert data["data"] is None


async def test_get_today_list_with_data(client, db_session):
    """Purchase list exists → returns items."""
    mid = uuid.UUID(TEST_MERCHANT_ID)
    async with db_session() as session:
        await _create_recommendation(session, mid, 1)

    await client.post("/api/v1/purchase/from-advice", json={"merchant_id": TEST_MERCHANT_ID})

    resp = await client.get("/api/v1/purchase/today", params={"merchant_id": TEST_MERCHANT_ID})
    assert resp.status_code == 200
    data = resp.json()
    assert data["code"] == 0
    assert data["data"] is not None
    assert len(data["data"]["items"]) == 1
    item = data["data"]["items"][0]
    assert item["product_id"] == 1
    assert item["product_name"] == "白菜"
    assert item["recommended_qty"] == 20.0


# ------------------------------------------------------------------
# PUT /api/v1/purchase/item/{item_id}?merchant_id=xxx
# ------------------------------------------------------------------


async def test_update_item(client, db_session):
    """Modify purchase quantity via PUT with merchant_id query param."""
    mid = uuid.UUID(TEST_MERCHANT_ID)
    async with db_session() as session:
        await _create_recommendation(session, mid, 1)

    await client.post("/api/v1/purchase/from-advice", json={"merchant_id": TEST_MERCHANT_ID})

    today = await client.get("/api/v1/purchase/today", params={"merchant_id": TEST_MERCHANT_ID})
    item_id = today.json()["data"]["items"][0]["item_id"]

    resp = await client.put(
        f"/api/v1/purchase/item/{item_id}",
        params={"merchant_id": TEST_MERCHANT_ID},
        json={"actual_qty": 15, "actual_unit_cost": 0.8},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["code"] == 0
    assert data["data"]["actual_qty"] == 15.0
    assert data["data"]["actual_cost"] == 12.0  # 15 × 0.8
    # deviation_ratio = (15 - 20) / 20 × 100 = -25.0
    assert data["data"]["deviation_ratio"] == -25.0


async def test_update_item_wrong_merchant(client, db_session):
    """Accessing another merchant's item returns 404 (tenant isolation)."""
    mid = uuid.UUID(TEST_MERCHANT_ID)
    async with db_session() as session:
        await _create_recommendation(session, mid, 1)

    await client.post("/api/v1/purchase/from-advice", json={"merchant_id": TEST_MERCHANT_ID})

    today = await client.get("/api/v1/purchase/today", params={"merchant_id": TEST_MERCHANT_ID})
    item_id = today.json()["data"]["items"][0]["item_id"]

    resp = await client.put(
        f"/api/v1/purchase/item/{item_id}",
        headers={"X-Test-Merchant-Id": SECOND_MERCHANT_ID},
        json={"actual_qty": 15},
    )
    assert resp.status_code == 404


# ------------------------------------------------------------------
# DELETE /api/v1/purchase/item/{item_id}?merchant_id=xxx
# ------------------------------------------------------------------


async def test_cancel_item(client, db_session):
    """Cancel a purchase item (soft-delete via status=cancelled)."""
    mid = uuid.UUID(TEST_MERCHANT_ID)
    async with db_session() as session:
        await _create_recommendation(session, mid, 1)

    await client.post("/api/v1/purchase/from-advice", json={"merchant_id": TEST_MERCHANT_ID})

    today = await client.get("/api/v1/purchase/today", params={"merchant_id": TEST_MERCHANT_ID})
    item_id = today.json()["data"]["items"][0]["item_id"]

    resp = await client.delete(
        f"/api/v1/purchase/item/{item_id}",
        params={"merchant_id": TEST_MERCHANT_ID},
    )
    assert resp.status_code == 200
    assert resp.json()["code"] == 0


# ------------------------------------------------------------------
# POST /api/v1/purchase/{list_id}/confirm
# ------------------------------------------------------------------


async def test_confirm_purchase(client, db_session):
    """Confirm purchase — generates inventory record + batch."""
    mid = uuid.UUID(TEST_MERCHANT_ID)
    async with db_session() as session:
        await _create_recommendation(session, mid, 1)

    create_resp = await client.post(
        "/api/v1/purchase/from-advice", json={"merchant_id": TEST_MERCHANT_ID}
    )
    list_id = create_resp.json()["data"]["list_id"]

    resp = await client.post(f"/api/v1/purchase/{list_id}/confirm", json={})
    assert resp.status_code == 200
    data = resp.json()
    assert data["code"] == 0
    assert data["data"]["confirmed_count"] == 1
    assert len(data["data"]["records"]) == 1

    # Verify inventory record was created
    from app.models.batch import BatchLifecycle
    from app.models.inventory import InventoryRecord

    async with db_session() as session:
        rec_result = await session.execute(
            select(InventoryRecord).where(
                InventoryRecord.merchant_id == mid,
                InventoryRecord.source == "purchase_list",
            )
        )
        records = rec_result.scalars().all()
        assert len(records) == 1
        assert records[0].event_type == "purchase"

        # Verify batch was created
        batch_result = await session.execute(
            select(BatchLifecycle).where(
                BatchLifecycle.merchant_id == mid,
                BatchLifecycle.product_id == 1,
            )
        )
        batches = batch_result.scalars().all()
        assert len(batches) == 1


async def test_confirm_purchase_idempotent(client, db_session):
    """Repeated confirm must not create duplicate inventory records."""
    mid = uuid.UUID(TEST_MERCHANT_ID)
    async with db_session() as session:
        await _create_recommendation(session, mid, 1)

    create_resp = await client.post(
        "/api/v1/purchase/from-advice", json={"merchant_id": TEST_MERCHANT_ID}
    )
    list_id = create_resp.json()["data"]["list_id"]

    # First confirm
    resp1 = await client.post(f"/api/v1/purchase/{list_id}/confirm", json={})
    assert resp1.status_code == 200
    assert resp1.json()["data"]["confirmed_count"] == 1

    # Second confirm — idempotent
    resp2 = await client.post(f"/api/v1/purchase/{list_id}/confirm", json={})
    assert resp2.status_code == 200
    assert resp2.json()["code"] == 0

    # Only one inventory record should exist
    from app.models.inventory import InventoryRecord

    async with db_session() as session:
        rec_result = await session.execute(
            select(InventoryRecord).where(
                InventoryRecord.merchant_id == mid,
                InventoryRecord.source == "purchase_list",
            )
        )
        records = rec_result.scalars().all()
        assert len(records) == 1
