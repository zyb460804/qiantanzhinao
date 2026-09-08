"""SKU 唯一性护栏 + 单位换算引导字段测试（create_sku 端点）。"""

import uuid


class TestSkuDuplicate409:
    async def test_duplicate_active_sku_rejected_409(self, client):
        res = await client.post(
            "/api/v1/catalog/skus",
            json={"name": "紫甘蓝", "canonical_unit": "斤"},
        )
        assert res.status_code == 200
        res2 = await client.post(
            "/api/v1/catalog/skus",
            json={"name": "紫甘蓝", "canonical_unit": "斤"},
        )
        assert res2.status_code == 409
        assert res2.json()["detail"] == "商品已存在"

    async def test_same_name_other_merchant_allowed(self, client):
        """部分唯一索引按 merchant_id 隔离：别家商户同名不受影响。"""
        res = await client.post(
            "/api/v1/catalog/skus",
            json={"name": "油麦菜", "canonical_unit": "斤"},
        )
        assert res.status_code == 200
        other = str(uuid.uuid4())
        res2 = await client.post(
            "/api/v1/catalog/skus",
            json={"name": "油麦菜", "canonical_unit": "斤"},
            headers={"X-Test-Merchant-Id": other},
        )
        assert res2.status_code == 200

    async def test_deactivated_sku_name_reusable(self, client):
        """软停用后同名可重建（部分唯一索引只约束活跃行）。"""
        res = await client.post(
            "/api/v1/catalog/skus",
            json={"name": "芥蓝", "canonical_unit": "斤"},
        )
        sku_id = res.json()["data"]["sku_id"]
        res2 = await client.delete(f"/api/v1/catalog/skus/{sku_id}")
        assert res2.status_code == 200

        res3 = await client.post(
            "/api/v1/catalog/skus",
            json={"name": "芥蓝", "canonical_unit": "斤"},
        )
        assert res3.status_code == 200, res3.text


class TestCreateSkuConversionHint:
    async def test_hint_when_primary_unit_differs(self, client):
        """显式 primary_unit（采购主单位）≠ canonical_unit → 引导字段。"""
        res = await client.post(
            "/api/v1/catalog/skus",
            json={"name": "莲藕", "canonical_unit": "斤", "primary_unit": "公斤"},
        )
        assert res.status_code == 200
        hint = res.json()["data"]["unit_conversion_hint"]
        assert hint["need_conversion"] is True
        assert hint["from_unit"] == "公斤"
        assert hint["to_unit"] == "斤"
        assert "换算" in hint["message"]

    async def test_hint_when_base_unit_differs(self, client):
        """未传 primary_unit 时回退商户基础单位（is_base=True）。"""
        await client.post(
            "/api/v1/catalog/units",
            json={"code": "斤", "name": "斤", "kind": "weight", "is_base": True},
        )
        res = await client.post(
            "/api/v1/catalog/skus",
            json={"name": "基围虾", "canonical_unit": "公斤"},
        )
        assert res.status_code == 200
        hint = res.json()["data"]["unit_conversion_hint"]
        assert hint["from_unit"] == "斤"
        assert hint["to_unit"] == "公斤"

    async def test_no_hint_when_units_match(self, client):
        """主单位与基准单位一致（或无基础单位配置）→ 不打扰，无提示字段。"""
        res = await client.post(
            "/api/v1/catalog/skus",
            json={"name": "茼蒿", "canonical_unit": "斤", "primary_unit": "斤"},
        )
        assert res.status_code == 200
        assert "unit_conversion_hint" not in res.json()["data"]
