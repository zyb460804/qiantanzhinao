"""POS 下单单位换算接入（A1 契约 convert_to_base_unit）的回归测试。

A1 的 app/services/unit_conversion.py 可能尚未落地，测试通过 sys.modules
注入契约桩（`async def convert_to_base_unit(session, sku_id, quantity,
from_unit) -> (换算数量, 基准单位) | None`）完成验证：
1. 有换算：按换算后数量扣库存，库存流水记基准单位，订单行保留下单单位。
2. 无换算且单位不一致：409，文案引导「需先在商品目录设置单位换算」，不扣库存。
3. 单位一致：跳过换算服务，直接按原数量扣。
4. 真实服务联调：A1 的 unit_conversion.py 落地后，种 UnitConversion 表行直接走真实现。
"""

from __future__ import annotations

import sys
import types
import uuid
from decimal import Decimal

import pytest
from sqlalchemy import select
from tests.conftest import TEST_MERCHANT_ID

from app.models.batch import BatchLifecycle
from app.models.catalog import ProductSKU
from app.models.inventory import InventoryRecord
from app.models.pos import SaleOrderItem
from app.services.batch import create_batch


MERCH = uuid.UUID(TEST_MERCHANT_ID)
BASKET_TO_JIN = Decimal("45")


@pytest.fixture
def conversion_stub(monkeypatch):
    """注入 A1 契约桩：筐→斤（1筐=45斤）；其他单位无换算返回 None。"""

    async def convert_to_base_unit(session, sku_id, quantity, from_unit):
        if from_unit == "筐":
            return Decimal(str(quantity)) * BASKET_TO_JIN, "斤"
        return None

    stub = types.ModuleType("app.services.unit_conversion")
    stub.convert_to_base_unit = convert_to_base_unit
    monkeypatch.setitem(sys.modules, "app.services.unit_conversion", stub)
    return stub


async def _seed_sku_stock(db_session, quantity=100) -> uuid.UUID:
    """创建白菜 SKU（基准单位斤）+ 一批库存，返回 sku_id。"""
    async with db_session() as session:
        sku = ProductSKU(
            merchant_id=MERCH,
            name="白菜",
            canonical_unit="斤",
            default_sale_price=Decimal("2"),
        )
        session.add(sku)
        await session.flush()
        await create_batch(
            session,
            MERCH,
            1,
            "白菜",
            f"白菜-conv-{uuid.uuid4().hex[:8]}",
            Decimal(str(quantity)),
            sku_id=sku.id,
            unit_cost=Decimal("1"),
        )
        await session.commit()
        return sku.id


@pytest.mark.asyncio
async def test_order_with_conversion_deducts_base_unit_stock(
    client, db_session, conversion_stub
):
    """2 筐（×45）→ 按 90 斤扣库存；订单行保留 2 筐；流水记 -90 斤。"""
    sku_id = await _seed_sku_stock(db_session, quantity=100)
    resp = await client.post(
        "/api/v1/pos/orders",
        json={
            "client_id": "pos-conv-001",
            "payment_method": "cash",
            "items": [
                {
                    "product_id": 1,
                    "sku_id": str(sku_id),
                    "quantity": 2,
                    "unit": "筐",
                    "unit_price": 90,
                }
            ],
        },
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["data"]["total_amount"] == 180.0

    async with db_session() as session:
        batch = (await session.execute(select(BatchLifecycle))).scalar_one()
        assert float(batch.remaining_qty) == 10  # 100 - 2筐×45斤

        record = (
            (
                await session.execute(
                    select(InventoryRecord).where(InventoryRecord.event_type == "sale")
                )
            )
            .scalars()
            .one()
        )
        assert float(record.quantity) == -90
        assert record.unit == "斤"

        item = (await session.execute(select(SaleOrderItem))).scalars().one()
        assert float(item.quantity) == 2
        assert item.unit == "筐"
        assert float(item.unit_cost) == 45.0  # 总成本90 / 2筐


@pytest.mark.asyncio
async def test_unit_mismatch_without_conversion_returns_409_guidance(
    client, db_session, conversion_stub
):
    """无换算且单位不一致 → 409 含引导文案，库存不动、订单不落。"""
    sku_id = await _seed_sku_stock(db_session, quantity=100)
    resp = await client.post(
        "/api/v1/pos/orders",
        json={
            "client_id": "pos-conv-002",
            "payment_method": "cash",
            "items": [
                {
                    "product_id": 1,
                    "sku_id": str(sku_id),
                    "quantity": 1,
                    "unit": "袋",
                    "unit_price": 5,
                }
            ],
        },
    )
    assert resp.status_code == 409
    assert "需先在商品目录设置单位换算" in resp.json()["detail"]

    async with db_session() as session:
        batch = (await session.execute(select(BatchLifecycle))).scalar_one()
        assert float(batch.remaining_qty) == 100
        sales = (
            (
                await session.execute(
                    select(InventoryRecord).where(InventoryRecord.event_type == "sale")
                )
            )
            .scalars()
            .all()
        )
        assert sales == []


@pytest.mark.asyncio
async def test_matching_unit_skips_conversion_service(client, db_session, conversion_stub):
    """下单单位 == SKU 基准单位 → 不调用换算服务，直接按原数量扣。"""
    calls: list[str] = []
    original = conversion_stub.convert_to_base_unit

    async def spy(session, sku_id, quantity, from_unit):
        calls.append(from_unit)
        return await original(session, sku_id, quantity, from_unit)

    conversion_stub.convert_to_base_unit = spy

    sku_id = await _seed_sku_stock(db_session, quantity=100)
    resp = await client.post(
        "/api/v1/pos/orders",
        json={
            "client_id": "pos-conv-003",
            "payment_method": "cash",
            "items": [
                {
                    "product_id": 1,
                    "sku_id": str(sku_id),
                    "quantity": 3,
                    "unit": "斤",
                    "unit_price": 2,
                }
            ],
        },
    )
    assert resp.status_code == 200, resp.text
    assert calls == [], "单位一致时不应触发换算查询"

    async with db_session() as session:
        batch = (await session.execute(select(BatchLifecycle))).scalar_one()
        assert float(batch.remaining_qty) == 97


@pytest.mark.asyncio
async def test_real_conversion_service_used_when_module_present(client, db_session):
    """联调（不注入桩）：真实 app/services/unit_conversion.py + UnitConversion 表。

    种一条 SKU 专属换算「筐→斤 ×45」后下单 2 筐，必须按 90 斤扣库存。
    """
    from app.models.catalog import UnitConversion

    async with db_session() as session:
        sku = ProductSKU(
            merchant_id=MERCH, name="白菜", canonical_unit="斤", default_sale_price=Decimal("2")
        )
        session.add(sku)
        await session.flush()
        await create_batch(
            session,
            MERCH,
            1,
            "白菜",
            f"白菜-realconv-{uuid.uuid4().hex[:8]}",
            Decimal("100"),
            sku_id=sku.id,
            unit_cost=Decimal("1"),
        )
        session.add(
            UnitConversion(
                merchant_id=MERCH,
                from_unit="筐",
                to_unit="斤",
                factor=Decimal("45"),
                sku_id=sku.id,
            )
        )
        await session.commit()
        sku_id = sku.id

    resp = await client.post(
        "/api/v1/pos/orders",
        json={
            "client_id": "pos-conv-real-001",
            "payment_method": "cash",
            "items": [
                {
                    "product_id": 1,
                    "sku_id": str(sku_id),
                    "quantity": 2,
                    "unit": "筐",
                    "unit_price": 90,
                }
            ],
        },
    )
    assert resp.status_code == 200, resp.text

    async with db_session() as session:
        batch = (await session.execute(select(BatchLifecycle))).scalar_one()
        assert float(batch.remaining_qty) == 10
        record = (
            (
                await session.execute(
                    select(InventoryRecord).where(InventoryRecord.event_type == "sale")
                )
            )
            .scalars()
            .one()
        )
        assert float(record.quantity) == -90
        assert record.unit == "斤"
