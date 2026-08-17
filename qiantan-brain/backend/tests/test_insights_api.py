"""Insights API tests — 算法能力闭环（定价建议 / 报童进货建议）。

覆盖：
- 鉴权：无 token 访问 pricing-suggestions → 401
- 定价建议：从真实 InventoryRecord 聚合库存与销量，返回非空建议
- 报童建议：从真实销量统计生成最优进货量
- 租户隔离：TEST 商户的数据不会出现在其他商户
- 参数校验：非法 price_tier → 422
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, time, timedelta
from decimal import Decimal

from tests.conftest import TEST_MERCHANT_ID  # noqa: F401

from app.core.timezone import CST, cst_today, utc_now
from app.models.catalog import ProductSKU
from app.models.inventory import InventoryRecord


OTHER_MERCHANT_ID = "00000000-0000-0000-0000-000000000002"


async def _seed_bai_cai(db_session, merchant_id: str = TEST_MERCHANT_ID) -> uuid.UUID:
    """Seed 白菜 SKU + 当前库存 + 连续 7 天日销 2 斤。"""
    mid = uuid.UUID(merchant_id)
    now = utc_now()
    async with db_session() as session:
        sku = ProductSKU(
            merchant_id=mid,
            name="白菜",
            canonical_unit="斤",
            shelf_life_hours=72,
            category_group="叶菜类",
            default_sale_price=Decimal("3.00"),
        )
        session.add(sku)
        await session.flush()

        session.add(
            InventoryRecord(
                merchant_id=mid,
                product_id=1,
                sku_id=sku.id,
                quantity=Decimal("50"),
                unit="斤",
                unit_cost=Decimal("1.20"),
                unit_price=None,
                event_type="purchase",
                event_time=now,
                source="manual",
            )
        )

        # 用 CST 业务日正午生成 7 条 sale，确保落在 cst_days_ago_bounds 窗口内。
        for i in range(7):
            day = cst_today() - timedelta(days=6 - i)
            event_time = (
                datetime.combine(day, time(12, 0), tzinfo=CST).astimezone(UTC).replace(tzinfo=None)
            )
            session.add(
                InventoryRecord(
                    merchant_id=mid,
                    product_id=1,
                    sku_id=sku.id,
                    quantity=Decimal("2"),
                    unit="斤",
                    event_type="sale",
                    event_time=event_time,
                    source="manual",
                )
            )

        await session.commit()
        return sku.id


class TestPricingSuggestions:
    async def test_pricing_suggestions_requires_auth(self, auth_client):
        """无 token → 401。"""
        res = await auth_client.get("/api/v1/insights/pricing-suggestions")
        assert res.status_code == 401

    async def test_pricing_suggestions_returns_sorted_suggestions(self, client, db_session):
        """seed 后返回非空建议，且核心字段齐全、折扣非负。"""
        await _seed_bai_cai(db_session)

        res = await client.get("/api/v1/insights/pricing-suggestions")
        assert res.status_code == 200
        body = res.json()
        assert body["code"] == 0

        data = body["data"]
        assert data["count"] > 0
        assert data["suggestions"]

        suggestion = data["suggestions"][0]
        assert suggestion["product_name"] == "白菜"
        assert suggestion["recommended_price"] > 0
        assert suggestion["reason"]
        assert suggestion["strategy"] in (
            "age_based",
            "inventory_based",
            "combined",
            "clearance",
        )
        assert suggestion["urgency"] in ("critical", "high", "medium", "low")
        assert suggestion["discount_pct"] >= 0
        assert isinstance(suggestion["alternative_prices"], list)

    async def test_pricing_suggestions_invalid_tier_returns_422(self, client):
        """price_tier=foo → 422。"""
        res = await client.get("/api/v1/insights/pricing-suggestions?price_tier=foo")
        assert res.status_code == 422


class TestNewsvendorSuggestions:
    async def test_newsvendor_suggestions_returns_advice(self, client, db_session):
        """seed 后返回报童模型建议，最优进货量 > 0。"""
        await _seed_bai_cai(db_session)

        res = await client.get("/api/v1/insights/newsvendor-suggestions")
        assert res.status_code == 200
        body = res.json()
        assert body["code"] == 0

        data = body["data"]
        assert data["count"] > 0
        assert data["suggestions"]

        suggestion = data["suggestions"][0]
        assert suggestion["optimal_quantity"] > 0
        assert suggestion["suggestion"]
        assert suggestion["waste_rate_pct"] >= 0
        assert suggestion["mean_demand"] > 0


class TestInsightsIsolation:
    async def test_insights_isolates_merchants(self, client, db_session):
        """TEST 商户数据不能被其他商户看到。"""
        await _seed_bai_cai(db_session)
        headers = {"X-Test-Merchant-Id": OTHER_MERCHANT_ID}

        pricing_res = await client.get("/api/v1/insights/pricing-suggestions", headers=headers)
        assert pricing_res.status_code == 200
        pricing_data = pricing_res.json()["data"]
        assert pricing_data["count"] == 0
        assert pricing_data["suggestions"] == []

        newsvendor_res = await client.get(
            "/api/v1/insights/newsvendor-suggestions", headers=headers
        )
        assert newsvendor_res.status_code == 200
        newsvendor_data = newsvendor_res.json()["data"]
        assert newsvendor_data["count"] == 0
        assert newsvendor_data["suggestions"] == []
