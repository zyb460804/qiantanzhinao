"""POS 资金链路回归测试 — 赊账退款 / 折扣摊分 / 组合支付日结 / 自动对账落库.

覆盖本轮修复的四个资金口径问题：
- Fix 1: 纯赊账订单退款必须冲销应收（credit Payment 行打通退款链路）。
- Fix 2: 折扣订单退款按实付比例摊分，refunded_amount 不超实收。
- Fix 3: 组合支付含 credit 的订单日结 diff_amount == 0。
- Fix 4: 支付后自动对账结果持久化到 DB。
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select, update
from tests.conftest import TEST_MERCHANT_ID

from app.core.timezone import CST, cst_today, local_now
from app.models.accounts import CustomerReceivable
from app.models.inventory import InventoryRecord
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


# ═══════════════════════════════════════════════════════════════════
# 第五轮 L3：四流恒等式跨日场景（V1-H2）+ net_cash_flow 去双算（V1-H3）
# 四流恒等式：total_sales = payments + credit_amount + refund_amount
# ═══════════════════════════════════════════════════════════════════


def _cst_day_utc_stamp(day: date) -> datetime:
    """CST 业务日 day 正午 12:00 对应的 naive UTC 时间（远离日界，避免边界误差）."""
    return datetime.combine(day, time(12, 0), tzinfo=CST).astimezone(UTC).replace(tzinfo=None)


async def _shift_order_to_cst_day(db_session, order_id: uuid.UUID, day: date) -> None:
    """把订单及其全部支付流水整体挪到指定 CST 业务日（模拟跨日资金场景）."""
    stamp = _cst_day_utc_stamp(day)
    async with db_session() as session:
        order = await session.get(SaleOrder, order_id)
        assert order is not None
        order.created_at = stamp
        await session.execute(
            update(Payment).where(Payment.order_id == order_id).values(created_at=stamp)
        )
        await session.commit()


async def _live_settlement(client, day: date) -> dict:
    """读取某 CST 日的实时日结数字（未关闭时 GET 返回 live 计算）."""
    res = await client.get(f"/api/v1/pos/daily-settlement/{day.isoformat()}")
    assert res.status_code == 200
    return res.json()["data"]


@pytest.mark.asyncio
async def test_same_day_refund_settlement_balances(client, db_session):
    """场景① 当日退：现金单 100 当日整单退 → refund==100、payments==0、diff==0."""
    await _seed_stock(db_session, quantity=10)
    create = await client.post(
        "/api/v1/pos/orders",
        json={
            "client_id": "l3-sameday-refund-001",
            "payment_method": "cash",
            "items": _items(10, 10.0),
        },
    )
    assert create.status_code == 200
    order_id = uuid.UUID(create.json()["data"]["order_id"])
    refund = await client.post(
        f"/api/v1/pos/orders/{order_id}/refund",
        json={"reason": "当日退货", "return_to_stock": True},
    )
    assert refund.status_code == 200

    numbers = await _live_settlement(client, cst_today())
    assert numbers["total_sales"] == 100.0
    assert numbers["refund_amount"] == 100.0
    assert numbers["total_payments"] == 0.0  # +100 收款与 -100 退款同日对冲
    assert numbers["diff_amount"] == 0.0


@pytest.mark.asyncio
async def test_next_day_refund_counts_on_refund_day_and_keeps_both_days_balanced(
    client, db_session
):
    """场景② 次日退当日订单：退款计入退款日（refund==100、cash==-100），两日 diff==0.

    V1-H2 回归：原 SUM(SaleOrder.refunded_amount) 按订单创建日归集，跨日退款
    时订单日重算 diff=-100、退款日又什么都不显示。
    """
    await _seed_stock(db_session, quantity=10)
    create = await client.post(
        "/api/v1/pos/orders",
        json={
            "client_id": "l3-nextday-refund-001",
            "payment_method": "cash",
            "items": _items(10, 10.0),
        },
    )
    assert create.status_code == 200
    order_id = uuid.UUID(create.json()["data"]["order_id"])
    yesterday = cst_today() - timedelta(days=1)
    await _shift_order_to_cst_day(db_session, order_id, yesterday)

    refund = await client.post(
        f"/api/v1/pos/orders/{order_id}/refund",
        json={"reason": "次日退货", "return_to_stock": True},
    )
    assert refund.status_code == 200

    # 退款日（今天）：无销售，但退款流水落今天并计入渠道额
    today_numbers = await _live_settlement(client, cst_today())
    assert today_numbers["total_sales"] == 0.0
    assert today_numbers["refund_amount"] == 100.0
    assert today_numbers["cash_amount"] == -100.0
    assert today_numbers["total_payments"] == -100.0
    assert today_numbers["diff_amount"] == 0.0

    # 订单日（昨天）：销售/收款照旧，退款不回灌订单日
    yesterday_numbers = await _live_settlement(client, yesterday)
    assert yesterday_numbers["total_sales"] == 100.0
    assert yesterday_numbers["total_payments"] == 100.0
    assert yesterday_numbers["refund_amount"] == 0.0
    assert yesterday_numbers["diff_amount"] == 0.0


@pytest.mark.asyncio
async def test_same_day_credit_same_day_collect_net_cash_flow(client, db_session):
    """场景③ 当日赊 10 当日收 4：net_cash_flow==4（payments 已含回款，不再双算）.

    V1-H3 回归：原 customer_repay 聚合不排除当日订单回款，净流算成 8。
    """
    await _seed_stock(db_session, quantity=10)
    create = await client.post(
        "/api/v1/pos/orders",
        json={
            "client_id": "l3-sameday-credit-001",
            "payment_method": "credit",
            "customer_name": "l3当日回款",
            "items": _items(2, 5.0),
        },
    )
    assert create.status_code == 200
    order_id = uuid.UUID(create.json()["data"]["order_id"])
    repay = await client.post(
        f"/api/v1/pos/orders/{order_id}/pay",
        json={"amount": 4, "method": "cash", "transaction_id": "l3-sameday-repay-001"},
    )
    assert repay.status_code == 200
    assert repay.json()["data"]["status"] == "partial"

    numbers = await _live_settlement(client, cst_today())
    assert numbers["total_sales"] == 10.0
    assert numbers["total_payments"] == 4.0
    assert numbers["customer_repay"] == 0.0  # 当日订单回款已计入 payments
    assert numbers["net_cash_flow"] == 4.0
    assert numbers["credit_amount"] == 6.0
    assert numbers["diff_amount"] == 0.0


@pytest.mark.asyncio
async def test_cross_day_repay_counts_once_in_net_cash_flow(client, db_session):
    """场景④ 跨日回款：昨赊 10 今收 4 → 今日 net_cash_flow==4、customer_repay==4、diff==0."""
    await _seed_stock(db_session, quantity=10)
    create = await client.post(
        "/api/v1/pos/orders",
        json={
            "client_id": "l3-crossday-credit-001",
            "payment_method": "credit",
            "customer_name": "l3跨日回款",
            "items": _items(2, 5.0),
        },
    )
    assert create.status_code == 200
    order_id = uuid.UUID(create.json()["data"]["order_id"])
    yesterday = cst_today() - timedelta(days=1)
    await _shift_order_to_cst_day(db_session, order_id, yesterday)

    repay = await client.post(
        f"/api/v1/pos/orders/{order_id}/pay",
        json={"amount": 4, "method": "wechat", "transaction_id": "l3-crossday-repay-001"},
    )
    assert repay.status_code == 200

    # 回款日（今天）：跨日回款不进 payments（否则与 customer_repay 双算）
    today_numbers = await _live_settlement(client, cst_today())
    assert today_numbers["total_sales"] == 0.0
    assert today_numbers["total_payments"] == 0.0
    assert today_numbers["customer_repay"] == 4.0
    assert today_numbers["net_cash_flow"] == 4.0
    assert today_numbers["diff_amount"] == 0.0

    # 赊账日（昨天）：credit_amount==10、无回款冲减
    yesterday_numbers = await _live_settlement(client, yesterday)
    assert yesterday_numbers["total_sales"] == 10.0
    assert yesterday_numbers["credit_amount"] == 10.0
    assert yesterday_numbers["total_payments"] == 0.0
    assert yesterday_numbers["diff_amount"] == 0.0


# ═══════════════════════════════════════════════════════════════════
# 第五轮 L3：幂等键列宽（V5-C1）+ 组合双赊账 + 折扣残差明细对齐
# ═══════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_pos_idempotency_keys_fit_varchar64(client, db_session):
    """V5-C1：sale/refund 幂等键压缩后 ≤64 且保留前缀语义（原 78/82+ 字符 PG 500）."""
    await _seed_stock(db_session, quantity=10)
    create = await client.post(
        "/api/v1/pos/orders",
        json={
            "client_id": "l3-idem-key-001",
            "payment_method": "cash",
            "items": [
                {"product_id": 1, "quantity": 1, "unit": "斤", "unit_price": 5.0},
                {"product_id": 1, "quantity": 1, "unit": "斤", "unit_price": 5.0},
            ],
        },
    )
    assert create.status_code == 200
    order_id = uuid.UUID(create.json()["data"]["order_id"])
    refund = await client.post(
        f"/api/v1/pos/orders/{order_id}/refund",
        json={"reason": "幂等键回归", "return_to_stock": True},
    )
    assert refund.status_code == 200

    async with db_session() as session:
        keys = (
            (
                await session.execute(
                    select(InventoryRecord.idempotency_key).where(
                        InventoryRecord.merchant_id == uuid.UUID(TEST_MERCHANT_ID),
                        InventoryRecord.event_type.in_(("sale", "refund")),
                    )
                )
            )
            .scalars()
            .all()
        )
    assert len(keys) >= 3
    for key in keys:
        assert key is not None
        assert len(key) <= 64, f"idempotency key exceeds VARCHAR(64): {key!r} ({len(key)})"
        assert key.startswith(("sale:", "refund:"))
    assert any(key.startswith("sale:") for key in keys)
    assert any(key.startswith("refund:") for key in keys)
    assert len(set(keys)) == len(keys)  # 每行键互不相同


@pytest.mark.asyncio
async def test_refund_receivable_key_fits_varchar64_on_large_refund(client, db_session):
    """V5-C1：赊账大额退款的 sale-refund 键 ≤64（原键 30 万退款额时 65 字符）."""
    await _seed_stock(db_session, quantity=10)
    create = await client.post(
        "/api/v1/pos/orders",
        json={
            "client_id": "l3-idem-refund-001",
            "payment_method": "credit",
            "customer_name": "l3大额赊退",
            "items": _items(6, 50000.0),
        },
    )
    assert create.status_code == 200
    order_id = uuid.UUID(create.json()["data"]["order_id"])
    refund = await client.post(
        f"/api/v1/pos/orders/{order_id}/refund",
        json={"reason": "大额整退", "return_to_stock": True},
    )
    assert refund.status_code == 200

    async with db_session() as session:
        rows = (
            (
                await session.execute(
                    select(CustomerReceivable).where(
                        CustomerReceivable.sale_order_id == order_id,
                        CustomerReceivable.direction == "repay",
                    )
                )
            )
            .scalars()
            .all()
        )
    assert len(rows) == 1
    key = rows[0].idempotency_key
    assert key is not None
    assert len(key) <= 64, f"sale-refund key exceeds VARCHAR(64): {key!r} ({len(key)})"
    assert key.startswith("sale-refund:")


@pytest.mark.asyncio
async def test_combo_double_credit_entries_merge_into_one_receivable(client, db_session):
    """LOW(b)：组合支付含两条 credit 不再撞幂等键 → 一笔合并应收，无 500."""
    await _seed_stock(db_session, quantity=10)
    res = await client.post(
        "/api/v1/pos/orders",
        json={
            "client_id": "l3-combo-double-credit-001",
            "customer_name": "l3双赊",
            "items": _items(10, 10.0),
            "payments": [
                {"method": "cash", "amount": 50.0},
                {"method": "credit", "amount": 30.0},
                {"method": "credit", "amount": 20.0},
            ],
        },
    )
    assert res.status_code == 200
    order_id = uuid.UUID(res.json()["data"]["order_id"])

    async with db_session() as session:
        charges = (
            (
                await session.execute(
                    select(CustomerReceivable).where(
                        CustomerReceivable.sale_order_id == order_id,
                        CustomerReceivable.direction == "charge",
                    )
                )
            )
            .scalars()
            .all()
        )
        assert len(charges) == 1  # 合并为一笔，不撞唯一约束
        assert charges[0].amount == Decimal("50")
        key = charges[0].idempotency_key
        assert key is not None and len(key) <= 64 and key.startswith("sale-credit:")

        credit_payments = (
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
        # Payment 流水仍逐笔保留（两笔 credit 各 30/20）
        assert sum((p.amount for p in credit_payments), Decimal("0")) == Decimal("50")


@pytest.mark.asyncio
async def test_discount_residual_aligns_inventory_records(client, db_session):
    """LOW(c)：整单退款 ±0.01 残差对齐时 InventoryRecord.total_amount 同步对齐."""
    await _seed_stock(db_session, quantity=5)
    create = await client.post(
        "/api/v1/pos/orders",
        json={
            "client_id": "l3-residual-align-001",
            "payment_method": "cash",
            "discount_amount": 1,
            "items": [
                {"product_id": 1, "quantity": 1, "unit": "斤", "unit_price": 10.0},
                {"product_id": 1, "quantity": 1, "unit": "斤", "unit_price": 10.0},
                {"product_id": 1, "quantity": 1, "unit": "斤", "unit_price": 10.0},
            ],
        },
    )
    assert create.status_code == 200
    assert create.json()["data"]["total_amount"] == 29.0
    order_id = uuid.UUID(create.json()["data"]["order_id"])

    refund = await client.post(
        f"/api/v1/pos/orders/{order_id}/refund",
        json={"reason": "残差对齐", "return_to_stock": True},
    )
    assert refund.status_code == 200
    assert refund.json()["data"]["refunded_amount"] == 29.0

    async with db_session() as session:
        order = await session.get(SaleOrder, order_id)
        refund_records = (
            (
                await session.execute(
                    select(InventoryRecord).where(
                        InventoryRecord.client_reference == order.order_no,
                        InventoryRecord.event_type == "refund",
                    )
                )
            )
            .scalars()
            .all()
        )
        assert len(refund_records) == 3
        amounts = [r.total_amount for r in refund_records]
        # 逐行 9.67×3 = 29.01 → 残差 -0.01 落到末行 9.66，明细合计对齐 29.00
        assert sum(amounts, Decimal("0")) == Decimal("29.00")
        assert max(amounts) - min(amounts) == Decimal("0.01")
