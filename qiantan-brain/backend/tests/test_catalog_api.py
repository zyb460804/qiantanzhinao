"""Tests for catalog router — SKU, alias, spec, unit, supplier CRUD + scoring.

Covers §6: normal flow, boundary, auth, cross-tenant isolation, idempotency.
"""

import uuid

import pytest


class TestSKUCRUD:
    async def test_create_and_list_sku(self, client):
        res = await client.post("/api/v1/catalog/skus", json={
            "name": "番茄", "canonical_unit": "斤",
            "category_group": "蔬菜", "shelf_life_hours": 72,
        })
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
        res = await client.post("/api/v1/catalog/skus", json={
            "name": "空心菜", "canonical_unit": "斤",
        })
        sku_id = res.json()["data"]["sku_id"]
        res2 = await client.delete(f"/api/v1/catalog/skus/{sku_id}")
        assert res2.status_code == 200

        res3 = await client.get("/api/v1/catalog/skus")
        skus = res3.json()["data"]
        # Deactivated SKU should not appear in active list
        assert all(s["sku_id"] != sku_id for s in skus)

    async def test_cross_merchant_isolation(self, client):
        res = await client.post("/api/v1/catalog/skus", json={
            "name": "菠菜", "canonical_unit": "斤",
        })
        sku_id = res.json()["data"]["sku_id"]

        other = str(uuid.uuid4())
        res2 = await client.get("/api/v1/catalog/skus",
                                headers={"X-Test-Merchant-Id": other})
        assert res2.status_code == 200
        assert all(s["sku_id"] != sku_id for s in res2.json()["data"])


class TestAliasCRUD:
    async def test_create_alias(self, client):
        res = await client.post("/api/v1/catalog/skus", json={
            "name": "番茄", "canonical_unit": "斤",
        })
        sku_id = res.json()["data"]["sku_id"]

        res2 = await client.post(f"/api/v1/catalog/skus/{sku_id}/aliases", json={
            "alias": "西红柿",
        })
        assert res2.status_code == 200

        res3 = await client.get(f"/api/v1/catalog/skus/{sku_id}/aliases")
        assert res3.status_code == 200
        aliases = res3.json()["data"]
        assert any(a["alias"] == "西红柿" for a in aliases)

    async def test_duplicate_alias_rejected(self, client):
        res = await client.post("/api/v1/catalog/skus", json={
            "name": "番茄", "canonical_unit": "斤",
        })
        sku_id = res.json()["data"]["sku_id"]
        await client.post(f"/api/v1/catalog/skus/{sku_id}/aliases", json={
            "alias": "洋柿子",
        })
        res2 = await client.post(f"/api/v1/catalog/skus/{sku_id}/aliases", json={
            "alias": "洋柿子",
        })
        assert res2.status_code == 409


class TestSupplierCRUD:
    async def test_create_supplier_minimal(self, client):
        res = await client.post("/api/v1/catalog/suppliers", json={"name": "老王"})
        assert res.status_code == 200
        data = res.json()
        assert data["code"] == 0
        assert data["data"]["name"] == "老王"

    async def test_create_supplier_full(self, client):
        res = await client.post("/api/v1/catalog/suppliers", json={
            "name": "老张菜铺", "contact": "13800001111",
            "address": "农贸市场A区12号", "business_category": "叶菜类",
            "default_credit_days": 30, "lead_time_hours": 24,
            "min_order_qty": 50,
        })
        assert res.status_code == 200
        sid = res.json()["data"]["supplier_id"]

        res2 = await client.get("/api/v1/catalog/suppliers")
        suppliers = res2.json()["data"]
        match = [s for s in suppliers if s["supplier_id"] == sid]
        assert len(match) == 1
        assert match[0]["name"] == "老张菜铺"
        assert match[0].get("address") == "农贸市场A区12号"
        assert match[0].get("default_credit_days") == 30

    async def test_blacklist_supplier(self, client):
        res = await client.post("/api/v1/catalog/suppliers", json={"name": "问题供应商"})
        sid = res.json()["data"]["supplier_id"]

        res2 = await client.put(f"/api/v1/catalog/suppliers/{sid}", json={
            "is_blacklisted": True,
        })
        assert res2.status_code == 200

        res3 = await client.get("/api/v1/catalog/suppliers")
        match = [s for s in res3.json()["data"] if s["supplier_id"] == sid]
        assert len(match) == 1
        assert match[0].get("is_blacklisted") == True  # noqa: E712


class TestUnitCRUD:
    async def test_create_unit(self, client):
        res = await client.post("/api/v1/catalog/units", json={
            "code": "筐", "name": "筐", "kind": "package",
        })
        assert res.status_code == 200

    async def test_create_conversion(self, client):
        res = await client.post("/api/v1/catalog/unit-conversions", json={
            "from_unit": "筐", "to_unit": "斤", "factor": 45,
        })
        assert res.status_code == 200

        res2 = await client.get("/api/v1/catalog/unit-conversions")
        assert res2.status_code == 200
        convs = res2.json()["data"]
        assert any(c["from_unit"] == "筐" and c["to_unit"] == "斤" for c in convs)


class TestUnauthenticated:
    async def test_create_sku_no_auth(self, auth_client):
        res = await auth_client.post("/api/v1/catalog/skus", json={
            "name": "test", "canonical_unit": "斤",
        })
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
