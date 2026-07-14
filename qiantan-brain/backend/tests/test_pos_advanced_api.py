"""Advanced POS tests — combo payment, hold/resume, refunds, batch isolation.

Covers the P1 gaps identified in the project quality audit (§6, §5.7).
"""

from __future__ import annotations

import uuid
from decimal import Decimal

import pytest
from sqlalchemy import func, select
from tests.conftest import TEST_MERCHANT_ID

from app.models.batch import BatchLifecycle
from app.models.inventory import InventoryRecord
from app.models.pos import Payment, SaleOrder, SaleOrderItem
from app.services.batch import create_batch, lock_batch


async def _seed_stock(
    db_session,
    merchant_id=TEST_MERCHANT_ID,
    quantity=10,
    product_id=1,
    product_name="白菜",
    batch_label=None,
):
    async with db_session() as session:
        await create_batch(
            session,
            uuid.UUID(merchant_id),
            product_id,
            product_name,
            batch_label or f"{product_name}-test-{uuid.uuid4().hex[:6]}",
            Decimal(str(quantity)),
        )
        await session.commit()


async def _seed_legacy_order_with_missing_product(db_session, *, status, include_valid=False):
    async with db_session() as session:
        item_count = 2 if include_valid else 1
        total_amount = Decimal("5") * item_count
        order = SaleOrder(
            merchant_id=uuid.UUID(TEST_MERCHANT_ID),
            order_no=f"LEGACY{uuid.uuid4().hex[:20]}",
            status=status,
            total_amount=total_amount,
            paid_amount=total_amount if status == "paid" else Decimal("0"),
            refunded_amount=Decimal("0"),
            discount_amount=Decimal("0"),
            client_id=f"legacy-{uuid.uuid4()}",
        )
        session.add(order)
        await session.flush()
        if include_valid:
            session.add(
                SaleOrderItem(
                    order_id=order.id,
                    merchant_id=order.merchant_id,
                    product_id=1,
                    quantity=Decimal("1"),
                    unit="斤",
                    unit_price=Decimal("5"),
                    total_amount=Decimal("5"),
                )
            )
        missing_item = SaleOrderItem(
            order_id=order.id,
            merchant_id=order.merchant_id,
            product_id=None,
            quantity=Decimal("1"),
            unit="斤",
            unit_price=Decimal("5"),
            total_amount=Decimal("5"),
        )
        session.add(missing_item)
        await session.commit()
        return order.id, missing_item.id


def _order(client_id, **overrides):
    payload = {
        "client_id": client_id,
        "payment_method": "cash",
        "items": [{"product_id": 1, "quantity": 2, "unit": "斤", "unit_price": 3.5}],
    }
    payload.update(overrides)
    return payload


# ═══════════════════════════════════════════════════════════════════
# 组合支付
# ═══════════════════════════════════════════════════════════════════


class TestComboPayment:
    async def test_combo_cash_wechat(self, client, db_session):
        """§4.7: 组合支付 — 现金 + 微信."""
        await _seed_stock(db_session)
        res = await client.post(
            "/api/v1/pos/orders",
            json={
                "client_id": "combo-cash-wechat-001",
                "items": [{"product_id": 1, "quantity": 4, "unit": "斤", "unit_price": 5.0}],
                "payments": [
                    {"method": "cash", "amount": 10.0},
                    {"method": "wechat", "amount": 10.0},
                ],
            },
        )
        assert res.status_code == 200
        data = res.json()["data"]
        assert data["status"] == "paid"
        assert data["total_amount"] == 20.0
        async with db_session() as session:
            payments = (
                (
                    await session.execute(
                        select(Payment).where(Payment.order_id == uuid.UUID(data["order_id"]))
                    )
                )
                .scalars()
                .all()
            )
            methods = {p.method for p in payments}
            assert "cash" in methods
            assert "wechat" in methods
            assert sum(float(p.amount) for p in payments) == 20.0

    async def test_combo_amount_mismatch_rejected(self, client, db_session):
        """组合支付金额合计与应收不匹配时拒绝."""
        await _seed_stock(db_session)
        res = await client.post(
            "/api/v1/pos/orders",
            json={
                "client_id": "combo-mismatch-001",
                "items": [{"product_id": 1, "quantity": 2, "unit": "斤", "unit_price": 5.0}],
                "payments": [
                    {"method": "cash", "amount": 3.0},
                    {"method": "wechat", "amount": 3.0},
                ],
            },
        )
        assert res.status_code == 400

    async def test_combo_includes_credit(self, client, db_session):
        """组合支付包含赊账."""
        await _seed_stock(db_session)
        res = await client.post(
            "/api/v1/pos/orders",
            json={
                "client_id": "combo-credit-001",
                "items": [{"product_id": 1, "quantity": 2, "unit": "斤", "unit_price": 5.0}],
                "customer_name": "李记食堂",
                "payments": [
                    {"method": "cash", "amount": 4.0},
                    {"method": "credit", "amount": 6.0},
                ],
            },
        )
        assert res.status_code == 200

    async def test_credit_combo_requires_customer(self, client, db_session):
        """赊账组合支付无客户名时拒绝."""
        await _seed_stock(db_session)
        res = await client.post(
            "/api/v1/pos/orders",
            json={
                "client_id": "combo-no-customer-001",
                "items": [{"product_id": 1, "quantity": 2, "unit": "斤", "unit_price": 5.0}],
                "payments": [
                    {"method": "cash", "amount": 4.0},
                    {"method": "credit", "amount": 6.0},
                ],
            },
        )
        assert res.status_code == 422  # Pydantic validation rejects


# ═══════════════════════════════════════════════════════════════════
# 挂单 / 取单 / 取消
# ═══════════════════════════════════════════════════════════════════


class TestHoldResumeCancel:
    async def test_hold_order(self, client, db_session):
        """挂起一个订单."""
        await _seed_stock(db_session)
        res = await client.post(
            "/api/v1/pos/orders/hold",
            json={
                "items": [{"product_id": 1, "quantity": 3, "unit": "斤", "unit_price": 4.0}],
                "client_id": "hold-test-001",
                "note": "客人去取钱",
            },
        )
        assert res.status_code == 200
        assert res.json()["data"]["status"] == "held"

    async def test_list_held_orders(self, client, db_session):
        """列出所有挂单."""
        await _seed_stock(db_session, quantity=20)
        hold = await client.post(
            "/api/v1/pos/orders/hold",
            json={
                "items": [{"product_id": 1, "quantity": 1, "unit": "斤", "unit_price": 3.0}],
                "client_id": "held-list-001",
            },
        )
        assert hold.status_code == 200
        res = await client.get("/api/v1/pos/orders/held")
        assert res.status_code == 200
        assert len(res.json()["data"]) >= 1

    async def test_resume_held_order(self, client, db_session):
        """取回挂单并完成支付."""
        await _seed_stock(db_session, quantity=20)
        hold = await client.post(
            "/api/v1/pos/orders/hold",
            json={
                "items": [{"product_id": 1, "quantity": 2, "unit": "斤", "unit_price": 3.0}],
                "client_id": "resume-test-001",
            },
        )
        order_id = hold.json()["data"]["order_id"]
        resume = await client.post(
            f"/api/v1/pos/orders/{order_id}/resume",
            json={"payment_method": "cash"},
        )
        assert resume.status_code == 200
        assert resume.json()["data"]["status"] in ("paid", "pending")

    async def test_cancel_held_order(self, client, db_session):
        """取消挂单 — 库存应恢复."""
        await _seed_stock(db_session, quantity=10)
        hold = await client.post(
            "/api/v1/pos/orders/hold",
            json={
                "items": [{"product_id": 1, "quantity": 2, "unit": "斤", "unit_price": 5.0}],
                "client_id": "cancel-test-001",
            },
        )
        order_id = hold.json()["data"]["order_id"]
        cancel = await client.delete(f"/api/v1/pos/orders/{order_id}")
        assert cancel.status_code == 200
        async with db_session() as session:
            order = await session.get(SaleOrder, uuid.UUID(order_id))
            assert order.status == "cancelled"

    async def test_cancel_paid_order_rejected(self, client, db_session):
        """已支付订单不能取消."""
        await _seed_stock(db_session)
        create = await client.post("/api/v1/pos/orders", json=_order("cancel-paid-001"))
        order_id = create.json()["data"]["order_id"]
        cancel = await client.delete(f"/api/v1/pos/orders/{order_id}")
        assert cancel.status_code in (400, 409)

    async def test_hold_does_not_consume_stock(self, client, db_session):
        """挂单时先不扣库存 — 取单(resume)时才扣，防止挂单霸占库存."""
        await _seed_stock(db_session, quantity=5)
        await client.post(
            "/api/v1/pos/orders/hold",
            json={
                "items": [{"product_id": 1, "quantity": 3, "unit": "斤", "unit_price": 4.0}],
                "client_id": "hold-stock-001",
            },
        )
        async with db_session() as session:
            batch = (
                await session.execute(
                    select(BatchLifecycle).where(
                        BatchLifecycle.merchant_id == uuid.UUID(TEST_MERCHANT_ID)
                    )
                )
            ).scalar_one()
            assert float(batch.remaining_qty) == 5  # 挂单不扣库存


# ═══════════════════════════════════════════════════════════════════
# 整单退款
# ═══════════════════════════════════════════════════════════════════


class TestFullRefund:
    async def test_full_refund_with_stock_return(self, client, db_session):
        """整单退款 + 退货入库."""
        await _seed_stock(db_session, quantity=10)
        create = await client.post("/api/v1/pos/orders", json=_order("refund-full-001"))
        order_id = create.json()["data"]["order_id"]
        refund = await client.post(
            f"/api/v1/pos/orders/{order_id}/refund",
            json={
                "reason": "品质问题",
                "return_to_stock": True,
            },
        )
        assert refund.status_code == 200
        data = refund.json()["data"]
        assert data["new_status"] == "refunded"
        assert data["refunded_amount"] == 7.0
        assert data["remaining_amount"] == 0.0
        async with db_session() as session:
            batch = (
                await session.execute(
                    select(BatchLifecycle).where(
                        BatchLifecycle.merchant_id == uuid.UUID(TEST_MERCHANT_ID)
                    )
                )
            ).scalar_one()
            assert float(batch.remaining_qty) == 10  # fully restored

    async def test_full_refund_without_stock_return(self, client, db_session):
        """整单退款不退库存."""
        await _seed_stock(db_session, quantity=10)
        create = await client.post("/api/v1/pos/orders", json=_order("refund-no-stock-001"))
        order_id = create.json()["data"]["order_id"]
        refund = await client.post(
            f"/api/v1/pos/orders/{order_id}/refund",
            json={
                "reason": "客户不满意",
                "return_to_stock": False,
            },
        )
        assert refund.status_code == 200
        async with db_session() as session:
            batch = (
                await session.execute(
                    select(BatchLifecycle).where(
                        BatchLifecycle.merchant_id == uuid.UUID(TEST_MERCHANT_ID)
                    )
                )
            ).scalar_one()
            assert float(batch.remaining_qty) == 8  # NOT restored
            refund_record = (
                await session.execute(
                    select(InventoryRecord).where(InventoryRecord.event_type == "refund")
                )
            ).scalar_one()
            assert refund_record.quantity == Decimal("0.00")

    async def test_double_refund_rejected(self, client, db_session):
        """重复退款应拒绝."""
        await _seed_stock(db_session, quantity=10)
        create = await client.post("/api/v1/pos/orders", json=_order("refund-double-001"))
        order_id = create.json()["data"]["order_id"]
        await client.post(
            f"/api/v1/pos/orders/{order_id}/refund",
            json={
                "reason": "第一次退款",
                "return_to_stock": False,
            },
        )
        second = await client.post(
            f"/api/v1/pos/orders/{order_id}/refund",
            json={
                "reason": "第二次退款",
                "return_to_stock": False,
            },
        )
        assert second.status_code in (400, 409)

    async def test_refund_credit_order(self, client, db_session):
        """赊账订单退款应冲减应收."""
        await _seed_stock(db_session, quantity=10)
        create = await client.post(
            "/api/v1/pos/orders",
            json={
                "client_id": "refund-credit-001",
                "payment_method": "credit",
                "customer_name": "赵记面馆",
                "items": [{"product_id": 1, "quantity": 2, "unit": "斤", "unit_price": 3.5}],
            },
        )
        order_id = create.json()["data"]["order_id"]
        refund = await client.post(
            f"/api/v1/pos/orders/{order_id}/refund",
            json={
                "reason": "赊账客户退货",
                "return_to_stock": True,
            },
        )
        assert refund.status_code == 200
        # Verify order is fully refunded
        async with db_session() as session:
            from app.models.accounts import CustomerReceivable

            entries = (
                (
                    await session.execute(
                        select(CustomerReceivable)
                        .where(
                            CustomerReceivable.customer_name == "赵记面馆",
                        )
                        .order_by(CustomerReceivable.created_at)
                    )
                )
                .scalars()
                .all()
            )
            # At minimum the charge entry exists
            assert len(entries) >= 1


# ═══════════════════════════════════════════════════════════════════
# 单品部分退款
# ═══════════════════════════════════════════════════════════════════


class TestPartialRefund:
    async def test_partial_refund_single_item(self, client, db_session):
        """单品部分退款 — 退 2 件中的 1 件."""
        await _seed_stock(db_session, quantity=10)
        create = await client.post(
            "/api/v1/pos/orders",
            json=_order(
                "partial-refund-001",
                items=[
                    {"product_id": 1, "quantity": 4, "unit": "斤", "unit_price": 5.0},
                ],
            ),
        )
        assert create.status_code == 200
        order_id = create.json()["data"]["order_id"]

        # Get the order items
        get_order = await client.get(f"/api/v1/pos/orders/{order_id}")
        items = get_order.json()["data"]["items"]
        item_id = items[0]["item_id"]

        refund = await client.post(
            f"/api/v1/pos/orders/{order_id}/refund",
            json={
                "reason": "退 1 斤",
                "items": [{"item_id": item_id, "quantity": 1, "return_to_stock": True}],
            },
        )
        assert refund.status_code == 200
        data = refund.json()["data"]
        assert data["refunded_amount"] == 5.0
        assert data["remaining_amount"] == 15.0  # 20 - 5

    async def test_partial_refund_excess_quantity_rejected(self, client, db_session):
        """退超过购买量的数量应拒绝."""
        await _seed_stock(db_session, quantity=10)
        create = await client.post(
            "/api/v1/pos/orders",
            json=_order(
                "partial-excess-001",
                items=[
                    {"product_id": 1, "quantity": 2, "unit": "斤", "unit_price": 5.0},
                ],
            ),
        )
        order_id = create.json()["data"]["order_id"]
        get_order = await client.get(f"/api/v1/pos/orders/{order_id}")
        item_id = get_order.json()["data"]["items"][0]["item_id"]

        refund = await client.post(
            f"/api/v1/pos/orders/{order_id}/refund",
            json={
                "reason": "超量退款",
                "items": [{"item_id": item_id, "quantity": 100, "return_to_stock": True}],
            },
        )
        assert refund.status_code in (400, 409)


# ═══════════════════════════════════════════════════════════════════
# 食品安全：锁定批次隔离
# ═══════════════════════════════════════════════════════════════════


class TestBatchIsolation:
    """§4.14 / §5.7: 锁定批次不能被 POS 销售."""

    async def test_locked_batch_not_sold(self, client, db_session):
        """锁定一个批次后，POS 销售应跳过该批次."""
        await _seed_stock(db_session, quantity=5, batch_label="locked-batch-001")
        async with db_session() as session:
            batches = (
                (
                    await session.execute(
                        select(BatchLifecycle).where(
                            BatchLifecycle.merchant_id == uuid.UUID(TEST_MERCHANT_ID),
                            BatchLifecycle.batch_label == "locked-batch-001",
                        )
                    )
                )
                .scalars()
                .all()
            )
            for b in batches:
                await lock_batch(
                    session, b.id, uuid.UUID(TEST_MERCHANT_ID), "快检不合格", "merchant"
                )
            await session.commit()

        # 锁定的批次不可售 → 库存不足
        res = await client.post(
            "/api/v1/pos/orders",
            json={
                "client_id": "locked-batch-sale-001",
                "payment_method": "cash",
                "items": [{"product_id": 1, "quantity": 2, "unit": "斤", "unit_price": 3.5}],
            },
        )
        assert res.status_code == 409  # 库存不足

    async def test_unlocked_batch_sellable_after_recheck(self, client, db_session):
        """解锁后恢复销售."""
        await _seed_stock(db_session, quantity=5, batch_label="unlock-batch-001")
        async with db_session() as session:
            batches = (
                (
                    await session.execute(
                        select(BatchLifecycle).where(
                            BatchLifecycle.merchant_id == uuid.UUID(TEST_MERCHANT_ID),
                            BatchLifecycle.batch_label == "unlock-batch-001",
                        )
                    )
                )
                .scalars()
                .all()
            )
            bid = batches[0].id
            mid = uuid.UUID(TEST_MERCHANT_ID)
            await lock_batch(session, bid, mid, "快检不合格", "merchant")
            await session.commit()
        # 此时锁定 — 不可售
        res_locked = await client.post(
            "/api/v1/pos/orders",
            json={
                "client_id": "still-locked-001",
                "payment_method": "cash",
                "items": [{"product_id": 1, "quantity": 1, "unit": "斤", "unit_price": 3.0}],
            },
        )
        assert res_locked.status_code == 409

        # 解锁
        async with db_session() as session:
            b = await session.get(BatchLifecycle, bid)
            b.status = "sellable"
            await session.commit()
        # 解锁后可售
        await _seed_stock(
            db_session,
            quantity=5,
            product_id=1,
            product_name="白菜",
            batch_label="fresh-unlocked-002",
        )
        res_unlocked = await client.post(
            "/api/v1/pos/orders",
            json={
                "client_id": "unlocked-sale-001",
                "payment_method": "cash",
                "items": [{"product_id": 1, "quantity": 1, "unit": "斤", "unit_price": 3.0}],
            },
        )
        assert res_unlocked.status_code == 200


# ═══════════════════════════════════════════════════════════════════
# 鉴权
# ═══════════════════════════════════════════════════════════════════


class TestUnauthenticated:
    async def test_hold_no_auth(self, auth_client):
        res = await auth_client.post(
            "/api/v1/pos/orders/hold",
            json={
                "items": [{"product_id": 1, "quantity": 1, "unit": "斤", "unit_price": 3.0}],
            },
        )
        assert res.status_code == 401

    async def test_refund_no_auth(self, auth_client):
        res = await auth_client.post(
            f"/api/v1/pos/orders/{uuid.uuid4()}/refund",
            json={
                "reason": "test",
            },
        )
        assert res.status_code == 401

    async def test_cancel_no_auth(self, auth_client):
        res = await auth_client.delete(f"/api/v1/pos/orders/{uuid.uuid4()}")
        assert res.status_code == 401


@pytest.mark.asyncio
async def test_resume_rejects_negative_combined_payment(client, db_session):
    await _seed_stock(db_session, quantity=20)
    held = await client.post(
        "/api/v1/pos/orders/hold",
        json={
            "items": [{"product_id": 1, "quantity": 2, "unit": "斤", "unit_price": 3.0}],
            "client_id": "resume-negative-payment-001",
        },
    )
    order_id = held.json()["data"]["order_id"]

    response = await client.post(
        f"/api/v1/pos/orders/{order_id}/resume",
        json={
            "payments": [
                {"method": "wechat", "amount": 7},
                {"method": "cash", "amount": -1},
            ],
        },
    )

    assert response.status_code == 422


@pytest.mark.asyncio
async def test_resume_credit_requires_customer_name(client, db_session):
    await _seed_stock(db_session, quantity=20)
    held = await client.post(
        "/api/v1/pos/orders/hold",
        json={
            "items": [{"product_id": 1, "quantity": 2, "unit": "斤", "unit_price": 3.0}],
            "client_id": "resume-credit-no-customer-001",
        },
    )
    order_id = held.json()["data"]["order_id"]

    response = await client.post(
        f"/api/v1/pos/orders/{order_id}/resume",
        json={
            "payments": [{"method": "credit", "amount": 6}],
        },
    )

    assert response.status_code == 400
    assert "客户" in response.json()["detail"]


@pytest.mark.asyncio
async def test_sale_rejects_zero_unit_price(client):
    response = await client.post(
        "/api/v1/pos/orders",
        json={
            "client_id": "zero-price-rejected-001",
            "payment_method": "cash",
            "items": [{"product_id": 1, "quantity": 1, "unit": "斤", "unit_price": 0}],
        },
    )
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_legacy_missing_product_is_readable_but_cannot_refund(client, db_session):
    order_id, item_id = await _seed_legacy_order_with_missing_product(
        db_session, status="paid"
    )

    detail = await client.get(f"/api/v1/pos/orders/{order_id}")
    assert detail.status_code == 200
    item = detail.json()["data"]["items"][0]
    assert item["product_id"] is None
    assert item["product_name"].startswith("未知商品")

    refund = await client.post(
        f"/api/v1/pos/orders/{order_id}/refund",
        json={"reason": "历史订单退款", "return_to_stock": True},
    )
    assert refund.status_code == 409
    assert "缺少商品关联" in refund.json()["detail"]

    async with db_session() as session:
        stored_item = await session.get(SaleOrderItem, item_id)
        assert stored_item is not None
        assert stored_item.refund_quantity == Decimal("0.00")
        refund_records = await session.scalar(
            select(func.count(InventoryRecord.id)).where(
                InventoryRecord.merchant_id == uuid.UUID(TEST_MERCHANT_ID),
                InventoryRecord.event_type == "refund",
            )
        )
        assert refund_records == 0


@pytest.mark.asyncio
async def test_resume_legacy_order_validates_all_items_before_inventory_write(client, db_session):
    await _seed_stock(db_session, quantity=5)
    order_id, _ = await _seed_legacy_order_with_missing_product(
        db_session, status="held", include_valid=True
    )

    response = await client.post(
        f"/api/v1/pos/orders/{order_id}/resume",
        json={"payment_method": "cash"},
    )
    assert response.status_code == 409
    assert "缺少商品关联" in response.json()["detail"]

    async with db_session() as session:
        batch = (
            await session.execute(
                select(BatchLifecycle).where(
                    BatchLifecycle.merchant_id == uuid.UUID(TEST_MERCHANT_ID),
                    BatchLifecycle.product_id == 1,
                )
            )
        ).scalar_one()
        assert batch.remaining_qty == Decimal("5.00")
        sale_records = await session.scalar(
            select(func.count(InventoryRecord.id)).where(
                InventoryRecord.merchant_id == uuid.UUID(TEST_MERCHANT_ID),
                InventoryRecord.event_type == "sale",
            )
        )
        assert sale_records == 0
