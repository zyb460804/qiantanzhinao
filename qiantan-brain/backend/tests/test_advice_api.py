"""
Integration tests for advice + simulation endpoints.

Covers:
  POST /api/v1/simulate/what-if — decision sandbox
  GET  /api/v1/advice/daily — daily recommendations (needs seeded sales data)
  GET  /api/v1/twin/dashboard — KPI aggregation
"""

import uuid

import pytest
from sqlalchemy import select
from tests.conftest import TEST_MERCHANT_ID

from app.models.recommendation import Recommendation


pytestmark = pytest.mark.asyncio


class TestSimulateWhatIf:
    """POST /api/v1/simulate/what-if — What-if sandbox engine."""

    async def test_basic_simulation(self, client):
        """Basic what-if returns all expected output fields."""
        resp = await client.post(
            "/api/v1/simulate/what-if",
            json={
                "merchant_id": TEST_MERCHANT_ID,
                "product_id": 1,
                "scenario": {
                    "purchase_qty": 50,
                    "unit_cost": 0.3,
                    "unit_price": 1.5,
                },
            },
        )

        assert resp.status_code == 200
        data = resp.json()["data"]

        # Output structure
        output = data["output"]
        assert "estimated_sales" in output
        assert "net_profit" in output
        assert "waste_rate" in output
        assert "estimated_revenue" in output

        # Comparison structure
        comp = data["comparison"]
        assert "verdict" in comp
        assert "improvement" in comp
        assert "recommendation" in comp

    async def test_zero_purchase_no_crash(self, client):
        """Zero purchase quantity should not crash the simulation."""
        resp = await client.post(
            "/api/v1/simulate/what-if",
            json={
                "merchant_id": TEST_MERCHANT_ID,
                "product_id": 1,
                "scenario": {
                    "purchase_qty": 0,
                    "unit_cost": 0.3,
                    "unit_price": 1.5,
                },
            },
        )

        assert resp.status_code == 200
        output = resp.json()["data"]["output"]
        assert output["estimated_sales"] == 0
        assert output["net_profit"] == 0

    async def test_monotonicity_more_purchase_more_waste(self, client):
        """Buying more → waste rate should not decrease (monotonicity check)."""
        base_resp = await client.post(
            "/api/v1/simulate/what-if",
            json={
                "merchant_id": TEST_MERCHANT_ID,
                "product_id": 1,
                "scenario": {"purchase_qty": 20, "unit_cost": 0.3, "unit_price": 1.5},
            },
        )
        high_resp = await client.post(
            "/api/v1/simulate/what-if",
            json={
                "merchant_id": TEST_MERCHANT_ID,
                "product_id": 1,
                "scenario": {"purchase_qty": 200, "unit_cost": 0.3, "unit_price": 1.5},
            },
        )

        base_waste = base_resp.json()["data"]["output"]["waste_rate"]
        high_waste = high_resp.json()["data"]["output"]["waste_rate"]
        # Buying 10x more should not result in LOWER waste rate
        assert high_waste >= base_waste

    async def test_uses_top_level_product_id(self, client):
        """The selected product must drive shelf-life and waste calculations."""
        scenario = {"purchase_qty": 50, "unit_cost": 1, "unit_price": 2}
        cabbage = await client.post(
            "/api/v1/simulate/what-if",
            json={
                "merchant_id": TEST_MERCHANT_ID,
                "product_id": 1,
                "scenario": scenario,
            },
        )
        tofu = await client.post(
            "/api/v1/simulate/what-if",
            json={
                "merchant_id": TEST_MERCHANT_ID,
                "product_id": 3,
                "scenario": scenario,
            },
        )

        assert tofu.status_code == 200
        assert (
            tofu.json()["data"]["output"]["waste_qty"]
            > cabbage.json()["data"]["output"]["waste_qty"]
        )


class TestAdviceDaily:
    """GET /api/v1/advice/daily — three-line explainable recommendations."""

    async def test_returns_envelope(self, client):
        """Daily advice should return the standard envelope with recommendations list."""
        resp = await client.get(
            "/api/v1/advice/daily",
            params={
                "merchant_id": TEST_MERCHANT_ID,
            },
        )

        assert resp.status_code == 200
        body = resp.json()
        assert body["code"] == 0
        assert "recommendations" in body["data"]

    async def test_recommendation_structure(self, client):
        """Each recommendation has the three-line format."""
        resp = await client.get(
            "/api/v1/advice/daily",
            params={
                "merchant_id": TEST_MERCHANT_ID,
            },
        )
        recs = resp.json()["data"]["recommendations"]

        if len(recs) > 0:
            rec = recs[0]
            assert "suggestion" in rec  # line 1
            assert "basis" in rec  # line 2 (list)
            assert isinstance(rec["basis"], list)
            assert "confidence" in rec
            # Prophet/online forecast is wired into the advice output
            assert "forecast" in rec
            if rec["forecast"] is not None:
                assert "model" in rec["forecast"]

    async def test_recommendations_are_committed(self, client, db_session):
        """Returned recommendation IDs must remain available after request session closes."""
        resp = await client.get(
            "/api/v1/advice/daily",
            params={
                "merchant_id": TEST_MERCHANT_ID,
            },
        )
        rec_ids = resp.json()["data"]["recommendation_ids"]
        assert rec_ids

        async with db_session() as session:
            result = await session.execute(
                select(Recommendation).where(Recommendation.id == uuid.UUID(rec_ids[0]))
            )
            assert result.scalar_one_or_none() is not None


class TestTwinDashboard:
    """GET /api/v1/twin/dashboard — KPI aggregation."""

    async def test_dashboard_kpi_fields(self, client):
        """Dashboard returns the expected KPI fields."""
        resp = await client.get(
            "/api/v1/twin/dashboard",
            params={
                "merchant_id": TEST_MERCHANT_ID,
            },
        )

        assert resp.status_code == 200
        data = resp.json()["data"]
        # KPI fields may be 0 for empty test DB but must exist
        assert "today_revenue" in data or "risk_score" in data


class TestEnvironmentToday:
    """GET /api/v1/env/today — environment with fallback chain."""

    async def test_env_today_returns_data(self, client):
        """Environment today always returns data (cached → API → mock)."""
        resp = await client.get("/api/v1/env/today", params={"city": "上海"})

        assert resp.status_code == 200
        data = resp.json()["data"]
        # Fallback chain guarantees weather fields exist regardless of tier
        assert "temp_high" in data
        assert "rainfall_prob" in data
        assert "weather_type" in data
