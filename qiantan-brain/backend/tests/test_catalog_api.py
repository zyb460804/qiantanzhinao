"""Tests for catalog router — SKU, alias, spec, unit, supplier CRUD + scoring.

Covers §6: normal flow, boundary, auth, cross-tenant isolation, idempotency.
"""

import uuid

import pytest


class TestSKUCRUD:
    async def test_create_and_list_sku(self, client):
        res = await client.post(
            "/api/v1/catalog/skus",
            json={
                "name": "番茄",
                "canonical_unit": "斤",
                "category_group": "蔬菜",
                "shelf_life_hours": 72,
            },
        )
        assert res.status_code == 200
        data = res.json()
        assert data["code"] == 0
        sku_id = data["data"]["sku_id"]

        res2 = await client.get("/api/v1/catalog/skus")
        assert res2.status_code == 200
        skus = res2.json()["data"]
        assert any(s["sku_id"] == sku_id for s in skus)

    async def test_create_sku_missing_name(self, client):
        res = await client.post("/api/v1/catalog/skus", json={"canonical_unit": "斤"})
        assert res.status_code in (400, 422)

    async def test_deactivate_sku(self, client):
        res = await client.post(
            "/api/v1/catalog/skus",
            json={
                "name": "空心菜",
                "canonical_unit": "斤",
            },
        )
        sku_id = res.json()["data"]["sku_id"]
        res2 = await client.delete(f"/api/v1/catalog/skus/{sku_id}")
        assert res2.status_code == 200

        res3 = await client.get("/api/v1/catalog/skus")
        skus = res3.json()["data"]
        # Deactivated SKU should not appear in active list
        assert all(s["sku_id"] != sku_id for s in skus)

    async def test_cross_merchant_isolation(self, client):
        res = await client.post(
            "/api/v1/catalog/skus",
            json={
                "name": "菠菜",
                "canonical_unit": "斤",
            },
        )
        sku_id = res.json()["data"]["sku_id"]

        other = str(uuid.uuid4())
        res2 = await client.get("/api/v1/catalog/skus", headers={"X-Test-Merchant-Id": other})
        assert res2.status_code == 200
        assert all(s["sku_id"] != sku_id for s in res2.json()["data"])


class TestAliasCRUD:
    async def test_create_alias(self, client):
        res = await client.post(
            "/api/v1/catalog/skus",
            json={
                "name": "番茄",
                "canonical_unit": "斤",
            },
        )
        sku_id = res.json()["data"]["sku_id"]

        res2 = await client.post(
            f"/api/v1/catalog/skus/{sku_id}/aliases",
            json={
                "alias": "西红柿",
            },
        )
        assert res2.status_code == 200

        res3 = await client.get(f"/api/v1/catalog/skus/{sku_id}/aliases")
        assert res3.status_code == 200
        aliases = res3.json()["data"]
        assert any(a["alias"] == "西红柿" for a in aliases)

    async def test_duplicate_alias_rejected(self, client):
        res = await client.post(
            "/api/v1/catalog/skus",
            json={
                "name": "番茄",
                "canonical_unit": "斤",
            },
        )
        sku_id = res.json()["data"]["sku_id"]
        await client.post(
            f"/api/v1/catalog/skus/{sku_id}/aliases",
            json={
                "alias": "洋柿子",
            },
        )
        res2 = await client.post(
            f"/api/v1/catalog/skus/{sku_id}/aliases",
            json={
                "alias": "洋柿子",
            },
        )
        assert res2.status_code == 409


class TestSupplierCRUD:
    async def test_create_supplier_minimal(self, client):
        res = await client.post("/api/v1/catalog/suppliers", json={"name": "老王"})
        assert res.status_code == 200
        data = res.json()
        assert data["code"] == 0
        assert data["data"]["name"] == "老王"

    async def test_create_supplier_full(self, client):
        res = await client.post(
            "/api/v1/catalog/suppliers",
            json={
                "name": "老张菜铺",
                "contact": "13800001111",
                "address": "农贸市场A区12号",
                "business_category": "叶菜类",
                "default_credit_days": 30,
                "lead_time_hours": 24,
                "min_order_qty": 50,
            },
        )
        assert res.status_code == 200
        sid = res.json()["data"]["supplier_id"]

        res2 = await client.get("/api/v1/catalog/suppliers")
        suppliers = res2.json()["data"]["items"]
        match = [s for s in suppliers if s["supplier_id"] == sid]
        assert len(match) == 1
        assert match[0]["name"] == "老张菜铺"
        assert match[0].get("address") == "农贸市场A区12号"
        assert match[0].get("default_credit_days") == 30

    async def test_blacklist_supplier(self, client):
        res = await client.post("/api/v1/catalog/suppliers", json={"name": "问题供应商"})
        sid = res.json()["data"]["supplier_id"]

        res2 = await client.put(
            f"/api/v1/catalog/suppliers/{sid}",
            json={
                "is_blacklisted": True,
            },
        )
        assert res2.status_code == 200

        res3 = await client.get("/api/v1/catalog/suppliers")
        match = [s for s in res3.json()["data"]["items"] if s["supplier_id"] == sid]
        assert len(match) == 1
        assert match[0].get("is_blacklisted") == True  # noqa: E712


class TestUnitCRUD:
    async def test_create_unit(self, client):
        res = await client.post(
            "/api/v1/catalog/units",
            json={
                "code": "筐",
                "name": "筐",
                "kind": "package",
            },
        )
        assert res.status_code == 200

    async def test_create_conversion(self, client):
        res = await client.post(
            "/api/v1/catalog/unit-conversions",
            json={
                "from_unit": "筐",
                "to_unit": "斤",
                "factor": 45,
            },
        )
        assert res.status_code == 200

        res2 = await client.get("/api/v1/catalog/unit-conversions")
        assert res2.status_code == 200
        convs = res2.json()["data"]
        assert any(c["from_unit"] == "筐" and c["to_unit"] == "斤" for c in convs)

    async def test_reject_same_unit_conversion(self, client):
        res = await client.post(
            "/api/v1/catalog/unit-conversions",
            json={
                "from_unit": "斤",
                "to_unit": "斤",
                "factor": 1,
            },
        )
        assert res.status_code == 400

    async def test_reject_non_positive_conversion_factor(self, client):
        res = await client.post(
            "/api/v1/catalog/unit-conversions",
            json={
                "from_unit": "筐",
                "to_unit": "斤",
                "factor": -1,
            },
        )
        assert res.status_code == 400

    async def test_reject_conflicting_conversion_cycle(self, client):
        assert (
            await client.post(
                "/api/v1/catalog/unit-conversions",
                json={
                    "from_unit": "箱",
                    "to_unit": "斤",
                    "factor": 10,
                },
            )
        ).status_code == 200
        res = await client.post(
            "/api/v1/catalog/unit-conversions",
            json={
                "from_unit": "斤",
                "to_unit": "箱",
                "factor": 0.2,
            },
        )
        assert res.status_code == 409
        assert "冲突" in res.json()["detail"]

    async def test_conversion_graph_isolated_by_sku(self, client):
        sku1 = (
            await client.post(
                "/api/v1/catalog/skus",
                json={
                    "name": "换算商品甲",
                    "canonical_unit": "斤",
                },
            )
        ).json()["data"]["sku_id"]
        sku2 = (
            await client.post(
                "/api/v1/catalog/skus",
                json={
                    "name": "换算商品乙",
                    "canonical_unit": "斤",
                },
            )
        ).json()["data"]["sku_id"]
        first = await client.post(
            "/api/v1/catalog/unit-conversions",
            json={
                "from_unit": "筐",
                "to_unit": "斤",
                "factor": 45,
                "sku_id": sku1,
            },
        )
        second = await client.post(
            "/api/v1/catalog/unit-conversions",
            json={
                "from_unit": "筐",
                "to_unit": "斤",
                "factor": 60,
                "sku_id": sku2,
            },
        )
        assert first.status_code == 200
        assert second.status_code == 200


class TestSpecificationValidation:
    async def test_reject_negative_final_spec_price(self, client):
        created = await client.post(
            "/api/v1/catalog/skus",
            json={
                "name": "低价测试商品",
                "canonical_unit": "斤",
                "default_sale_price": 5,
            },
        )
        sku_id = created.json()["data"]["sku_id"]
        res = await client.post(
            f"/api/v1/catalog/skus/{sku_id}/specs",
            json={
                "name": "错误规格",
                "price_delta": -6,
            },
        )
        assert res.status_code == 400


class TestUnauthenticated:
    async def test_create_sku_no_auth(self, auth_client):
        res = await auth_client.post(
            "/api/v1/catalog/skus",
            json={
                "name": "test",
                "canonical_unit": "斤",
            },
        )
        assert res.status_code == 401

    async def test_create_supplier_no_auth(self, auth_client):
        res = await auth_client.post("/api/v1/catalog/suppliers", json={"name": "test"})
        assert res.status_code == 401


class TestSupplierScoring:
    async def test_score_no_history(self, client):
        res = await client.post("/api/v1/catalog/suppliers", json={"name": "新供应商"})
        sid = res.json()["data"]["supplier_id"]

        res2 = await client.post(f"/api/v1/catalog/suppliers/{sid}/recalculate-score")
        assert res2.status_code == 200
        data = res2.json()
        assert data["data"]["score"] is None


class TestSupplierPagination:
    """Tests for paginated supplier list (offset/limit)."""

    async def test_pagination_defaults(self, client):
        res = await client.get("/api/v1/catalog/suppliers")
        assert res.status_code == 200
        data = res.json()["data"]
        assert "items" in data
        assert "total" in data
        assert data["offset"] == 0
        assert data["limit"] == 50

    async def test_pagination_with_limit(self, client):
        for i in range(5):
            await client.post("/api/v1/catalog/suppliers", json={"name": f"分页测试{i}"})

        res = await client.get("/api/v1/catalog/suppliers?limit=2")
        data = res.json()["data"]
        assert len(data["items"]) == 2
        assert data["total"] >= 5
        assert data["limit"] == 2

    async def test_pagination_offset(self, client):
        for i in range(5):
            await client.post("/api/v1/catalog/suppliers", json={"name": f"偏移测试{i}"})

        first = await client.get("/api/v1/catalog/suppliers?limit=2&offset=0")
        second = await client.get("/api/v1/catalog/suppliers?limit=2&offset=2")
        third = await client.get("/api/v1/catalog/suppliers?limit=2&offset=4")

        first_ids = {s["supplier_id"] for s in first.json()["data"]["items"]}
        second_ids = {s["supplier_id"] for s in second.json()["data"]["items"]}
        third_ids = {s["supplier_id"] for s in third.json()["data"]["items"]}

        # Pages should not overlap
        assert len(first_ids & second_ids) == 0
        assert len(second_ids & third_ids) == 0
        assert len(first_ids & third_ids) == 0

    async def test_pagination_beyond_total(self, client):
        res = await client.get("/api/v1/catalog/suppliers?offset=9999")
        data = res.json()["data"]
        assert data["items"] == []
        assert data["offset"] == 9999

    async def test_keyword_search_with_pagination(self, client):
        await client.post(
            "/api/v1/catalog/suppliers", json={"name": "老王菜铺", "contact": "13800000001"}
        )
        await client.post(
            "/api/v1/catalog/suppliers", json={"name": "老张肉铺", "contact": "13800000002"}
        )
        await client.post(
            "/api/v1/catalog/suppliers", json={"name": "小李豆坊", "contact": "13800000003"}
        )

        res = await client.get("/api/v1/catalog/suppliers?keyword=老王")
        data = res.json()["data"]
        assert data["total"] == 1
        assert len(data["items"]) == 1
        assert data["items"][0]["name"] == "老王菜铺"

    async def test_keyword_no_match(self, client):
        res = await client.get("/api/v1/catalog/suppliers?keyword=不存在的供应商XYZ")
        data = res.json()["data"]
        assert data["total"] == 0
        assert data["items"] == []

    async def test_cross_tenant_pagination_isolation(self, client):
        other = str(uuid.uuid4())
        await client.post("/api/v1/catalog/suppliers", json={"name": "我的供应商"})
        await client.post(
            "/api/v1/catalog/suppliers",
            json={"name": "他的供应商"},
            headers={"X-Test-Merchant-Id": other},
        )

        mine = await client.get("/api/v1/catalog/suppliers")
        his = await client.get("/api/v1/catalog/suppliers", headers={"X-Test-Merchant-Id": other})

        mine_data = mine.json()["data"]
        his_data = his.json()["data"]
        mine_names = {s["name"] for s in mine_data["items"]}
        his_names = {s["name"] for s in his_data["items"]}

        assert "我的供应商" in mine_names
        assert "他的供应商" not in mine_names
        assert "他的供应商" in his_names
        assert "我的供应商" not in his_names


class TestSupplierCompare:
    """Tests for supplier comparison and recommendation endpoints."""

    async def _create_sku_with_suppliers(self, client):
        """Helper: create a SKU and two suppliers with different prices/quality."""
        # Create SKU
        sku_res = await client.post(
            "/api/v1/catalog/skus",
            json={
                "name": "比价测试商品",
                "canonical_unit": "斤",
            },
        )
        sku_id = sku_res.json()["data"]["sku_id"]

        # Create two suppliers
        s1 = await client.post(
            "/api/v1/catalog/suppliers",
            json={
                "name": "优质供应商",
                "lead_time_hours": 12,
            },
        )
        s1_id = s1.json()["data"]["supplier_id"]

        s2 = await client.post(
            "/api/v1/catalog/suppliers",
            json={
                "name": "便宜供应商",
                "lead_time_hours": 48,
            },
        )
        s2_id = s2.json()["data"]["supplier_id"]

        # Link SKU to both suppliers with different prices
        await client.post(
            f"/api/v1/catalog/suppliers/{s1_id}/products",
            json={
                "sku_id": sku_id,
                "last_price": 5.0,
                "min_order_qty": 10,
            },
        )
        await client.post(
            f"/api/v1/catalog/suppliers/{s2_id}/products",
            json={
                "sku_id": sku_id,
                "last_price": 3.5,
                "min_order_qty": 20,
            },
        )

        return sku_id, s1_id, s2_id

    async def test_compare_basic(self, client):
        sku_id, s1_id, s2_id = await self._create_sku_with_suppliers(client)

        res = await client.get(f"/api/v1/catalog/suppliers/compare?sku_id={sku_id}")
        assert res.status_code == 200
        data = res.json()["data"]
        assert data["sku_name"] == "比价测试商品"
        assert data["total"] == 2
        assert len(data["suppliers"]) == 2

        names = {s["supplier_name"] for s in data["suppliers"]}
        assert "优质供应商" in names
        assert "便宜供应商" in names

    async def test_compare_sorted_by_price(self, client):
        sku_id, s1_id, s2_id = await self._create_sku_with_suppliers(client)

        res = await client.get(f"/api/v1/catalog/suppliers/compare?sku_id={sku_id}&sort_by=price")
        suppliers = res.json()["data"]["suppliers"]
        # Cheapest first
        assert suppliers[0]["last_price"] <= suppliers[1]["last_price"]

    async def test_compare_sorted_by_value_default(self, client):
        sku_id, s1_id, s2_id = await self._create_sku_with_suppliers(client)

        res = await client.get(f"/api/v1/catalog/suppliers/compare?sku_id={sku_id}")
        suppliers = res.json()["data"]["suppliers"]
        assert suppliers[0]["is_best"] is True

    async def test_compare_no_suppliers(self, client):
        # SKU with no suppliers linked
        sku_res = await client.post(
            "/api/v1/catalog/skus",
            json={
                "name": "无供应商商品",
                "canonical_unit": "斤",
            },
        )
        sku_id = sku_res.json()["data"]["sku_id"]

        res = await client.get(f"/api/v1/catalog/suppliers/compare?sku_id={sku_id}")
        assert res.status_code == 200
        assert res.json()["data"]["suppliers"] == []

    async def test_compare_excludes_blacklisted(self, client):
        sku_id, s1_id, s2_id = await self._create_sku_with_suppliers(client)

        # Blacklist supplier 1
        await client.put(
            f"/api/v1/catalog/suppliers/{s1_id}",
            json={
                "is_blacklisted": True,
            },
        )

        res = await client.get(f"/api/v1/catalog/suppliers/compare?sku_id={sku_id}")
        suppliers = res.json()["data"]["suppliers"]
        names = {s["supplier_name"] for s in suppliers}
        assert "优质供应商" not in names  # blacklisted
        assert "便宜供应商" in names


class TestSupplierRecommend:
    """Tests for the AI recommendation endpoint."""

    async def _create_sku_with_suppliers(self, client):
        sku_res = await client.post(
            "/api/v1/catalog/skus",
            json={
                "name": "推荐测试商品",
                "canonical_unit": "斤",
            },
        )
        sku_id = sku_res.json()["data"]["sku_id"]

        s1 = await client.post(
            "/api/v1/catalog/suppliers",
            json={
                "name": "五星供应商",
                "lead_time_hours": 24,
            },
        )
        s1_id = s1.json()["data"]["supplier_id"]

        s2 = await client.post(
            "/api/v1/catalog/suppliers",
            json={
                "name": "低价供应商",
                "lead_time_hours": 72,
            },
        )
        s2_id = s2.json()["data"]["supplier_id"]

        await client.post(
            f"/api/v1/catalog/suppliers/{s1_id}/products",
            json={
                "sku_id": sku_id,
                "last_price": 8.0,
                "min_order_qty": 5,
            },
        )
        await client.post(
            f"/api/v1/catalog/suppliers/{s2_id}/products",
            json={
                "sku_id": sku_id,
                "last_price": 4.5,
                "min_order_qty": 30,
            },
        )

        return sku_id, s1_id, s2_id

    async def test_recommend_normal(self, client):
        sku_id, s1_id, s2_id = await self._create_sku_with_suppliers(client)

        res = await client.post(f"/api/v1/catalog/suppliers/recommend?sku_id={sku_id}")
        assert res.status_code == 200
        data = res.json()["data"]
        assert data["recommendation"] is not None
        assert data["recommendation"]["supplier_name"] in ("五星供应商", "低价供应商")
        assert len(data["alternatives"]) > 0

    async def test_recommend_cost_mode(self, client):
        sku_id, s1_id, s2_id = await self._create_sku_with_suppliers(client)

        res = await client.post(f"/api/v1/catalog/suppliers/recommend?sku_id={sku_id}&urgency=cost")
        data = res.json()["data"]
        assert data["recommendation"]["supplier_name"] == "低价供应商"

    async def test_recommend_max_price(self, client):
        sku_id, s1_id, s2_id = await self._create_sku_with_suppliers(client)

        res = await client.post(f"/api/v1/catalog/suppliers/recommend?sku_id={sku_id}&max_price=3")
        data = res.json()["data"]
        assert data["recommendation"] is None  # no supplier under 3 yuan
        assert "价格上限" in data["message"]

    async def test_recommend_no_suppliers(self, client):
        sku_res = await client.post(
            "/api/v1/catalog/skus",
            json={
                "name": "无供应商商品",
                "canonical_unit": "斤",
            },
        )
        sku_id = sku_res.json()["data"]["sku_id"]

        res = await client.post(f"/api/v1/catalog/suppliers/recommend?sku_id={sku_id}")
        assert res.status_code == 200
        assert res.json()["data"]["recommendation"] is None
