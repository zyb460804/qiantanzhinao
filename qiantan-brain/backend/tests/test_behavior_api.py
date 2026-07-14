"""Integration tests for merchant behavior feedback and profile learning."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select
from tests.conftest import TEST_MERCHANT_ID

from app.models.merchant import Merchant
from app.models.preference import MerchantPreference
from app.models.recommendation import Recommendation


async def _seed_recommendations(db_session, merchant_id: uuid.UUID, count: int) -> list[uuid.UUID]:
    async with db_session() as session:
        recommendations = [
            Recommendation(
                merchant_id=merchant_id,
                product_id=1,
                suggestion=f"测试建议 {index}",
                basis=["behavior-test"],
                recommended_qty=10,
                confidence=0.9,
            )
            for index in range(count)
        ]
        session.add_all(recommendations)
        await session.commit()
        return [recommendation.id for recommendation in recommendations]


class TestBehaviorFeedback:
    async def test_miniprogram_payload_records_feedback_and_returns_typed_profile(
        self, client, db_session
    ):
        merchant_id = uuid.UUID(TEST_MERCHANT_ID)
        recommendation_id = (await _seed_recommendations(db_session, merchant_id, 1))[0]

        response = await client.post(
            "/api/v1/behavior/feedback",
            json={
                "recommendation_id": str(recommendation_id),
                "was_adopted": True,
                "actual_quantity": 8,
            },
        )
        assert response.status_code == 200
        result = response.json()["data"]
        assert result == {
            "purchase_style": "balanced",
            "profile_label": "均衡型",
            "recommended_multiplier": 1.0,
            "total_decisions_recorded": 1,
        }

        profile_response = await client.get("/api/v1/behavior/profile")
        assert profile_response.status_code == 200
        payload = profile_response.json()
        assert payload["data"] == {
            "purchase_style": "balanced",
            "profile_label": "均衡型",
            "quantity_multiplier": 1.0,
            "total_decisions": 1,
            "adoption_rate": 1.0,
            "correction_rate": 0.0,
            "risk_profile": "neutral",
        }
        assert {item["key"] for item in payload["available_profiles"]} == {
            "conservative",
            "balanced",
            "aggressive",
        }

        async with db_session() as session:
            recommendation = await session.get(Recommendation, recommendation_id)
            assert recommendation is not None
            assert recommendation.was_adopted is True
            assert float(recommendation.actual_deviation) == pytest.approx(-0.2)

            preference = await session.scalar(
                select(MerchantPreference).where(MerchantPreference.merchant_id == merchant_id)
            )
            assert preference is not None
            assert preference.preference_data == {
                "purchase_style": "balanced",
                "avg_adoption_rate": 1.0,
                "correction_rate": 0.0,
                "total_decisions": 1,
                "total_corrections": 0,
            }

    async def test_rejects_feedback_for_another_merchant(self, client, db_session):
        other_merchant_id = uuid.uuid4()
        async with db_session() as session:
            session.add(Merchant(id=other_merchant_id, name="其他商户"))
            recommendation = Recommendation(
                merchant_id=other_merchant_id,
                product_id=1,
                suggestion="其他商户的建议",
                basis=["isolation-test"],
                recommended_qty=10,
                confidence=0.8,
            )
            session.add(recommendation)
            await session.commit()
            recommendation_id = recommendation.id

        response = await client.post(
            "/api/v1/behavior/feedback",
            json={
                "recommendation_id": str(recommendation_id),
                "was_adopted": True,
            },
        )
        assert response.status_code == 404
        assert "不属于当前商户" in response.json()["detail"]

        async with db_session() as session:
            recommendation = await session.get(Recommendation, recommendation_id)
            assert recommendation is not None
            assert recommendation.was_adopted is None

    async def test_reclassifies_after_five_decisions(self, client, db_session):
        merchant_id = uuid.UUID(TEST_MERCHANT_ID)
        recommendation_ids = await _seed_recommendations(db_session, merchant_id, 5)

        for recommendation_id in recommendation_ids:
            response = await client.post(
                "/api/v1/behavior/feedback",
                json={
                    "recommendation_id": str(recommendation_id),
                    "was_adopted": True,
                    "actual_quantity": 8,
                },
            )
            assert response.status_code == 200

        result = response.json()["data"]
        assert result["purchase_style"] == "conservative"
        assert result["recommended_multiplier"] == 0.85
        assert result["total_decisions_recorded"] == 5

        profile_response = await client.get("/api/v1/behavior/profile")
        profile = profile_response.json()["data"]
        assert profile["purchase_style"] == "conservative"
        assert profile["quantity_multiplier"] == 0.85
        assert profile["adoption_rate"] == 1.0
        assert profile["total_decisions"] == 5
