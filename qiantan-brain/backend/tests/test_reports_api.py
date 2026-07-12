"""Integration tests for /api/v1/reports — daily/weekly reports, trends, rankings.

Covers revenue/cost/profit calculation, COGS estimation, multi-merchant
isolation, and the total_amount=None edge case (previously a 500).
"""

import sys
from pathlib import Path


sys.path.insert(0, str(Path(__file__).parent.parent))

import uuid
from datetime import datetime

import pytest
import pytest_asyncio
from tests.conftest import TEST_MERCHANT_ID, TEST_PRODUCT_ID


SECOND_MERCHANT_ID = "00000000-0000-0000-0000-000000000002"

pytestmark = pytest.mark.asyncio


async def _create_record(
    session,
    merchant_id,
    product_id,
    event_type,
    quantity,
    unit_cost=None,
    unit_price=None,
    total_amount=None,
):
    """Insert an InventoryRecord directly via session."""
    from app.models.inventory import InventoryRecord

    mid = uuid.UUID(merchant_id) if isinstance(merchant_id, str) else merchant_id
    record = InventoryRecord(
        merchant_id=mid,
        product_id=product_id,
        quantity=quantity,
        unit="斤",
        unit_cost=unit_cost,
        unit_price=unit_price,
        total_amount=total_amount,
        event_type=event_type,
        event_time=datetime.now(),
    )
    session.add(record)
    await session.commit()


# ------------------------------------------------------------------
# GET /api/v1/reports/daily
# ------------------------------------------------------------------


async def test_daily_report_empty_data(client, db_session):
    """Empty database — revenue/cost/profit all zero."""
    resp = await client.get("/api/v1/reports/daily", params={"merchant_id": TEST_MERCHANT_ID})
    assert resp.status_code == 200
    data = resp.json()
    assert data["code"] == 0
    d = data["data"]
    assert d["revenue"] == 0
    assert d["cost"] == 0
    assert d["profit"] == 0


async def test_daily_report_with_data(client, db_session):
    """Purchase + sale records — verify revenue/cost/profit/COGS/cash_balance."""
    mid = uuid.UUID(TEST_MERCHANT_ID)
    async with db_session() as session:
        # Purchase 10斤 @ 0.5 → total 5.0
        await _create_record(
            session,
            mid,
            1,
            "purchase",
            quantity=10,
            unit_cost=0.5,
            total_amount=5.0,
        )
        # Sale 5斤 @ 1.5 → total 7.5
        await _create_record(
            session,
            mid,
            1,
            "sale",
            quantity=-5,
            unit_price=1.5,
            total_amount=7.5,
        )

    resp = await client.get("/api/v1/reports/daily", params={"merchant_id": TEST_MERCHANT_ID})
    assert resp.status_code == 200
    d = resp.json()["data"]
    assert d["revenue"] == 7.5
    assert d["cost"] == 5.0
    # cash_balance = revenue - cost = 2.5; profit is alias of cash_balance
    assert d["cash_balance"] == 2.5
    assert d["profit"] == 2.5
    # estimated_cogs = sale_qty(5) × avg_purchase_cost(0.5) = 2.5
    assert d["estimated_cogs"] == 2.5
    # estimated_gross_profit = revenue - cogs = 7.5 - 2.5 = 5.0
    assert d["estimated_gross_profit"] == 5.0


async def test_daily_report_none_total_amount(client, db_session):
    """total_amount=None must not cause a 500 (regression for prior bug)."""
    mid = uuid.UUID(TEST_MERCHANT_ID)
    async with db_session() as session:
        await _create_record(
            session, mid, 1, "sale", quantity=-5, unit_price=1.5, total_amount=None
        )

    resp = await client.get("/api/v1/reports/daily", params={"merchant_id": TEST_MERCHANT_ID})
    assert resp.status_code == 200
    assert resp.json()["code"] == 0
    # revenue should be 0 (None treated as 0), not an error
    assert resp.json()["data"]["revenue"] == 0


# ------------------------------------------------------------------
# GET /api/v1/reports/weekly
# ------------------------------------------------------------------


async def test_weekly_report_empty(client, db_session):
    """Empty weekly report."""
    resp = await client.get("/api/v1/reports/weekly", params={"merchant_id": TEST_MERCHANT_ID})
    assert resp.status_code == 200
    data = resp.json()
    assert data["code"] == 0
    d = data["data"]
    assert d["week_revenue"] == 0
    assert len(d["daily_trends"]) == 7
    assert d["sales_ranking"] == []
    assert d["waste_ranking"] == []


async def test_weekly_report_with_data(client, db_session):
    """Weekly report with sale + waste records."""
    mid = uuid.UUID(TEST_MERCHANT_ID)
    async with db_session() as session:
        await _create_record(
            session, mid, 1, "purchase", quantity=20, unit_cost=0.5, total_amount=10.0
        )
        await _create_record(
            session, mid, 1, "sale", quantity=-10, unit_price=2.0, total_amount=20.0
        )
        await _create_record(
            session, mid, 2, "sale", quantity=-5, unit_price=3.0, total_amount=15.0
        )
        await _create_record(session, mid, 1, "waste", quantity=-2, total_amount=1.0)

    resp = await client.get("/api/v1/reports/weekly", params={"merchant_id": TEST_MERCHANT_ID})
    assert resp.status_code == 200
    d = resp.json()["data"]
    # 7-day trends
    assert len(d["daily_trends"]) == 7
    # Sales ranking: product 1 (20.0) > product 2 (15.0)
    assert len(d["sales_ranking"]) == 2
    assert d["sales_ranking"][0]["product_id"] == 1
    assert d["sales_ranking"][0]["revenue"] == 20.0
    # Waste ranking: product 1
    assert len(d["waste_ranking"]) == 1
    assert d["waste_ranking"][0]["product_id"] == 1
    # Health score in [0, 100]
    assert 0 <= d["health_score"] <= 100


# ------------------------------------------------------------------
# GET /api/v1/reports/trends
# ------------------------------------------------------------------


async def test_trends_report(client, db_session):
    """Trends report returns the correct number of days."""
    # Default 7 days
    resp = await client.get("/api/v1/reports/trends", params={"merchant_id": TEST_MERCHANT_ID})
    assert resp.status_code == 200
    data = resp.json()
    assert data["code"] == 0
    assert len(data["data"]) == 7
    # Each day has the expected fields
    for day in data["data"]:
        assert "date" in day
        assert "revenue" in day
        assert "cost" in day
        assert "profit" in day

    # Custom 14 days
    resp14 = await client.get(
        "/api/v1/reports/trends",
        params={"merchant_id": TEST_MERCHANT_ID, "days": 14},
    )
    assert resp14.status_code == 200
    assert len(resp14.json()["data"]) == 14


# ------------------------------------------------------------------
# GET /api/v1/reports/product-ranking
# ------------------------------------------------------------------


async def test_product_ranking(client, db_session):
    """Product ranking sorted by revenue descending."""
    mid = uuid.UUID(TEST_MERCHANT_ID)
    async with db_session() as session:
        await _create_record(session, mid, 1, "sale", quantity=-10, total_amount=20.0)
        await _create_record(session, mid, 2, "sale", quantity=-5, total_amount=30.0)
        await _create_record(session, mid, 3, "sale", quantity=-8, total_amount=15.0)

    resp = await client.get(
        "/api/v1/reports/product-ranking",
        params={"merchant_id": TEST_MERCHANT_ID, "metric": "revenue"},
    )
    assert resp.status_code == 200
    ranking = resp.json()["data"]
    assert len(ranking) == 3
    # Sorted by sale_revenue desc
    assert ranking[0]["product_id"] == 2  # 30.0
    assert ranking[1]["product_id"] == 1  # 20.0
    assert ranking[2]["product_id"] == 3  # 15.0
    assert ranking[0]["sale_revenue"] >= ranking[1]["sale_revenue"]


# ------------------------------------------------------------------
# Multi-merchant isolation
# ------------------------------------------------------------------


async def test_multi_merchant_isolation(client, db_session):
    """Two merchants' data must not leak across tenants."""
    from app.models.merchant import Merchant

    mid1 = uuid.UUID(TEST_MERCHANT_ID)
    mid2 = uuid.UUID(SECOND_MERCHANT_ID)

    async with db_session() as session:
        # Create second merchant
        session.add(Merchant(id=mid2, name="测试摊位2", business_type="水果"))
        await session.commit()

        # Merchant 1: sale 10.0
        await _create_record(session, mid1, 1, "sale", quantity=-5, total_amount=10.0)
        # Merchant 2: sale 50.0
        await _create_record(session, mid2, 1, "sale", quantity=-3, total_amount=50.0)

    resp1 = await client.get("/api/v1/reports/daily", params={"merchant_id": TEST_MERCHANT_ID})
    resp2 = await client.get(
        "/api/v1/reports/daily", headers={"X-Test-Merchant-Id": SECOND_MERCHANT_ID}
    )
    assert resp1.status_code == 200
    assert resp2.status_code == 200

    d1 = resp1.json()["data"]
    d2 = resp2.json()["data"]
    assert d1["revenue"] == 10.0
    assert d2["revenue"] == 50.0
