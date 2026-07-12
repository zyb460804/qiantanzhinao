"""AI action execution integration tests — real business side effects.

Validates that execute_action calls real services:
  price → writes PriceHistory
  purchase → creates PurchaseList + PurchaseItems
  clearance → updates multiple SKU prices
  lock_batch → locks a real batch via food_safety service
"""

from __future__ import annotations

import uuid
from decimal import Decimal

import pytest
from sqlalchemy import select
from tests.conftest import TEST_MERCHANT_ID

from app.models.ai_action import AIAction
from app.models.audit import AuditLog
from app.models.catalog import PriceHistory, ProductSKU
from app.models.purchase import PurchaseItem, PurchaseList
from app.services.batch import create_batch


async def _seed_skus(db_session, count=3):
    """Seed test SKUs for AI price/clearance tests."""
    async with db_session() as session:
        skus = []
        for i in range(count):
            sku = ProductSKU(
                merchant_id=uuid.UUID(TEST_MERCHANT_ID),
                name=f"AI测试商品{i+1}",
                default_sale_price=Decimal(str(5.0 + i * 2)),
                canonical_unit="斤",
                category_group=f"test-cat-{i}",
            )
            session.add(sku)
            skus.append(sku)
        await session.commit()
        for s in skus:
            await session.refresh(s)
        return [str(s.id) for s in skus]


async def _seed_batch_for_lock(db_session):
    """Seed a batch for lock_batch action test."""
    async with db_session() as session:
        batch = await create_batch(
            session, uuid.UUID(TEST_MERCHANT_ID), 1, "白菜",
            f"ai-lock-test-{uuid.uuid4().hex[:6]}", Decimal("10"),
        )
        await session.commit()
        await session.refresh(batch)
        return str(batch.id)


# ═══════════════════════════════════════════════════════════════════
# Price (改价)
# ═══════════════════════════════════════════════════════════════════


class TestExecutePriceAction:
    async def test_execute_price_writes_price_history(self, client, db_session):
        """§4.11: 改价动作执行后写 PriceHistory + 更新 SKU 售价."""
        sku_ids = await _seed_skus(db_session, count=1)
        sku_id = sku_ids[0]

        gen = await client.post("/api/v1/ai-actions/generate", json={
            "actions": [{
                "action_type": "price",
                "title": "降价测试",
                "payload": {"sku_id": sku_id, "new_price": 2.5},
            }],
        })
        action_id = gen.json()["data"][0]["id"]

        res = await client.post(f"/api/v1/ai-actions/{action_id}/execute")
        assert res.status_code == 200
        assert res.json()["data"]["status"] == "executed"

        async with db_session() as session:
            # PriceHistory written
            ph = (await session.execute(
                select(PriceHistory).where(PriceHistory.source == "ai")
            )).scalars().all()
            assert len(ph) >= 1
            assert ph[0].reason == "ai_discount"

            # SKU price updated
            sku = await session.get(ProductSKU, uuid.UUID(sku_id))
            assert float(sku.default_sale_price) == 2.5

            # Audit log
            logs = (await session.execute(
                select(AuditLog).where(AuditLog.action == "ai_price")
            )).scalars().all()
            assert len(logs) == 1

    async def test_price_action_invalid_sku(self, client, db_session):
        """不存在的 SKU 应该 400."""
        gen = await client.post("/api/v1/ai-actions/generate", json={
            "actions": [{
                "action_type": "price",
                "title": "invalid sku",
                "payload": {"sku_id": str(uuid.uuid4()), "new_price": 5.0},
            }],
        })
        action_id = gen.json()["data"][0]["id"]
        res = await client.post(f"/api/v1/ai-actions/{action_id}/execute")
        assert res.status_code == 400


# ═══════════════════════════════════════════════════════════════════
# Purchase (生成采购单)
# ═══════════════════════════════════════════════════════════════════


class TestExecutePurchaseAction:
    async def test_execute_purchase_creates_list(self, client, db_session):
        """§4.11: 采购动作执行后创建真实采购清单."""
        gen = await client.post("/api/v1/ai-actions/generate", json={
            "actions": [{
                "action_type": "purchase",
                "title": "建议采购白菜50斤",
                "payload": {
                    "items": [
                        {"product_id": 1, "qty": 50, "unit": "斤", "cost": 1.5},
                        {"product_id": 2, "qty": 30, "unit": "斤", "cost": 2.0},
                    ],
                    "total_cost": 135,
                },
            }],
        })
        action_id = gen.json()["data"][0]["id"]

        res = await client.post(f"/api/v1/ai-actions/{action_id}/execute")
        assert res.status_code == 200
        data = res.json()["data"]
        assert data["status"] == "executed"
        list_id = data["result"]["list_id"]

        async with db_session() as session:
            plist = await session.get(PurchaseList, uuid.UUID(list_id))
            assert plist is not None
            assert plist.status == "draft"
            assert plist.item_count == 2

            items = (await session.execute(
                select(PurchaseItem).where(PurchaseItem.list_id == uuid.UUID(list_id))
            )).scalars().all()
            assert len(items) == 2

    async def test_purchase_action_empty_items(self, client, db_session):
        """空采购清单应该 400."""
        gen = await client.post("/api/v1/ai-actions/generate", json={
            "actions": [{
                "action_type": "purchase",
                "title": "空清单",
                "payload": {"items": [], "total_cost": 0},
            }],
        })
        action_id = gen.json()["data"][0]["id"]
        res = await client.post(f"/api/v1/ai-actions/{action_id}/execute")
        assert res.status_code == 400


# ═══════════════════════════════════════════════════════════════════
# Clearance (清货降价)
# ═══════════════════════════════════════════════════════════════════


class TestExecuteClearanceAction:
    async def test_execute_clearance_updates_multiple_skus(self, client, db_session):
        """§4.11: 清货动作批量更新多个 SKU 售价."""
        sku_ids = await _seed_skus(db_session, count=3)

        gen = await client.post("/api/v1/ai-actions/generate", json={
            "actions": [{
                "action_type": "clearance",
                "title": "临期清货批量降价",
                "payload": {
                    "skus": [
                        {"sku_id": sku_ids[0], "new_price": 2.0},
                        {"sku_id": sku_ids[1], "new_price": 3.0},
                        {"sku_id": sku_ids[2], "new_price": 4.0},
                    ],
                },
            }],
        })
        action_id = gen.json()["data"][0]["id"]

        res = await client.post(f"/api/v1/ai-actions/{action_id}/execute")
        assert res.status_code == 200
        assert res.json()["data"]["result"]["updated"] == 3

        async with db_session() as session:
            for i, sid in enumerate(sku_ids):
                sku = await session.get(ProductSKU, uuid.UUID(sid))
                expected = [2.0, 3.0, 4.0][i]
                assert float(sku.default_sale_price) == expected

            # All three should have PriceHistory entries
            ph = (await session.execute(
                select(PriceHistory).where(PriceHistory.source == "ai")
            )).scalars().all()
            assert len(ph) == 3
            for p in ph:
                assert p.reason == "clearance"


# ═══════════════════════════════════════════════════════════════════
# Lock Batch (锁定批次)
# ═══════════════════════════════════════════════════════════════════


class TestExecuteLockBatchAction:
    async def test_execute_lock_batch(self, client, db_session):
        """§4.11: 锁定批次动作调用真实 batch service."""
        batch_id = await _seed_batch_for_lock(db_session)

        gen = await client.post("/api/v1/ai-actions/generate", json={
            "actions": [{
                "action_type": "lock_batch",
                "title": "AI检测风险建议锁定批次",
                "payload": {
                    "batch_id": batch_id,
                    "reason": "AI快检风险预警",
                },
            }],
        })
        action_id = gen.json()["data"][0]["id"]

        res = await client.post(f"/api/v1/ai-actions/{action_id}/execute")
        assert res.status_code == 200
        assert res.json()["data"]["status"] == "executed"

        async with db_session() as session:
            from app.models.batch import BatchLifecycle
            batch = await session.get(BatchLifecycle, uuid.UUID(batch_id))
            assert batch.status == "locked"
            assert batch.locked_reason == "AI快检风险预警"
            assert batch.locked_by == "ai_action"


# ═══════════════════════════════════════════════════════════════════
# 鉴权
# ═══════════════════════════════════════════════════════════════════


class TestUnauthenticated:
    """Auth tests covered in test_ai_actions_api.py — skip here."""
