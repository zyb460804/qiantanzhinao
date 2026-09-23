"""QA 修复回归测试 — Agent K（商品目录/员工/费用域）。

覆盖缺陷编号：
- QA-02：PUT /catalog/skus/{id} 挂 change_price 权限（owner/manager 200，cashier 403）
- QA-33：price_history.changed_by 记录操作者身份（员工名 / 老板），不再恒为 "merchant"
- QA-11：SKU 售价非数字串（"abc"/"NaN"/"Infinity"）→ 422，不再 500
- QA-08：supplier contact/address、expense description 存储型 XSS 净化（create+update）
- QA-21：POST /staff 创建员工 PIN 必填（缺失/空 → 422）
- QA-28：canonical_unit ≤16 字（422）；supplier min_order_qty ≥ 0（422）
- QA-05：POS 可售列表（GET /inventory/current）商户自有 SKU 名称/价格优先（own_sku_* 字段）
"""

from __future__ import annotations

import uuid
from datetime import datetime

import pytest
from sqlalchemy import select

from tests.conftest import TEST_MERCHANT_ID

pytestmark = pytest.mark.asyncio


# ═══════════════════════════════════════════════════════════════════
# 工具
# ═══════════════════════════════════════════════════════════════════


async def _create_sku(client, name: str, **extra) -> str:
    res = await client.post("/api/v1/catalog/skus", json={"name": name, **extra})
    assert res.status_code == 200, res.text
    return res.json()["data"]["sku_id"]


async def _create_staff_direct(db_session, role: str, name: str) -> str:
    """直接落库建员工（绕过 API 的 PIN 必填约束），返回 staff_id 供 X-Staff-Id 头用。"""
    from app.models.staff import StaffMember

    sid = uuid.uuid4()
    async with db_session() as session:
        session.add(
            StaffMember(
                id=sid,
                merchant_id=uuid.UUID(TEST_MERCHANT_ID),
                name=name,
                role=role,
                pin_code="$2b$12$xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx",
            )
        )
        await session.commit()
    return str(sid)


# ═══════════════════════════════════════════════════════════════════
# QA-02：改价权限 + QA-33：changed_by 操作者身份
# ═══════════════════════════════════════════════════════════════════


class TestChangePricePermission:
    async def test_owner_can_update_sku_price(self, client):
        """owner（默认商户身份）有 change_price 权限 → 200。"""
        sku_id = await _create_sku(client, "白菜", default_sale_price=3.5)
        res = await client.put(
            f"/api/v1/catalog/skus/{sku_id}", json={"default_sale_price": 4}
        )
        assert res.status_code == 200, res.text

    async def test_cashier_cannot_update_sku_price_403(self, client, db_session):
        """QA-02：cashier 无 change_price 权限，改价 → 403（此前 3.5 可改 0.01）。"""
        sku_id = await _create_sku(client, "苹果", default_sale_price=3.5)
        sid = await _create_staff_direct(db_session, "cashier", "收银小张")
        res = await client.put(
            f"/api/v1/catalog/skus/{sku_id}",
            json={"default_sale_price": 0.01},
            headers={"X-Staff-Id": sid},
        )
        assert res.status_code == 403
        # 价格未被改动
        list_res = await client.get("/api/v1/catalog/skus")
        row = [s for s in list_res.json()["data"] if s["sku_id"] == sku_id][0]
        assert row["default_sale_price"] == 3.5

    async def test_manager_can_update_sku_price(self, client, db_session):
        """manager 角色含 change_price → 放行（权限粒度回归）。"""
        sku_id = await _create_sku(client, "土豆", default_sale_price=2)
        sid = await _create_staff_direct(db_session, "manager", "店长老李")
        res = await client.put(
            f"/api/v1/catalog/skus/{sku_id}",
            json={"default_sale_price": 2.5},
            headers={"X-Staff-Id": sid},
        )
        assert res.status_code == 200, res.text


class TestPriceHistoryChangedBy:
    async def test_staff_price_change_records_staff_name(self, client, db_session):
        """QA-33：员工（manager）改价后 changed_by = 员工名，而非 "merchant"。"""
        sku_id = await _create_sku(client, "豆腐", default_sale_price=3)
        sid = await _create_staff_direct(db_session, "manager", "店长老王")
        res = await client.put(
            f"/api/v1/catalog/skus/{sku_id}",
            json={"default_sale_price": 3.8},
            headers={"X-Staff-Id": sid},
        )
        assert res.status_code == 200, res.text
        hist = await client.get(f"/api/v1/catalog/skus/{sku_id}/price-history")
        assert hist.status_code == 200
        rows = hist.json()["data"]
        assert len(rows) >= 1
        assert rows[0]["changed_by"] == "店长老王"
        assert rows[0]["changed_by"] != "merchant"

    async def test_owner_price_change_records_boss(self, client):
        """QA-33：owner 改价 → changed_by = "老板"（向后兼容：字段仍为 str）。"""
        sku_id = await _create_sku(client, "猪肉", default_sale_price=12)
        res = await client.put(
            f"/api/v1/catalog/skus/{sku_id}", json={"default_sale_price": 13}
        )
        assert res.status_code == 200
        hist = await client.get(f"/api/v1/catalog/skus/{sku_id}/price-history")
        rows = hist.json()["data"]
        assert len(rows) >= 1
        assert rows[0]["changed_by"] == "老板"


# ═══════════════════════════════════════════════════════════════════
# QA-11：SKU 售价非数字串 → 422（create + update 双路径）
# ═══════════════════════════════════════════════════════════════════


class TestSalePriceFormatGuard:
    @pytest.mark.parametrize("bad_price", ["abc", "NaN", "Infinity"])
    async def test_create_sku_bad_price_422(self, client, bad_price):
        """QA-11：三个坏样本 create 路径全 422（此前 Decimal InvalidOperation → 500）。"""
        res = await client.post(
            "/api/v1/catalog/skus",
            json={"name": "坏价商品", "default_sale_price": bad_price},
        )
        assert res.status_code == 422, res.text

    @pytest.mark.parametrize("bad_price", ["abc", "NaN", "Infinity"])
    async def test_update_sku_bad_price_422(self, client, bad_price):
        """QA-11：update 路径同样 422，口径与费用金额防护一致。"""
        sku_id = await _create_sku(client, "正常价商品", default_sale_price=5)
        res = await client.put(
            f"/api/v1/catalog/skus/{sku_id}", json={"default_sale_price": bad_price}
        )
        assert res.status_code == 422, res.text

    async def test_expense_price_message_consistency(self, client):
        """口径对齐：费用金额非法 → 400（既有防护），SKU 售价非法 → 422，
        两者均有中文明确报错而非 500。"""
        res = await client.post(
            "/api/v1/expenses",
            json={"category": "other", "amount": "abc", "expense_date": "2026-09-22"},
        )
        assert res.status_code == 400
        assert "格式" in res.json()["detail"]


# ═══════════════════════════════════════════════════════════════════
# QA-08：supplier contact/address、expense description XSS 净化
# ═══════════════════════════════════════════════════════════════════


class TestStoredXssSanitization:
    async def test_create_supplier_contact_address_sanitized(self, client):
        """QA-08：create 供应商 contact/address 的 payload 落库前被剥离。"""
        res = await client.post(
            "/api/v1/catalog/suppliers",
            json={
                "name": "老王供货商",
                "contact": "王<script>alert('c')</script>13800' or '1'='1",
                "address": '市场东门"><img src=x onerror=alert(3)>12号',
            },
        )
        assert res.status_code == 200, res.text
        sid = res.json()["data"]["supplier_id"]
        detail = await client.get(f"/api/v1/catalog/suppliers/{sid}")
        data = detail.json()["data"]
        for field in ("contact", "address"):
            value = data[field] or ""
            for ch in "<>\"'":
                assert ch not in value, f"{field} 残留危险字符 {ch}: {value}"

    async def test_update_supplier_contact_address_sanitized(self, client):
        """QA-08：update 路径同款净化。"""
        res = await client.post(
            "/api/v1/catalog/suppliers", json={"name": "干净供应商"}
        )
        sid = res.json()["data"]["supplier_id"]
        upd = await client.put(
            f"/api/v1/catalog/suppliers/{sid}",
            json={
                "contact": "<script>alert(1)</script>13800",
                "address": "`地址`<b>加粗</b>",
            },
        )
        assert upd.status_code == 200, upd.text
        detail = await client.get(f"/api/v1/catalog/suppliers/{sid}")
        data = detail.json()["data"]
        assert data["contact"] == "scriptalert(1)/script13800"
        assert data["address"] == "地址b加粗/b"

    async def test_create_expense_description_sanitized(self, client):
        """QA-08：费用 description 的 payload 落库前被剥离，读回为净化文本。"""
        res = await client.post(
            "/api/v1/expenses",
            json={
                "category": "other",
                "amount": 20,
                "expense_date": "2026-09-22",
                "description": "<script>alert('v')</script>摊位费花了20块",
            },
        )
        assert res.status_code == 200, res.text
        exp_id = res.json()["data"]["id"]
        listing = await client.get("/api/v1/expenses?start=2026-09-01&end=2026-09-30")
        row = [r for r in listing.json()["data"] if r["id"] == exp_id][0]
        assert row["description"] == "scriptalert(v)/script摊位费花了20块"


# ═══════════════════════════════════════════════════════════════════
# QA-21：POST /staff 创建员工 PIN 必填
# ═══════════════════════════════════════════════════════════════════


class TestStaffPinRequired:
    async def test_create_staff_without_pin_422(self, client, db_session):
        """QA-21：pin_code 缺失 → 422「请设置员工 PIN」，不再落 NULL 的 active 员工。"""
        res = await client.post(
            "/api/v1/staff", json={"name": "无PIN员工", "role": "cashier"}
        )
        assert res.status_code == 422, res.text
        assert "PIN" in res.json()["detail"]
        from app.models.staff import StaffMember

        async with db_session() as session:
            rows = (
                (await session.execute(select(StaffMember).where(StaffMember.name == "无PIN员工")))
                .scalars()
                .all()
            )
            assert rows == []

    async def test_create_staff_empty_pin_422(self, client):
        """QA-21：pin_code 空串同样 → 422。"""
        res = await client.post(
            "/api/v1/staff",
            json={"name": "空PIN员工", "role": "cashier", "pin_code": ""},
        )
        assert res.status_code == 422
        assert "PIN" in res.json()["detail"]

    async def test_create_staff_with_pin_ok(self, client):
        """正常路径：携带合法 PIN → 200。"""
        res = await client.post(
            "/api/v1/staff",
            json={"name": "有PIN员工", "role": "cashier", "pin_code": "123456"},
        )
        assert res.status_code == 200, res.text


# ═══════════════════════════════════════════════════════════════════
# QA-28：字段边界（canonical_unit ≤16；min_order_qty ≥ 0）
# ═══════════════════════════════════════════════════════════════════


class TestFieldBoundaries:
    async def test_create_sku_canonical_unit_too_long_422(self, client):
        """QA-28①：300 字 canonical_unit → 422。"""
        res = await client.post(
            "/api/v1/catalog/skus",
            json={"name": "长单位商品", "canonical_unit": "斤" * 300},
        )
        assert res.status_code == 422, res.text

    async def test_update_sku_canonical_unit_too_long_422(self, client):
        """QA-28①：更新路径同样限制。"""
        sku_id = await _create_sku(client, "单位正常商品")
        res = await client.put(
            f"/api/v1/catalog/skus/{sku_id}", json={"canonical_unit": "x" * 17}
        )
        assert res.status_code == 422, res.text

    async def test_create_sku_canonical_unit_16_ok(self, client):
        """QA-28①：恰好 16 字放行。"""
        res = await client.post(
            "/api/v1/catalog/skus",
            json={"name": "边界单位商品", "canonical_unit": "斤" * 16},
        )
        assert res.status_code == 200, res.text

    async def test_create_supplier_negative_min_order_qty_422(self, client):
        """QA-28②：min_order_qty=-5 → 422。"""
        res = await client.post(
            "/api/v1/catalog/suppliers",
            json={"name": "负起订量供应商", "min_order_qty": -5},
        )
        assert res.status_code == 422, res.text

    async def test_update_supplier_negative_min_order_qty_422(self, client):
        """QA-28②：更新路径同样拒绝负数。"""
        res = await client.post(
            "/api/v1/catalog/suppliers", json={"name": "边界供应商"}
        )
        sid = res.json()["data"]["supplier_id"]
        upd = await client.put(
            f"/api/v1/catalog/suppliers/{sid}", json={"min_order_qty": -5}
        )
        assert upd.status_code == 422, upd.text


# ═══════════════════════════════════════════════════════════════════
# QA-05：POS 可售列表（GET /inventory/current）商户自有 SKU 优先
# ═══════════════════════════════════════════════════════════════════


class TestPosSellableOwnSkuPriority:
    async def _seed_inventory(self, db_session, product_id: int, qty: float = 10):
        from app.models.inventory import InventoryRecord

        async with db_session() as session:
            session.add(
                InventoryRecord(
                    merchant_id=uuid.UUID(TEST_MERCHANT_ID),
                    product_id=product_id,
                    sku_id=None,  # 种子品类批次：sku_id 为空（QA-05 场景）
                    quantity=qty,
                    unit="斤",
                    unit_cost=2.376,
                    event_type="purchase",
                    event_time=datetime(2026, 9, 22, 8, 0, 0),
                    idempotency_key=f"qa05-{product_id}-{uuid.uuid4().hex[:8]}",
                )
            )
            await session.commit()

    async def test_own_sku_name_price_preferred(self, client, db_session):
        """商户建了自有同名 SKU 后，可售列表返回自有 SKU 的名称与价格。"""
        await self._seed_inventory(db_session, product_id=1)  # 种子品类「白菜」
        sku_id = await _create_sku(client, "白菜", default_sale_price=3.5)
        res = await client.get("/api/v1/inventory/current")
        assert res.status_code == 200, res.text
        items = res.json()["data"]
        row = [i for i in items if i["product_id"] == 1][0]
        assert row["own_sku_id"] == sku_id
        assert row["own_sku_name"] == "白菜"
        assert row["own_sku_price"] == 3.5

    async def test_seed_category_fallback_without_own_sku(self, client, db_session):
        """无自有同名 SKU 时 own_sku_* 为 None（种子品类兜底，既有字段不变）。"""
        await self._seed_inventory(db_session, product_id=2)  # 种子品类「土豆」
        res = await client.get("/api/v1/inventory/current")
        items = res.json()["data"]
        row = [i for i in items if i["product_id"] == 2][0]
        assert row["own_sku_id"] is None
        assert row["own_sku_name"] is None
        assert row["own_sku_price"] is None
        assert row["product_name"] == "土豆"  # 既有字段语义不变
