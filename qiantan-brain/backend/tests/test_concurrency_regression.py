"""逻辑层并发回归测试（测试审计 C1 可落地部分）.

诚实边界（先读这里，再读断言）：
- 测试库是单连接 SQLite（conftest：aiosqlite :memory: + StaticPool），全部请求
  session 共享同一条物理连接。SELECT ... FOR UPDATE 被 SQLite 静默忽略，所以本
  文件验证的不是 PG 行锁本身，而是并发重复请求下「应用层守卫 + 幂等唯一约束」
  能否保证账本恰好落一套数据。
- 实验结论：在本 harness 里让多个写请求事务真正同时在途（gather 同时发出且彼此
  事务重叠）时，共享连接上的交叉回滚会互相污染，出现 200 却落 0 行、无幂等键的
  表落 3 行等非确定性结果。这是单连接 harness 的承载极限，不是业务代码缺陷。
  因此每个场景采用「首笔写请求已提交 + gather 并发放出一批重复请求」的形态，
  这正是 PG 行锁下第二个事务在锁上等待、首事务提交后重读状态所走到的路径。
- 真正的事务重叠与 PG 行锁/MVCC 行为需要 testcontainers 起真 PG16 重放本文件
  场景，属后续项，本文件不覆盖。
- quota 并发累加已由 tests/test_quota_usage.py 的
  test_record_usage_concurrent_calls_do_not_lose_updates 覆盖，此处不重复。
"""

import asyncio
import uuid
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from sqlalchemy import func, select
from tests.conftest import TEST_MERCHANT_ID

from app.models.accounts import SupplierPayable
from app.models.batch import BatchLifecycle
from app.models.feedback import MerchantFeedback
from app.models.idempotency import IdempotencyRecord
from app.models.inventory import InventoryRecord
from app.models.pos import Payment, SaleOrder
from app.models.purchase import PurchaseList
from app.models.recommendation import Recommendation
from app.models.staff import StaffMember
from app.models.stocktake import StocktakeSession
from app.services.batch import create_batch


MID = uuid.UUID(TEST_MERCHANT_ID)

pytestmark = pytest.mark.asyncio


async def _burst(client, url, json=None, headers=None, n=3):
    """gather 并发放出 n 个完全相同的写请求，返回响应列表（不写时序假设）。"""
    tasks = [client.post(url, json=json, headers=headers) for _ in range(n)]
    return await asyncio.gather(*tasks)


def _assert_no_5xx(resps):
    for r in resps:
        assert r.status_code < 500, f"unexpected 5xx: {r.status_code} {r.text[:200]}"


async def _scalars(db_session, stmt):
    async with db_session() as s:
        return (await s.execute(stmt)).scalars().all()


async def _seed_stock(db_session, quantity=20):
    async with db_session() as s:
        await create_batch(
            s,
            MID,
            1,
            "白菜",
            "白菜-conc-" + uuid.uuid4().hex[:6],
            Decimal(str(quantity)),
        )
        await s.commit()


async def _mk_purchase_list(db_session, client):
    async with db_session() as s:
        s.add(
            Recommendation(
                merchant_id=MID,
                product_id=1,
                suggestion="建议采购",
                basis=[],
                recommended_qty=10,
                confidence=0.8,
            )
        )
        await s.commit()
    gen = await client.post("/api/v1/purchase/from-advice", json={})
    assert gen.status_code == 200, gen.text
    return gen.json()["data"]["list_id"]


async def test_voice_confirm_duplicate_burst_lands_single_inventory_record(client, db_session):
    """并发重复 confirm 同一 VoiceLog → 恰 1 条库存流水，其余幂等返回，无 5xx.

    PG 区分度：首笔提交后，锚点行锁让后续请求串行重读 status=confirmed 走幂等
    短路；SQLite 忽略行锁，但「首笔已提交」同样让守卫命中。真正同时在途的双
    confirm 在 SQLite 下由 uq_inventory_idempotency_per_merchant 唯一约束兜底
    （见 test_idempotency_unique_constraints_backstop）。
    """
    parse = await client.post(
        "/api/v1/voice/parse-text",
        json={"merchant_id": TEST_MERCHANT_ID, "text": "进了白菜50斤，三毛钱一斤"},
    )
    assert parse.status_code == 200, parse.text
    voice_log_id = parse.json()["data"]["parsed"]["voice_log_id"]

    first = await client.post("/api/v1/voice/confirm", json={"voice_log_id": voice_log_id})
    assert first.status_code == 200, first.text

    resps = await _burst(client, "/api/v1/voice/confirm", json={"voice_log_id": voice_log_id}, n=5)
    _assert_no_5xx(resps)
    for r in resps:
        assert r.status_code == 200
        assert r.json()["data"]["idempotent"] is True

    records = await _scalars(
        db_session,
        select(InventoryRecord).where(InventoryRecord.voice_log_id == uuid.UUID(voice_log_id)),
    )
    assert len(records) == 1
    assert records[0].idempotency_key == "voice:" + voice_log_id
    assert records[0].quantity == Decimal("50")


async def test_stocktake_complete_duplicate_burst_single_adjustment(client, db_session):
    """并发重复 complete 同一盘点会话 → 恰 1 条调整流水，会话单次完成，无 5xx.

    PG 区分度：complete 的锚点 FOR UPDATE 让并发请求串行化，后到者读到
    status=completed 走空调整短路。SQLite 下盘点调整流水没有幂等键，真正同时
    在途的双 complete 无法在本 harness 安全复现（见模块 docstring），属
    testcontainers 真 PG 的后续项。
    """
    # P2-2：盘点只为有账面流水的品项生成待盘项 —— 先给商品1入账
    async with db_session() as session:
        session.add(
            InventoryRecord(
                merchant_id=MID,
                product_id=1,
                quantity=Decimal("10"),
                unit="斤",
                total_amount=Decimal("20"),
                event_type="purchase",
                event_time=datetime.now(),
            )
        )
        await session.commit()
    start = (
        await client.post(
            "/api/v1/inventory/stocktake/start", json={"merchant_id": TEST_MERCHANT_ID}
        )
    ).json()["data"]
    sid = start["session_id"]
    for item in start["items"]:
        qty = 5 if item["product_id"] == 1 else item["book_qty"]
        submit = await client.post(
            f"/api/v1/inventory/stocktake/{sid}/submit",
            json={"product_id": item["product_id"], "actual_qty": qty},
        )
        assert submit.status_code == 200, submit.text

    first = await client.post(f"/api/v1/inventory/stocktake/{sid}/complete", json={})
    assert first.status_code == 200, first.text
    assert len(first.json()["data"]["adjustments"]) == 1

    resps = await _burst(client, f"/api/v1/inventory/stocktake/{sid}/complete", json={}, n=3)
    _assert_no_5xx(resps)
    for r in resps:
        assert r.status_code == 200
        assert r.json()["data"]["adjustments"] == []

    records = await _scalars(
        db_session,
        select(InventoryRecord).where(
            InventoryRecord.source == "stocktake",
            # QA-20：盘亏改记 waste 口径（原 'adjustment'）
            InventoryRecord.event_type == "waste",
        ),
    )
    assert len(records) == 1
    sessions = await _scalars(
        db_session, select(StocktakeSession).where(StocktakeSession.id == uuid.UUID(sid))
    )
    assert sessions[0].status == "completed"
    # 账面 10（前置入账），实盘 5 → 差异 -5
    assert float(sessions[0].total_variance) == -5.0


async def test_purchase_legacy_confirm_duplicate_burst_single_entry(client, db_session):
    """并发重复 legacy /confirm → 恰 1 套库存流水/批次，清单 stored，重复请求 409，无 5xx.

    PG 区分度：/confirm 入口 FOR UPDATE + stored 状态检查让后到者 409；SQLite 下
    真正在途的双 confirm 由库存流水幂等键唯一约束兜底。
    """
    list_id = await _mk_purchase_list(db_session, client)

    first = await client.post(f"/api/v1/purchase/{list_id}/confirm", json={})
    assert first.status_code == 200, first.text
    assert first.json()["data"]["confirmed_count"] == 1

    resps = await _burst(client, f"/api/v1/purchase/{list_id}/confirm", json={}, n=3)
    _assert_no_5xx(resps)
    for r in resps:
        assert r.status_code == 409

    records = await _scalars(
        db_session, select(InventoryRecord).where(InventoryRecord.source == "purchase_list")
    )
    assert len(records) == 1
    assert records[0].idempotency_key
    batches = await _scalars(
        db_session, select(BatchLifecycle).where(BatchLifecycle.merchant_id == MID)
    )
    assert len(batches) == 1
    plists = await _scalars(
        db_session, select(PurchaseList).where(PurchaseList.id == uuid.UUID(list_id))
    )
    assert plists[0].status == "stored"
    # from-advice 清单默认无供应商 → record_supplier_payable_from_purchase 短路不落
    # 应付；应付不重复由 uq_supplier_payable_idempotency_per_merchant 约束兜底
    # （见 test_idempotency_unique_constraints_backstop）。


async def test_purchase_acceptance_confirm_duplicate_burst_single_entry(client, db_session):
    """并发重复 /acceptance/confirm（新路径）→ 恰 1 套库存流水/批次，重复 409，无 5xx.

    PG 区分度：与 legacy /confirm 相同的锚点 FOR UPDATE + accepted→stored 状态机；
    SQLite 下真正在途的双 confirm 同样靠库存流水幂等键兜底。
    """
    list_id = await _mk_purchase_list(db_session, client)
    today = await client.get("/api/v1/purchase/today", params={"list_id": list_id})
    item_id = today.json()["data"]["items"][0]["item_id"]
    acc = await client.post(
        f"/api/v1/purchase/{list_id}/acceptance",
        json={
            "items": [
                {
                    "item_id": item_id,
                    "arrival_qty": 10,
                    "accepted_qty": 10,
                    "shortage_qty": 0,
                    "damaged_qty": 0,
                    "rejected_qty": 0,
                    "returned_qty": 0,
                    "replenish_qty": 0,
                    "actual_unit_cost": 2.0,
                    "quality_ok": True,
                }
            ]
        },
    )
    assert acc.status_code == 200, acc.text

    first = await client.post(f"/api/v1/purchase/{list_id}/acceptance/confirm")
    assert first.status_code == 200, first.text
    assert first.json()["data"]["confirmed_count"] == 1

    resps = await _burst(client, f"/api/v1/purchase/{list_id}/acceptance/confirm", n=3)
    _assert_no_5xx(resps)
    for r in resps:
        assert r.status_code == 409

    records = await _scalars(
        db_session, select(InventoryRecord).where(InventoryRecord.source == "purchase_list")
    )
    assert len(records) == 1
    batches = await _scalars(
        db_session, select(BatchLifecycle).where(BatchLifecycle.merchant_id == MID)
    )
    assert len(batches) == 1
    plists = await _scalars(
        db_session, select(PurchaseList).where(PurchaseList.id == uuid.UUID(list_id))
    )
    assert plists[0].status == "stored"


async def test_pos_pay_duplicate_burst_cannot_overpay(client, db_session):
    """同一订单并发重复收款（不同 transaction_id）→ paid 不超 total、成功支付恰
    1 笔，重复请求 409，无 5xx.

    PG 区分度：pay 的锚点 FOR UPDATE 串行化 paid_amount 读-改-写；SQLite 单连接
    下真正同时在途的多笔收款会互相污染（实验见模块 docstring），重叠窗口属
    testcontainers 真 PG 的后续项。
    """
    await _seed_stock(db_session)
    create = await client.post(
        "/api/v1/pos/orders",
        json={
            "client_id": "conc-pay-" + uuid.uuid4().hex[:8],
            "payment_method": "credit",
            "customer_name": "并发收款客户",
            "items": [{"product_id": 1, "quantity": 2, "unit_price": 5.0}],
        },
    )
    assert create.status_code == 200, create.text
    order_id = create.json()["data"]["order_id"]
    total = Decimal(str(create.json()["data"]["total_amount"]))

    first = await client.post(
        f"/api/v1/pos/orders/{order_id}/pay",
        json={"amount": float(total), "method": "cash", "transaction_id": "conc-txn-first"},
    )
    assert first.status_code == 200, first.text

    tasks = [
        client.post(
            f"/api/v1/pos/orders/{order_id}/pay",
            json={
                "amount": float(total),
                "method": "cash",
                "transaction_id": "conc-txn-dup-" + str(i),
            },
        )
        for i in range(3)
    ]
    resps = await asyncio.gather(*tasks)
    _assert_no_5xx(resps)
    for r in resps:
        assert r.status_code == 409

    orders = await _scalars(
        db_session, select(SaleOrder).where(SaleOrder.id == uuid.UUID(order_id))
    )
    assert orders[0].paid_amount == total
    cash_payments = await _scalars(
        db_session,
        select(Payment).where(
            Payment.order_id == uuid.UUID(order_id),
            Payment.status == "success",
            Payment.method == "cash",
        ),
    )
    # 赊账开单本身会落一笔 credit 收款行；真金收款只允许这一笔 cash
    assert len(cash_payments) == 1
    assert cash_payments[0].amount == total


async def test_pos_refund_duplicate_burst_cannot_overrefund(client, db_session):
    """并发重复整单退款 → refunded 恰等于实付、反向支付流水恰 1 笔，重复 409，无 5xx.

    PG 区分度：refund 的锚点 FOR UPDATE + refunded 状态检查让后到者 409；
    SQLite 下真正在途的双退款同样只在 PG harness 有区分度。
    """
    await _seed_stock(db_session)
    create = await client.post(
        "/api/v1/pos/orders",
        json={
            "client_id": "conc-refund-" + uuid.uuid4().hex[:8],
            "payment_method": "cash",
            "items": [{"product_id": 1, "quantity": 2, "unit_price": 5.0}],
        },
    )
    assert create.status_code == 200, create.text
    order_id = create.json()["data"]["order_id"]
    data = create.json()["data"]
    assert Decimal(str(data["paid_amount"])) == Decimal(str(data["total_amount"]))

    first = await client.post(
        f"/api/v1/pos/orders/{order_id}/refund",
        json={"reason": "并发退款回归", "return_to_stock": False},
    )
    assert first.status_code == 200, first.text

    resps = await _burst(
        client,
        f"/api/v1/pos/orders/{order_id}/refund",
        json={"reason": "并发退款回归", "return_to_stock": False},
        n=2,
    )
    _assert_no_5xx(resps)
    for r in resps:
        assert r.status_code == 409

    orders = await _scalars(
        db_session, select(SaleOrder).where(SaleOrder.id == uuid.UUID(order_id))
    )
    order = orders[0]
    assert order.refunded_amount == order.paid_amount
    assert order.refunded_amount == order.total_amount
    assert order.status == "refunded"
    refunds = await _scalars(
        db_session,
        select(Payment).where(
            Payment.order_id == uuid.UUID(order_id), Payment.status == "refunded"
        ),
    )
    assert len(refunds) == 1
    assert refunds[0].amount == -order.total_amount


async def test_idempotency_key_duplicate_burst_writes_once(auth_client, db_session, monkeypatch):
    """同一 Idempotency-Key 并发提交同一写请求 → 业务侧恰 1 次落库，重复请求
    重放首响应，无 5xx.

    占位行（status_code=102）先于业务写入提交，是并发下的第一道防线。本 harness
    无法安全复现「占位行与业务写入同时在途」的重叠窗口（见模块 docstring）；该
    窗口在 PG/独立连接 harness 下由 idempotency_records 唯一约束 + 102 状态短路
    保证（约束本身见 test_idempotency_unique_constraints_backstop）。
    """

    async def fake_code2session(code):
        return "conc-idem-openid"

    monkeypatch.setattr("app.routers.auth.wechat_code2session", fake_code2session)
    login = await auth_client.post("/api/v1/auth/wechat-login", json={"code": "conc-idem-login"})
    assert login.status_code == 200, login.text
    token = login.json()["data"]["token"]

    headers = {"Authorization": "Bearer " + token, "Idempotency-Key": "conc-burst-key-0001"}
    payload = {"content": "并发幂等回归"}

    first = await auth_client.post("/api/v1/feedback", json=payload, headers=headers)
    assert first.status_code == 200, first.text

    resps = await _burst(auth_client, "/api/v1/feedback", json=payload, headers=headers, n=5)
    _assert_no_5xx(resps)
    for r in resps:
        assert r.status_code == 200
        assert r.json() == first.json()

    async with db_session() as s:
        fb_count = (await s.execute(select(func.count(MerchantFeedback.id)))).scalar()
    assert fb_count == 1
    records = await _scalars(
        db_session,
        select(IdempotencyRecord).where(IdempotencyRecord.idempotency_key == "conc-burst-key-0001"),
    )
    assert len(records) == 1
    assert records[0].status_code == 200


async def test_idempotency_unique_constraints_backstop(db_session):
    """幂等唯一约束回补：inventory_records / supplier_payables / idempotency_records
    的 (商户, 幂等键) 唯一约束，是并发重叠窗口里的最后一道防线——重复键落库直接
    IntegrityError，账本最多一套数据（PG 行锁是第一道；SQLite 行锁被忽略后只剩
    这道，因此必须回归保护）。
    """
    from sqlalchemy.exc import IntegrityError

    now = datetime.now(UTC)

    async with db_session() as s:
        base = {
            "merchant_id": MID,
            "product_id": 1,
            "quantity": Decimal("1"),
            "unit": "斤",
            "event_type": "purchase",
            "event_time": now,
            "source": "manual",
        }
        s.add(InventoryRecord(idempotency_key="backstop-inv-1", **base))
        await s.flush()
        s.add(InventoryRecord(idempotency_key="backstop-inv-1", **base))
        with pytest.raises(IntegrityError):
            await s.flush()
        await s.rollback()

        payable = {
            "merchant_id": MID,
            "supplier_id": uuid.uuid4(),
            "direction": "purchase",
            "amount": Decimal("10"),
            "purchase_list_id": uuid.uuid4(),
        }
        s.add(SupplierPayable(idempotency_key="backstop-pay-1", **payable))
        await s.flush()
        s.add(SupplierPayable(idempotency_key="backstop-pay-1", **payable))
        with pytest.raises(IntegrityError):
            await s.flush()
        await s.rollback()

        record = {
            "idempotency_key": "backstop-idem-1",
            "tenant_id": "backstop-tenant",
            "operation": "POST:/api/v1/feedback",
            "request_hash": "hash-1",
            "status_code": 102,
        }
        s.add(IdempotencyRecord(**record))
        await s.flush()
        s.add(IdempotencyRecord(**record))
        with pytest.raises(IntegrityError):
            await s.flush()
        await s.rollback()


async def test_pos_order_client_id_unique_race_loser_returns_existing(client, db_session, monkeypatch):
    """并发同 client_id 的真时序竞态（QA-10/F-02）：输者预检查时赢家尚未提交
    （scalar 落空），到 flush 才撞 uq_sale_order_client_per_merchant——兜底必须在
    flush 处回查赢家按幂等重放返回 200，而不是漏接成 500。

    单连接 harness 无法让两事务真实重叠（见模块 docstring），这里按生产时序做
    定向注入：scalar 仅对 SaleOrder 预检查语句放空一次；flush 仅在刷入目标
    SaleOrder 时抛一次 IntegrityError——其余语句/对象不受影响，依赖链
    （结算锁/租户门禁/配额）原样执行。
    """
    from sqlalchemy.ext.asyncio import AsyncSession
    from sqlalchemy.exc import IntegrityError

    cid = "race-flush-" + uuid.uuid4().hex[:8]
    winner_id = uuid.uuid4()
    async with db_session() as s:
        s.add(
            SaleOrder(
                id=winner_id,
                merchant_id=MID,
                order_no="POS-RACE-" + uuid.uuid4().hex[:10],
                status="paid",
                client_id=cid,
                total_amount=Decimal("10"),
                paid_amount=Decimal("10"),
            )
        )
        await s.commit()

    real_scalar, real_flush = AsyncSession.scalar, AsyncSession.flush
    fired = {"scalar": False, "flush": False}

    async def racy_scalar(self, statement, *a, **k):
        entity = None
        try:
            entity = statement.column_descriptions[0]["entity"]
        except Exception:
            pass
        if entity is SaleOrder and not fired["scalar"]:
            fired["scalar"] = True  # 模拟赢家此刻尚未提交，预检查落空
            return None
        return await real_scalar(self, statement, *a, **k)

    async def racy_flush(self, *a, **k):
        target = any(
            isinstance(o, SaleOrder) and getattr(o, "client_id", None) == cid
            for o in list(self.new)
        )
        if target and not fired["flush"]:
            fired["flush"] = True  # 模拟唯一约束在 INSERT（flush）时触发
            raise IntegrityError(
                "INSERT INTO sale_orders ...",
                None,
                Exception(
                    "UNIQUE constraint failed: sale_orders.merchant_id, sale_orders.client_id"
                ),
            )
        return await real_flush(self, *a, **k)

    monkeypatch.setattr(AsyncSession, "scalar", racy_scalar)
    monkeypatch.setattr(AsyncSession, "flush", racy_flush)

    resp = await client.post(
        "/api/v1/pos/orders",
        json={
            "client_id": cid,
            "payment_method": "cash",
            "items": [{"product_id": 1, "quantity": 2, "unit_price": 5.0}],
        },
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert data["duplicate"] is True
    assert data["order_id"] == str(winner_id)
    assert fired == {"scalar": True, "flush": True}  # 兜底路径确实被走到

    orders = await _scalars(
        db_session, select(SaleOrder).where(SaleOrder.client_id == cid)
    )
    assert len(orders) == 1  # 赢家订单原样，输者未产生第二行


async def test_staff_create_duplicate_phone_conflict_409(client, db_session):
    """同商户重复手机号建员工 → 409 而非 500（QA-12/MC-01）：唯一约束冲突必须
    转义为明确冲突语义，不允许 IntegrityError 直抛。"""
    payload = {"name": "张三", "role": "cashier", "phone": "13900000001", "pin_code": "123456"}
    first = await client.post("/api/v1/staff", json=payload)
    assert first.status_code == 200, first.text

    dup = await client.post(
        "/api/v1/staff",
        json={"name": "李四", "role": "cashier", "phone": "13900000001", "pin_code": "123456"},
    )
    assert dup.status_code == 409, dup.text
    assert "手机号" in dup.json()["detail"]

    staff = await _scalars(
        db_session, select(StaffMember).where(StaffMember.phone == "13900000001")
    )
    assert len(staff) == 1
    assert staff[0].name == "张三"


async def test_staff_update_phone_conflict_409(client, db_session):
    """把员工手机号改成同商户已有号码 → 409 而非 500（QA-12/MC-01 同族）。"""
    a = await client.post(
        "/api/v1/staff",
        json={"name": "甲", "role": "cashier", "phone": "13900000011", "pin_code": "123456"},
    )
    b = await client.post(
        "/api/v1/staff",
        json={"name": "乙", "role": "cashier", "phone": "13900000012", "pin_code": "123456"},
    )
    assert a.status_code == 200 and b.status_code == 200, (a.text, b.text)

    upd = await client.put(
        f"/api/v1/staff/{b.json()['data']['staff_id']}", json={"phone": "13900000011"}
    )
    assert upd.status_code == 409, upd.text

    staff = await _scalars(
        db_session, select(StaffMember).where(StaffMember.phone == "13900000011")
    )
    assert len(staff) == 1
    assert staff[0].name == "甲"  # 原归属未被打错
