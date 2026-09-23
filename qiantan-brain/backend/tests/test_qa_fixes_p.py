"""QA 修复回归测试（Agent P 域：采购/库存/盘点/离线补账）。

覆盖缺陷：
  - QA-05：自建 SKU 进不了采购单（manual_by_name 只查全局种子品类）
  - QA-06：采购重复商品被静默丢弃（应合并数量 + 返回 merged_count）
  - QA-04：离线补账死信（sku_id=NULL 无主批次不被 sku 过滤消耗命中）
  - QA-20：盘点盘亏落 adjustment 无成本（应改 waste + FIFO 成本实扣）
  - QA-35：采购批次 sku_id=NULL 溯源断层（验收 confirm 优先采购项绑定 SKU）
"""

from __future__ import annotations

import sys
import uuid
from datetime import datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select
from tests.conftest import TEST_MERCHANT_ID


SECOND_MERCHANT_ID = "00000000-0000-0000-0000-000000000002"

pytestmark = pytest.mark.asyncio


# ------------------------------------------------------------------
# 公共夹具
# ------------------------------------------------------------------


async def _create_merchant_sku(session, merchant_id, name, alias=None, price="6.00"):
    """为商户创建一个自有 SKU（可选别名），返回 SKU id。"""
    from app.models.catalog import ProductAlias, ProductSKU

    mid = uuid.UUID(merchant_id) if isinstance(merchant_id, str) else merchant_id
    sku = ProductSKU(
        merchant_id=mid,
        name=name,
        canonical_unit="斤",
        shelf_life_hours=72,
        default_sale_price=Decimal(price),
        category_group="其他",
    )
    session.add(sku)
    await session.flush()
    if alias:
        session.add(ProductAlias(merchant_id=mid, sku_id=sku.id, alias=alias))
    await session.commit()
    return sku.id


async def _seed_purchase_record_and_batch(
    session, merchant_id, product_id, qty, unit_cost, sku_id=None
):
    """同时落一笔采购流水与对应批次（模拟真实入库，账实一致）。"""
    from app.models.batch import BatchLifecycle
    from app.models.inventory import InventoryRecord

    mid = uuid.UUID(merchant_id) if isinstance(merchant_id, str) else merchant_id
    now = datetime.utcnow()
    session.add(
        InventoryRecord(
            merchant_id=mid,
            product_id=product_id,
            sku_id=sku_id,
            quantity=Decimal(str(qty)),
            unit="斤",
            unit_cost=Decimal(str(unit_cost)),
            total_amount=Decimal(str(qty)) * Decimal(str(unit_cost)),
            event_type="purchase",
            event_time=now,
        )
    )
    session.add(
        BatchLifecycle(
            merchant_id=mid,
            product_id=product_id,
            sku_id=sku_id,
            batch_label=f"seed-{uuid.uuid4().hex[:8]}",
            purchase_date=now,
            purchase_qty=Decimal(str(qty)),
            remaining_qty=Decimal(str(qty)),
            expiry_date=now + timedelta(hours=72),
            status="sellable",
            unit_cost=Decimal(str(unit_cost)),
        )
    )
    await session.commit()


# ------------------------------------------------------------------
# QA-05：自建 SKU 进不了采购单
# ------------------------------------------------------------------


async def test_qa05_merchant_sku_can_be_purchased_by_name(client, db_session):
    """商户自建 SKU 同名手动采购应成功入单，且采购项绑定该 SKU（QA-35 联动）。"""
    mid = uuid.UUID(TEST_MERCHANT_ID)
    async with db_session() as session:
        sku_id = await _create_merchant_sku(session, mid, "红富士苹果")

    resp = await client.post(
        "/api/v1/purchase/from-advice",
        json={"items": [{"name": "红富士苹果", "qty": 10, "cost": 3.5, "unit": "斤"}]},
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert data["added_count"] == 1
    assert data["unmatched_items"] == []

    today = await client.get("/api/v1/purchase/today")
    items = today.json()["data"]["items"]
    assert len(items) == 1
    assert items[0]["product_name"] == "红富士苹果"
    assert items[0]["actual_qty"] == 10.0
    assert items[0]["estimated_unit_cost"] == 3.5

    from app.models.product import ProductCategory
    from app.models.purchase import PurchaseItem

    async with db_session() as session:
        item = (await session.execute(select(PurchaseItem))).scalars().first()
        assert item is not None
        # 采购项绑定商户自有 SKU，批次/流水可溯源
        assert str(item.sku_id) == str(sku_id)
        # 同名品类自动补建，外键成立且继承 SKU 档案
        cat = await session.scalar(
            select(ProductCategory).where(ProductCategory.name == "红富士苹果")
        )
        assert cat is not None
        assert cat.unit == "斤"
        assert float(cat.default_price) == 6.0


async def test_qa05_merchant_sku_alias_can_be_purchased(client, db_session):
    """按别名采购同样命中商户自有 SKU。"""
    mid = uuid.UUID(TEST_MERCHANT_ID)
    async with db_session() as session:
        sku_id = await _create_merchant_sku(session, mid, "番茄", alias="西红柿")

    resp = await client.post(
        "/api/v1/purchase/from-advice",
        json={"items": [{"name": "西红柿", "qty": 8, "unit": "斤"}]},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["data"]["added_count"] == 1

    from app.models.product import ProductCategory
    from app.models.purchase import PurchaseItem

    async with db_session() as session:
        item = (await session.execute(select(PurchaseItem))).scalars().first()
        assert str(item.sku_id) == str(sku_id)
        cat = await session.get(ProductCategory, item.product_id)
        assert cat.name == "番茄"


async def test_qa05_other_merchants_sku_do_not_match(client, db_session):
    """商户隔离：别家商户的 SKU 不能被本商户按名采购到。"""
    async with db_session() as session:
        await _create_merchant_sku(
            session, SECOND_MERCHANT_ID, "别家专属菜"
        )

    resp = await client.post(
        "/api/v1/purchase/from-advice",
        json={"items": [{"name": "别家专属菜", "qty": 5, "unit": "斤"}]},
    )
    assert resp.status_code == 400
    assert "商品目录中未找到" in resp.json()["detail"]


async def test_qa05_seeded_category_flow_still_works(client):
    """向后兼容：全局种子品类（白菜）按名采购不受影响。"""
    resp = await client.post(
        "/api/v1/purchase/from-advice",
        json={"items": [{"name": "白菜", "qty": 5, "unit": "斤"}]},
    )
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["added_count"] == 1
    assert data["unmatched_items"] == []


# ------------------------------------------------------------------
# QA-06：重复商品静默丢弃 → 合并数量
# ------------------------------------------------------------------


async def test_qa06_duplicate_item_merges_quantity(client):
    """清单已有白菜×10 再录白菜×15 → 合并为 25，merged_count=1。"""
    first = await client.post(
        "/api/v1/purchase/from-advice",
        json={"items": [{"name": "白菜", "qty": 10, "cost": 1.0, "unit": "斤"}]},
    )
    assert first.status_code == 200
    assert first.json()["data"]["added_count"] == 1
    # QA-06：向后兼容字段 —— 首次提交 merged_count=0
    assert first.json()["data"]["merged_count"] == 0

    second = await client.post(
        "/api/v1/purchase/from-advice",
        json={"items": [{"name": "白菜", "qty": 15, "cost": 2.0}]},
    )
    assert second.status_code == 200
    data = second.json()["data"]
    assert data["added_count"] == 0
    assert data["merged_count"] == 1
    assert data["item_count"] == 1
    assert "合并" in second.json()["message"]

    today = await client.get("/api/v1/purchase/today")
    items = today.json()["data"]["items"]
    assert len(items) == 1
    item = items[0]
    assert item["actual_qty"] == 25.0
    assert item["recommended_qty"] == 25.0
    # 新提交进价生效，合计按合并量重算
    assert item["estimated_unit_cost"] == 2.0
    assert item["actual_unit_cost"] == 2.0
    assert item["estimated_cost"] == 50.0
    assert item["actual_cost"] == 50.0


async def test_qa06_merge_keeps_prior_cost_when_new_cost_missing(client):
    """合并时未提交新进价 → 沿用原行单价重算合计。"""
    first = await client.post(
        "/api/v1/purchase/from-advice",
        json={"items": [{"name": "土豆", "qty": 10, "cost": 1.2, "unit": "斤"}]},
    )
    assert first.status_code == 200

    second = await client.post(
        "/api/v1/purchase/from-advice",
        json={"items": [{"name": "土豆", "qty": 10, "unit": "斤"}]},
    )
    assert second.status_code == 200
    assert second.json()["data"]["merged_count"] == 1

    today = await client.get("/api/v1/purchase/today")
    item = today.json()["data"]["items"][0]
    assert item["actual_qty"] == 20.0
    assert item["estimated_unit_cost"] == 1.2
    assert item["estimated_cost"] == 24.0


async def test_qa06_duplicate_within_one_request_merges(client):
    """同一次请求内重复提交同商品也合并，不产生重复行。"""
    resp = await client.post(
        "/api/v1/purchase/from-advice",
        json={
            "items": [
                {"name": "白菜", "qty": 3, "unit": "斤"},
                {"name": "白菜", "qty": 4, "unit": "斤"},
            ]
        },
    )
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["added_count"] == 1
    assert data["merged_count"] == 1
    assert data["item_count"] == 1

    today = await client.get("/api/v1/purchase/today")
    assert today.json()["data"]["items"][0]["actual_qty"] == 7.0


# ------------------------------------------------------------------
# QA-04：离线补账死信（无主批次 sku_id=NULL 回退消耗）
# ------------------------------------------------------------------


async def test_qa04_offline_sync_consumes_unowned_batches(client, db_session):
    """语音/POS 兜底产生的 sku_id=NULL 批次：offline-sync 销售应能回退消耗。"""
    mid = uuid.UUID(TEST_MERCHANT_ID)
    # 库存 16 斤挂在无主批次上（模拟语音/POS 兜底建账，sku_id=NULL）
    async with db_session() as session:
        await _seed_purchase_record_and_batch(
            session, mid, 1, qty=16, unit_cost="1.0", sku_id=None
        )

    payload = {
        "items": [
            {
                "idempotency_key": "qa04-offline-sale-001",
                "event_type": "sale",
                "product_name": "白菜",
                "quantity": 2,
                "unit": "斤",
                "unit_price": 3.5,
                "total_amount": 7,
                "source": "offline",
            }
        ]
    }
    resp = await client.post("/api/v1/inventory/offline-sync", json=payload)
    assert resp.status_code == 200, resp.text
    assert resp.json()["data"]["created"] == 1, resp.json()["data"]
    assert resp.json()["data"]["failed"] == 0

    from app.models.batch import BatchLifecycle
    from app.models.inventory import InventoryRecord

    async with db_session() as session:
        batch = (
            (
                await session.execute(
                    select(BatchLifecycle).where(BatchLifecycle.merchant_id == mid)
                )
            )
            .scalars()
            .first()
        )
        # 无主批次被正确扣减：16 - 2 = 14
        assert float(batch.remaining_qty) == 14.0
        sale = (
            (
                await session.execute(
                    select(InventoryRecord).where(
                        InventoryRecord.idempotency_key == "qa04-offline-sale-001"
                    )
                )
            )
            .scalars()
            .one()
        )
        assert sale.event_type == "sale"
        # 幂等键与流水字段语义不变；补账流水仍带归一化后的 sku_id
        assert sale.sku_id is not None
        # QA2-03：流水数量按事件类型归一符号（sale 恒负），与批次扣减同向；
        # 此前按客户端原样存 +2 造成台账/批次分歧
        assert float(sale.quantity) == -2.0


# ------------------------------------------------------------------
# QA-20：盘点盘亏落 waste + FIFO 成本实扣
# ------------------------------------------------------------------


async def _run_stocktake_with_actual(client, actual_qty):
    """以「账面=夹具入账」为前提完成一次盘点，返回 complete 响应。"""
    start = await client.post("/api/v1/inventory/stocktake/start", json={})
    assert start.status_code == 200, start.text
    start_data = start.json()["data"]
    session_id = start_data["session_id"]
    for item in start_data["items"]:
        actual = actual_qty if item["product_id"] == 1 else item["book_qty"]
        resp = await client.post(
            f"/api/v1/inventory/stocktake/{session_id}/submit",
            json={"product_id": item["product_id"], "actual_qty": actual},
        )
        assert resp.status_code == 200
    complete = await client.post(f"/api/v1/inventory/stocktake/{session_id}/complete", json={})
    assert complete.status_code == 200, complete.text
    return complete.json()["data"]


async def test_qa20_stocktake_loss_records_waste_with_fifo_cost(client, db_session):
    """盘亏 1 斤：落 waste（成本 FIFO 实扣）、批次同步扣减、日报含报损金额。"""
    mid = uuid.UUID(TEST_MERCHANT_ID)
    async with db_session() as session:
        await _seed_purchase_record_and_batch(session, mid, 1, qty=10, unit_cost="1.2")

    data = await _run_stocktake_with_actual(client, actual_qty=9)
    assert data["total_variance"] == -1.0
    assert len(data["adjustments"]) == 1

    from app.models.batch import BatchLifecycle
    from app.models.inventory import InventoryRecord

    async with db_session() as session:
        record = (
            (
                await session.execute(
                    select(InventoryRecord).where(
                        InventoryRecord.merchant_id == mid,
                        InventoryRecord.source == "stocktake",
                    )
                )
            )
            .scalars()
            .one()
        )
        # QA-20 核心断言：盘亏按报损口径落账，且带 FIFO 成本
        assert record.event_type == "waste"
        assert float(record.quantity) == -1.0
        assert float(record.unit_cost) == 1.2
        assert float(record.total_amount) == 1.2

        batch = (
            (
                await session.execute(
                    select(BatchLifecycle).where(BatchLifecycle.merchant_id == mid)
                )
            )
            .scalars()
            .one()
        )
        # 批次台账同步扣减：10 - 1 = 9，与流水净和一致（消除 1 斤偏差）
        assert float(batch.remaining_qty) == 9.0

    # 日报报损口径计入盘亏金额
    daily = await client.get("/api/v1/reports/daily")
    assert daily.status_code == 200
    assert daily.json()["data"]["waste_amount"] == 1.2

    # 库存账实一致：流水净和 = 批次余量 = 9
    current = await client.get("/api/v1/inventory/current")
    rows = current.json()["data"]
    row = next(r for r in rows if r["product_id"] == 1)
    assert row["current_qty"] == 9.0


async def test_qa20_stocktake_gain_keeps_adjustment_semantics(client, db_session):
    """盘盈（正差异）保持原 adjustment + 盘盈批次语义不变。"""
    mid = uuid.UUID(TEST_MERCHANT_ID)
    async with db_session() as session:
        await _seed_purchase_record_and_batch(session, mid, 1, qty=5, unit_cost="1.0")

    data = await _run_stocktake_with_actual(client, actual_qty=7)
    assert data["total_variance"] == 2.0

    from app.models.batch import BatchLifecycle
    from app.models.inventory import InventoryRecord

    async with db_session() as session:
        record = (
            (
                await session.execute(
                    select(InventoryRecord).where(
                        InventoryRecord.merchant_id == mid,
                        InventoryRecord.source == "stocktake",
                    )
                )
            )
            .scalars()
            .one()
        )
        assert record.event_type == "adjustment"
        assert float(record.quantity) == 2.0
        batches = (
            (
                await session.execute(
                    select(BatchLifecycle).where(BatchLifecycle.merchant_id == mid)
                )
            )
            .scalars()
            .all()
        )
        # 原入库批次 5 + 盘盈批次 2
        assert sorted(float(b.remaining_qty) for b in batches) == [2.0, 5.0]


async def test_qa20_stocktake_loss_consumes_unowned_batches_via_fallback(
    client, db_session
):
    """盘亏消耗走 QA-04 同款回退：无主批次（sku_id=NULL）也能被扣减。"""
    from app.models.catalog import ProductSKU
    from app.models.batch import BatchLifecycle

    mid = uuid.UUID(TEST_MERCHANT_ID)
    async with db_session() as session:
        await _seed_purchase_record_and_batch(session, mid, 1, qty=6, unit_cost="1.0")
        # 商户建了同名 SKU → complete 会解析出 sku_id，批次却是无主的
        session.add(
            ProductSKU(
                merchant_id=mid,
                name="白菜",
                canonical_unit="斤",
                shelf_life_hours=72,
            )
        )
        await session.commit()

    data = await _run_stocktake_with_actual(client, actual_qty=5)
    assert data["total_variance"] == -1.0

    async with db_session() as session:
        batch = (
            (
                await session.execute(
                    select(BatchLifecycle).where(BatchLifecycle.merchant_id == mid)
                )
            )
            .scalars()
            .one()
        )
        assert float(batch.remaining_qty) == 5.0


# ------------------------------------------------------------------
# QA-35：采购验收批次 sku_id 落库
# ------------------------------------------------------------------


async def test_qa35_acceptance_confirm_batch_carries_sku_id(client, db_session):
    """自建 SKU 采购 → 验收入库后批次/流水均绑定商户 SKU。"""
    from app.models.batch import BatchLifecycle
    from app.models.inventory import InventoryRecord

    mid = uuid.UUID(TEST_MERCHANT_ID)
    async with db_session() as session:
        sku_id = await _create_merchant_sku(session, mid, "进口车厘子", price="30.00")

    created = await client.post(
        "/api/v1/purchase/from-advice",
        json={"items": [{"name": "进口车厘子", "qty": 5, "unit": "斤"}]},
    )
    assert created.status_code == 200, created.text
    list_id = created.json()["data"]["list_id"]

    today = await client.get("/api/v1/purchase/today")
    item_id = today.json()["data"]["items"][0]["item_id"]

    accept = await client.post(
        f"/api/v1/purchase/{list_id}/acceptance",
        json={
            "items": [
                {
                    "item_id": item_id,
                    "arrival_qty": 5,
                    "accepted_qty": 5,
                    "package_count": 1,
                    "quality_ok": True,
                }
            ]
        },
    )
    assert accept.status_code == 200, accept.text

    confirm = await client.post(f"/api/v1/purchase/{list_id}/acceptance/confirm", json={})
    assert confirm.status_code == 200, confirm.text

    async with db_session() as session:
        batch = (
            (
                await session.execute(
                    select(BatchLifecycle).where(
                        BatchLifecycle.merchant_id == mid,
                        BatchLifecycle.batch_label.like("进口车厘子-%"),
                    )
                )
            )
            .scalars()
            .one()
        )
        assert str(batch.sku_id) == str(sku_id)
        record = (
            (
                await session.execute(
                    select(InventoryRecord).where(
                        InventoryRecord.merchant_id == mid,
                        InventoryRecord.source == "purchase_list",
                    )
                )
            )
            .scalars()
            .one()
        )
        assert str(record.sku_id) == str(sku_id)
