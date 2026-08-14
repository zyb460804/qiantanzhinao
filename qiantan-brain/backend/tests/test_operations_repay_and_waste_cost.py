"""第五轮 L4 定向测试 — 客户回款并发/幂等（V2-H2）+ 报损成本落账（V1-M3/M4）。

诚实边界（与 tests/test_concurrency_regression.py 同一 harness）：测试库是
单连接 SQLite，真正在途重叠的双回款事务在本 harness 非确定（见该文件
docstring）。并发场景采用「首笔回款已提交 + gather 并发放出重复请求」形态
—— 这正是 PG advisory lock 下第二个事务在锁上等待、首事务提交后重读状态
所走到的路径；真正在途的重叠窗口由 (merchant, idempotency_key) 唯一约束 +
IntegrityError 回查重放兜底（test_repay_unique_constraint_backstop 直测）。
真 PG 下的锁行为属 testcontainers 后续项。
"""

from __future__ import annotations

import asyncio
import uuid
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from tests.conftest import TEST_MERCHANT_ID

from app.models.accounts import CustomerReceivable
from app.models.inventory import InventoryRecord
from app.services.accounts_service import get_customer_balance
from app.services.batch import create_batch


MID = uuid.UUID(TEST_MERCHANT_ID)

pytestmark = pytest.mark.asyncio


async def _seed_charge(
    db_session, customer_name: str = "回款并发客户", amount: Decimal = Decimal("100")
) -> None:
    async with db_session() as session:
        session.add(
            CustomerReceivable(
                merchant_id=MID,
                customer_name=customer_name,
                direction="charge",
                amount=amount,
            )
        )
        await session.commit()


async def _repay(client, *, amount, key=None, name="回款并发客户"):
    payload = {"customer_name": name, "amount": amount}
    if key is not None:
        payload["idempotency_key"] = key
    return await client.post("/api/v1/ops/customers/repay", json=payload)


# ═══════════════════════════════════════════════════════════
# 客户回款：并发 + 幂等（V2-H2）
# ═══════════════════════════════════════════════════════════


class TestCustomerRepayConcurrency:
    async def test_duplicate_burst_cannot_make_balance_negative(self, client, db_session):
        """并发双回款余额不为负：余额 100，首笔全额回款提交后 gather 5 笔
        全额回款 → 全部 400（无欠款），余额保持 0，绝不为负。"""
        await _seed_charge(db_session, amount=Decimal("100"))

        first = await _repay(client, amount=100)
        assert first.status_code == 200, first.text
        assert first.json()["data"]["new_balance"] == 0

        resps = await asyncio.gather(*(_repay(client, amount=100) for _ in range(5)))
        for r in resps:
            assert r.status_code < 500, r.text
            assert r.status_code == 400
            assert "没有欠款" in r.json()["detail"]

        async with db_session() as session:
            balance = await get_customer_balance(session, MID, "回款并发客户")
        assert balance == 0

    async def test_same_key_duplicate_burst_replays_single_repayment(
        self, client, db_session
    ):
        """同键并发重试 5 连发 → 恰 1 笔回款流水，全部幂等重放，余额不重复扣。"""
        await _seed_charge(db_session, amount=Decimal("100"))

        first = await _repay(client, amount=60, key="repay-key-burst")
        assert first.status_code == 200, first.text
        assert first.json()["data"]["new_balance"] == 40

        resps = await asyncio.gather(
            *(_repay(client, amount=60, key="repay-key-burst") for _ in range(5))
        )
        for r in resps:
            assert r.status_code == 200, r.text
            assert r.json()["data"]["new_balance"] == 40
            assert "幂等键已存在" in r.json()["message"]

        async with db_session() as session:
            repays = (
                (
                    await session.execute(
                        select(CustomerReceivable).where(
                            CustomerReceivable.merchant_id == MID,
                            CustomerReceivable.direction == "repay",
                        )
                    )
                )
                .scalars()
                .all()
            )
            balance = await get_customer_balance(session, MID, "回款并发客户")
        assert len(repays) == 1
        assert repays[0].amount == Decimal("60.00")
        assert balance == 40

    async def test_same_key_different_amount_rejected(self, client, db_session):
        """同键不同金额（或不同客户）→ 409，键不得被挪用。"""
        await _seed_charge(db_session, amount=Decimal("100"))
        first = await _repay(client, amount=30, key="repay-key-conflict")
        assert first.status_code == 200, first.text

        second = await _repay(client, amount=70, key="repay-key-conflict")
        assert second.status_code == 409
        assert "另一笔回款" in second.json()["detail"]

    async def test_serial_full_then_partial_repay_cannot_overpay(self, client, db_session):
        """顺序两笔（各无客户端键）回款：第二笔超额 → 400，余额不为负。"""
        await _seed_charge(db_session, amount=Decimal("100"))

        first = await _repay(client, amount=60)
        assert first.status_code == 200
        assert first.json()["data"]["new_balance"] == 40

        # 自动幂等键非空（服务端生成），顺序第二笔读到提交后的余额 40
        second = await _repay(client, amount=60)
        assert second.status_code == 400
        assert "不能超过当前欠款" in second.json()["detail"]

        async with db_session() as session:
            balance = await get_customer_balance(session, MID, "回款并发客户")
            repays = (
                (
                    await session.execute(
                        select(CustomerReceivable).where(
                            CustomerReceivable.merchant_id == MID,
                            CustomerReceivable.direction == "repay",
                        )
                    )
                )
                .scalars()
                .all()
            )
        assert balance == 40
        # 无客户端键 → 服务端生成的确定性压缩键非空且在列宽内
        assert all(r.idempotency_key and len(r.idempotency_key) <= 64 for r in repays)
        assert all(r.idempotency_key.startswith("customer-repay:") for r in repays)

    async def test_repay_unique_constraint_backstop(self, db_session):
        """(merchant, idempotency_key) 唯一约束直测：同键第二笔回款落库即
        IntegrityError —— 重叠并发窗口的最后一道防线（PG 锁是第一道）。"""
        async with db_session() as session:
            base = {
                "merchant_id": MID,
                "customer_name": "回款约束直测",
                "direction": "repay",
                "amount": Decimal("10"),
            }
            session.add(CustomerReceivable(idempotency_key="backstop-repay-1", **base))
            await session.flush()
            session.add(CustomerReceivable(idempotency_key="backstop-repay-1", **base))
            with pytest.raises(IntegrityError):
                await session.flush()
            await session.rollback()


# ═══════════════════════════════════════════════════════════
# 报损成本落账（V1-M3）+ 自动幂等键列宽（V1-M4）
# ═══════════════════════════════════════════════════════════


class TestWasteCost:
    async def test_waste_records_fifo_cost(self, client, db_session):
        """报损流水带 FIFO 成本：加权 unit_cost + 实际 total_amount；
        报损分析 cost 不再恒 0（V1-M3）。"""
        async with db_session() as session:
            await create_batch(
                session,
                MID,
                1,
                "白菜",
                "waste-cost-batch",
                Decimal("10"),
                unit_cost=Decimal("2.00"),
            )
            await session.commit()

        res = await client.post(
            "/api/v1/ops/waste", json={"product_id": 1, "quantity": 5, "reason": "腐烂"}
        )
        assert res.status_code == 200, res.text

        async with db_session() as session:
            record = (
                (
                    await session.execute(
                        select(InventoryRecord).where(InventoryRecord.event_type == "waste")
                    )
                )
                .scalars()
                .one()
            )
        assert record.unit_cost == Decimal("2.00")
        assert record.total_amount == Decimal("10.00")
        # 自动幂等键（V1-M4）：short_idem_key 压缩，非空且 ≤ 64 字符列宽
        assert record.idempotency_key is not None
        assert len(record.idempotency_key) <= 64
        assert record.idempotency_key.startswith("waste:")

        analysis = await client.get("/api/v1/ops/waste/analysis?days=30")
        assert analysis.status_code == 200
        by_reason = analysis.json()["data"]["by_reason"]
        costs = [Decimal(str(row["cost"])) for row in by_reason if row["reason"].startswith("腐烂")]
        assert costs and costs[0] == Decimal("10.0")

    async def test_waste_mixed_cost_batches_leaves_cost_null(self, client, db_session):
        """被消费批次任一缺成本 → 不猜成本：unit_cost/total_amount 保持 NULL。"""
        async with db_session() as session:
            await create_batch(
                session,
                MID,
                1,
                "白菜",
                "waste-cost-known",
                Decimal("5"),
                unit_cost=Decimal("3.00"),
            )
            await create_batch(session, MID, 1, "白菜", "waste-cost-unknown", Decimal("5"))
            await session.commit()

        res = await client.post(
            "/api/v1/ops/waste", json={"product_id": 1, "quantity": 10, "reason": "过熟"}
        )
        assert res.status_code == 200, res.text

        async with db_session() as session:
            record = (
                (
                    await session.execute(
                        select(InventoryRecord).where(InventoryRecord.event_type == "waste")
                    )
                )
                .scalars()
                .one()
            )
        assert record.unit_cost is None
        assert record.total_amount is None
