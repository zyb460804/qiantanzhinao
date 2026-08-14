"""POS 资金链路回归测试 — 赊账退款 / 折扣摊分 / 组合支付日结 / 自动对账落库.

覆盖本轮修复的四个资金口径问题：
- Fix 1: 纯赊账订单退款必须冲销应收（credit Payment 行打通退款链路）。
- Fix 2: 折扣订单退款按实付比例摊分，refunded_amount 不超实收。
- Fix 3: 组合支付含 credit 的订单日结 diff_amount == 0。
- Fix 4: 支付后自动对账结果持久化到 DB。
"""

from __future__ import annotations

import uuid
from decimal import Decimal

import pytest
from sqlalchemy import select
from tests.conftest import TEST_MERCHANT_ID

from app.core.timezone import local_now
from app.models.accounts import CustomerReceivable
from app.models.payment import ReconciliationDifference, ReconciliationTask
from app.models.pos import Payment, SaleOrder
from app.services.accounts_service import get_customer_balance
from app.services.batch import create_batch


async def _seed_stock(db_session, quantity=20):
    async with db_session() as session:
        await create_batch(
            session,
            uuid.UUID(TEST_MERCHANT_ID),
            1,
            "白菜",
            f"白菜-moneyflow-{uuid.uuid4().hex[:6]}",
            Decimal(str(quantity)),
        )
        await session.commit()


def _items(qty, price):
    return [{"product_id": 1, "quantity": qty, "unit": "斤", "unit_price": price}]


# ═══════════════════════════════════════════════════════════════════
# Fix 1: 纯赊账订单退款冲销应收
# ═══════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_pure_credit_full_refund_clears_receivable(client, db_session):
    """纯赊账单整单退款 → 客户应收余额归零、订单 refunded、反向 credit 流水落库."""
    await _seed_stock(db_session, quantity=10)
    create = await client.post(
        "/api/v1/pos/orders",
        json={
            "client_id": "moneyflow-credit-refund-001",
            "payment_method": "credit",
            "customer_name": "钱记大排档",
            "items": _items(2, 5.0),
        },
    )
    assert create.status_code == 200
    assert create.json()["data"]["status"] == "credit"
    order_id = uuid.UUID(create.json()["data"]["order_id"])

    async with db_session() as session:
        # 创建即挂账 10 元
        assert await get_customer_balance(session, uuid.UUID(TEST_MERCHANT_ID), "钱记大排档") == (
            Decimal("10")
        )
        credit_rows = (
            (
                await session.execute(
                    select(Payment).where(
                        Payment.order_id == order_id, Payment.method == "credit"
                    )
                )
            )
            .scalars()
            .all()
        )
        assert [(p.status, float(p.amount)) for p in credit_rows] == [("success", 10.0)]

    refund = await client.post(
        f"/api/v1/pos/orders/{order_id}/refund",
        json={"reason": "赊账整单退货", "return_to_stock": True},
    )
    assert refund.status_code == 200
    data = refund.json()["data"]
    assert data["new_status"] == "refunded"
    assert data["refunded_amount"] == 10.0
    assert data["remaining_amount"] == 0.0

    async with db_session() as session:
        # 应收余额归零（charge 10 - 退款冲减 10）
        assert await get_customer_balance(session, uuid.UUID(TEST_MERCHANT_ID), "钱记大排档") == (
            Decimal("0")
        )
        entries = (
            (
                await session.execute(
                    select(CustomerReceivable).where(
                        CustomerReceivable.customer_name == "钱记大排档"
                    )
                )
            )
            .scalars()
            .all()
        )
        assert [(e.direction, float(e.amount)) for e in entries] == [
            ("charge", 10.0),
            ("repay", 10.0),
        ]
        reverse_rows = (
            (
                await session.execute(
                    select(Payment).where(
                        Payment.order_id == order_id,
                        Payment.method == "credit",
                        Payment.status == "refunded",
                    )
                )
            )
            .scalars()
            .all()
        )
        assert [float(p.amount) for p in reverse_rows] == [-10.0]
        order = await session.get(SaleOrder, order_id)
        assert order.status == "refunded"
        assert order.refunded_amount == Decimal("10.00")


@pytest.mark.asyncio
async def test_credit_with_partial_repay_full_refund_returns_cash_first(client, db_session):
    """赊账单部分回款后整单退款：真金优先退客户，余额再冲应收，余额恰归零.

    订单 10 元纯赊账，客户回款 4 元微信 → 整单退款时应退微信 4 元现金、
    冲应收 6 元；不得"现金不退、应收多冲成负数"。
    """
    await _seed_stock(db_session, quantity=10)
    create = await client.post(
        "/api/v1/pos/orders",
        json={
            "client_id": "moneyflow-credit-repay-refund-001",
            "payment_method": "credit",
            "customer_name": "吴记快餐",
            "items": _items(2, 5.0),
        },
    )
    order_id = create.json()["data"]["order_id"]
    repay = await client.post(
        f"/api/v1/pos/orders/{order_id}/pay",
        json={"amount": 4, "method": "wechat", "transaction_id": "wx-moneyflow-repay-001"},
    )
    assert repay.status_code == 200
    assert repay.json()["data"]["status"] == "partial"

    refund = await client.post(
        f"/api/v1/pos/orders/{order_id}/refund",
        json={"reason": "整单退货", "return_to_stock": True},
    )
    assert refund.status_code == 200
    assert refund.json()["data"]["refunded_amount"] == 10.0

    async with db_session() as session:
        # 客户欠款归零：charge 10 - 回款 4 - 退款冲减 6
        assert await get_customer_balance(session, uuid.UUID(TEST_MERCHANT_ID), "吴记快餐") == (
            Decimal("0")
        )
        reverse = (
            (
                await session.execute(
                    select(Payment).where(
                        Payment.order_id == uuid.UUID(order_id),
                        Payment.status == "refunded",
                    )
                )
            )
            .scalars()
            .all()
        )
        by_method = {p.method: float(p.amount) for p in reverse}
        assert by_method == {"wechat": -4.0, "credit": -6.0}


# ═══════════════════════════════════════════════════════════════════
# Fix 2: 折扣订单退款按实付比例摊分
# ═══════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_discounted_order_full_refund_returns_net_paid(client, db_session):
    """折扣单（毛 100 折 20 实付 80）整单退 → refunded_amount==80、remaining==0."""
    await _seed_stock(db_session, quantity=10)
    create = await client.post(
        "/api/v1/pos/orders",
        json={
            "client_id": "moneyflow-discount-full-001",
            "payment_method": "cash",
            "discount_amount": 20,
            "items": _items(10, 10.0),
        },
    )
    assert create.status_code == 200
    data = create.json()["data"]
    assert data["total_amount"] == 80.0
    order_id = uuid.UUID(data["order_id"])

    refund = await client.post(
        f"/api/v1/pos/orders/{order_id}/refund",
        json={"reason": "整单退货", "return_to_stock": True},
    )
    assert refund.status_code == 200
    rdata = refund.json()["data"]
    assert rdata["new_status"] == "refunded"
    assert rdata["refunded_amount"] == 80.0
    assert rdata["remaining_amount"] == 0.0

    async with db_session() as session:
        order = await session.get(SaleOrder, order_id)
        assert order.refunded_amount == Decimal("80.00")
        reverse = (
            (
                await session.execute(
                    select(Payment).where(
                        Payment.order_id == order_id, Payment.status == "refunded"
                    )
                )
            )
            .scalars()
            .all()
        )
        assert sum((p.amount for p in reverse), Decimal("0")) == Decimal("-80.00")


@pytest.mark.asyncio
async def test_discounted_order_partial_refund_prorates(client, db_session):
    """折扣单部分退款：退一半数量 → 按实付比例退 40，remaining==40."""
    await _seed_stock(db_session, quantity=10)
    create = await client.post(
        "/api/v1/pos/orders",
        json={
            "client_id": "moneyflow-discount-partial-001",
            "payment_method": "cash",
            "discount_amount": 20,
            "items": _items(10, 10.0),
        },
    )
    order_id = uuid.UUID(create.json()["data"]["order_id"])
    detail = await client.get(f"/api/v1/pos/orders/{order_id}")
    item_id = detail.json()["data"]["items"][0]["item_id"]

    refund = await client.post(
        f"/api/v1/pos/orders/{order_id}/refund",
        json={
            "reason": "退一半",
            "items": [{"item_id": item_id, "quantity": 5, "return_to_stock": True}],
        },
    )
    assert refund.status_code == 200
    rdata = refund.json()["data"]
    # 毛 50 × (80/100) = 实退 40，不得按毛额退 50
    assert rdata["refunded_amount"] == 40.0
    assert rdata["remaining_amount"] == 40.0
    assert rdata["new_status"] == "partial_refund"

    async with db_session() as session:
        reverse = (
            (
                await session.execute(
                    select(Payment).where(
                        Payment.order_id == order_id, Payment.status == "refunded"
                    )
                )
            )
            .scalars()
            .all()
        )
        assert sum((p.amount for p in reverse), Decimal("0")) == Decimal("-40.00")


# ═══════════════════════════════════════════════════════════════════
# Fix 3: 组合支付含 credit 的订单日结口径
# ═══════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_combo_cash_credit_settlement_balances(client, db_session):
    """组合支付 [现金50+赊账50] 日结 → credit_amount==50、diff_amount==0."""
    await _seed_stock(db_session, quantity=10)
    create = await client.post(
        "/api/v1/pos/orders",
        json={
            "client_id": "moneyflow-combo-settle-001",
            "customer_name": "孙记熟食",
            "items": _items(10, 10.0),
            "payments": [
                {"method": "cash", "amount": 50.0},
                {"method": "credit", "amount": 50.0},
            ],
        },
    )
    assert create.status_code == 200
    assert create.json()["data"]["status"] == "paid"

    settle = await client.post(
        f"/api/v1/pos/daily-settlement/{local_now().date().isoformat()}/close"
    )
    assert settle.status_code == 200
    data = settle.json()["data"]
    assert data["total_sales"] == 100.0
    assert data["cash_amount"] == 50.0
    assert data["total_payments"] == 50.0
    assert data["credit_amount"] == 50.0
    assert data["diff_amount"] == 0.0


@pytest.mark.asyncio
async def test_pure_credit_settlement_balances(client, db_session):
    """纯赊账订单（status="credit"）日结原口径保持成立：credit==total、diff==0."""
    await _seed_stock(db_session, quantity=10)
    create = await client.post(
        "/api/v1/pos/orders",
        json={
            "client_id": "moneyflow-credit-settle-001",
            "payment_method": "credit",
            "customer_name": "郑记面馆",
            "items": _items(2, 5.0),
        },
    )
    assert create.status_code == 200

    settle = await client.post(
        f"/api/v1/pos/daily-settlement/{local_now().date().isoformat()}/close"
    )
    assert settle.status_code == 200
    data = settle.json()["data"]
    assert data["total_sales"] == 10.0
    assert data["total_payments"] == 0.0
    assert data["credit_amount"] == 10.0
    assert data["diff_amount"] == 0.0


@pytest.mark.asyncio
async def test_credit_partial_repay_settlement_balances(client, db_session):
    """赊账 10 当日回款 4 → payments==4、credit_amount==6、diff==0（回款不双算）."""
    await _seed_stock(db_session, quantity=10)
    create = await client.post(
        "/api/v1/pos/orders",
        json={
            "client_id": "moneyflow-credit-repay-settle-001",
            "payment_method": "credit",
            "customer_name": "王记卤味",
            "items": _items(2, 5.0),
        },
    )
    order_id = create.json()["data"]["order_id"]
    repay = await client.post(
        f"/api/v1/pos/orders/{order_id}/pay",
        json={"amount": 4, "method": "wechat", "transaction_id": "wx-moneyflow-settle-001"},
    )
    assert repay.status_code == 200
    assert repay.json()["data"]["status"] == "partial"

    settle = await client.post(
        f"/api/v1/pos/daily-settlement/{local_now().date().isoformat()}/close"
    )
    assert settle.status_code == 200
    data = settle.json()["data"]
    assert data["total_sales"] == 10.0
    assert data["wechat_amount"] == 4.0
    assert data["total_payments"] == 4.0
    assert data["credit_amount"] == 6.0
    assert data["diff_amount"] == 0.0


# ═══════════════════════════════════════════════════════════════════
# Fix 4: 支付后自动对账落库
# ═══════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_auto_reconcile_persists_task_and_differences(client, db_session):
    """导入渠道账单后支付 → 自动对账的 task 与差异明细持久化在 DB.

    修复前 helper 只 flush 不 commit，get_db 关闭 session 时全部隐式回滚。
    """
    recon_date = local_now().date().isoformat()
    content = (
        "transaction_id,merchant_order_no,amount,fee,status,record_type\n"
        "wx-moneyflow-orphan-001,MONEYFLOW-ORPHAN,3.00,0.02,SUCCESS,payment\n"
    )
    imported = await client.post(
        f"/api/v1/reconciliation/import/{recon_date}?channel=wechat",
        files={"file": ("bill.csv", content.encode("utf-8-sig"), "text/csv")},
    )
    assert imported.status_code == 200

    await _seed_stock(db_session, quantity=10)
    create = await client.post(
        "/api/v1/pos/orders",
        json={
            "client_id": "moneyflow-auto-recon-001",
            "payment_method": "wechat",
            "items": _items(2, 3.5),
        },
    )
    assert create.status_code == 200

    async with db_session() as session:
        task = (
            await session.execute(
                select(ReconciliationTask).where(
                    ReconciliationTask.merchant_id == uuid.UUID(TEST_MERCHANT_ID),
                    ReconciliationTask.channel == "wechat",
                    ReconciliationTask.date == local_now().date(),
                )
            )
        ).scalar_one()
        # 自动对账已按当日微信流水重跑并落库
        assert task.system_total == Decimal("7.00")
        assert task.channel_total == Decimal("3.00")
        assert task.diff_amount == Decimal("4.00")
        assert task.status == "exception"
        differences = (
            (
                await session.execute(
                    select(ReconciliationDifference).where(
                        ReconciliationDifference.task_id == task.id
                    )
                )
            )
            .scalars()
            .all()
        )
        assert {d.diff_type for d in differences} == {"channel_only", "system_only"}


@pytest.mark.asyncio
async def test_auto_reconcile_persists_task_without_bills(client, db_session):
    """无渠道账单时支付 → 仍应持久化当日的渠道对账任务（此前恒被隐式回滚）."""
    await _seed_stock(db_session, quantity=10)
    create = await client.post(
        "/api/v1/pos/orders",
        json={
            "client_id": "moneyflow-auto-recon-task-001",
            "payment_method": "cash",
            "items": _items(1, 3.0),
        },
    )
    assert create.status_code == 200

    async with db_session() as session:
        task = (
            await session.execute(
                select(ReconciliationTask).where(
                    ReconciliationTask.merchant_id == uuid.UUID(TEST_MERCHANT_ID),
                    ReconciliationTask.channel == "cash",
                    ReconciliationTask.date == local_now().date(),
                )
            )
        ).scalar_one_or_none()
        assert task is not None
        # 未导渠道账单 → 任务保持待对账状态（不触发逐笔匹配）
        assert task.status == "pending"


@pytest.mark.asyncio
async def test_auto_reconcile_skips_credit_channel(client, db_session):
    """赊账支付不产生 credit 渠道对账任务（credit 无外部渠道账单）."""
    await _seed_stock(db_session, quantity=10)
    create = await client.post(
        "/api/v1/pos/orders",
        json={
            "client_id": "moneyflow-auto-recon-credit-001",
            "payment_method": "credit",
            "customer_name": "冯记小炒",
            "items": _items(1, 3.0),
        },
    )
    assert create.status_code == 200

    async with db_session() as session:
        credit_tasks = (
            (
                await session.execute(
                    select(ReconciliationTask).where(
                        ReconciliationTask.channel == "credit",
                        ReconciliationTask.merchant_id == uuid.UUID(TEST_MERCHANT_ID),
                    )
                )
            )
            .scalars()
            .all()
        )
        assert credit_tasks == []


# ═══════════════════════════════════════════════════════════════════
# 集成补修：组合收款对赊账订单记应收回款；收款拒绝 credit 方式
# ═══════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_combo_pay_on_credit_order_records_receivable_repay(client, db_session):
    """赊账单用组合方式回款 → 应收按真金合计冲减，与单笔路径口径一致."""
    await _seed_stock(db_session, quantity=10)
    create = await client.post(
        "/api/v1/pos/orders",
        json={
            "client_id": "moneyflow-combo-repay-001",
            "payment_method": "credit",
            "customer_name": "郑记卤味",
            "items": _items(2, 5.0),
        },
    )
    order_id = create.json()["data"]["order_id"]

    async with db_session() as session:
        assert await get_customer_balance(session, uuid.UUID(TEST_MERCHANT_ID), "郑记卤味") == (
            Decimal("10")
        )

    repay = await client.post(
        f"/api/v1/pos/orders/{order_id}/pay",
        json={
            "amount": 5,
            "payments": [
                {"amount": 3, "method": "cash"},
                {"amount": 2, "method": "wechat"},
            ],
        },
    )
    assert repay.status_code == 200
    assert repay.json()["data"]["status"] == "partial"

    async with db_session() as session:
        # charge 10 - 组合真金回款 5（cash 3 + wechat 2）
        assert await get_customer_balance(session, uuid.UUID(TEST_MERCHANT_ID), "郑记卤味") == (
            Decimal("5")
        )
        repay_rows = (
            (
                await session.execute(
                    select(CustomerReceivable).where(
                        CustomerReceivable.sale_order_id == uuid.UUID(order_id),
                        CustomerReceivable.direction == "repay",
                    )
                )
            )
            .scalars()
            .all()
        )
        assert sum(r.amount for r in repay_rows) == Decimal("5")


@pytest.mark.asyncio
async def test_pay_rejects_credit_method(client, db_session):
    """收款端点拒绝 credit 方式：用赊账还赊账没有资金语义."""
    await _seed_stock(db_session, quantity=10)
    create = await client.post(
        "/api/v1/pos/orders",
        json={
            "client_id": "moneyflow-pay-credit-reject-001",
            "payment_method": "credit",
            "customer_name": "冯记早点",
            "items": _items(1, 5.0),
        },
    )
    order_id = create.json()["data"]["order_id"]
    repay = await client.post(
        f"/api/v1/pos/orders/{order_id}/pay",
        json={"amount": 5, "payments": [{"amount": 5, "method": "credit"}]},
    )
    assert repay.status_code == 400
