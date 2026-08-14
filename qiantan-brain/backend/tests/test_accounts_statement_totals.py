"""往来账对账单全量聚合测试。

背景（CRITICAL 修复）：get_supplier_statement 曾对分页截断后的最近 limit
条明细求和计算 total_purchases/total_payments/current_balance，流水超过
limit 后余额失真，与 /accounts/supplier-balance（全量聚合）各说各话。
总额/余额现在对该供应商全部流水聚合，明细列表保持分页。
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from tests.conftest import TEST_MERCHANT_ID

from app.models.accounts import SupplierPayable
from app.models.catalog import Supplier


def _naive_utc_minutes_ago(minutes: int) -> datetime:
    """SQLite 列存 naive UTC；逐条错开分钟数保证分页顺序确定。"""
    return datetime.now(UTC).replace(tzinfo=None) - timedelta(minutes=minutes)


async def _seed_supplier_with_ledger(db_session, purchases: int, payments: int):
    """播种供应商 + 指定条数的流水：先 purchase（更旧）后 payment（更新）。"""
    async with db_session() as session:
        supplier = Supplier(
            merchant_id=uuid.UUID(TEST_MERCHANT_ID),
            name=f"对账供应商-{uuid.uuid4().hex[:6]}",
        )
        session.add(supplier)
        await session.flush()

        rows = []
        for i in range(purchases):
            rows.append(
                SupplierPayable(
                    merchant_id=uuid.UUID(TEST_MERCHANT_ID),
                    supplier_id=supplier.id,
                    direction="purchase",
                    amount=Decimal("10.00"),
                    created_at=_naive_utc_minutes_ago(purchases + payments - i),
                )
            )
        for i in range(payments):
            rows.append(
                SupplierPayable(
                    merchant_id=uuid.UUID(TEST_MERCHANT_ID),
                    supplier_id=supplier.id,
                    direction="payment",
                    amount=Decimal("2.00"),
                    created_at=_naive_utc_minutes_ago(payments - i),
                )
            )
        session.add_all(rows)
        await session.commit()
        return supplier.id


class TestSupplierStatementTotals:
    async def test_statement_totals_full_aggregation_beyond_limit(self, client, db_session):
        """流水 60 条 > limit 50：总额/余额必须按全量算，且与 supplier-balance 一致。"""
        supplier_id = await _seed_supplier_with_ledger(db_session, purchases=55, payments=5)

        statement = await client.get(f"/api/v1/accounts/supplier/{supplier_id}/statement")
        assert statement.status_code == 200, statement.text
        data = statement.json()["data"]

        # 明细保持分页：只返回最近 50 条（45 purchase + 5 payment）
        assert len(data["items"]) == 50
        assert sum(1 for i in data["items"] if i["direction"] == "purchase") == 45
        assert sum(1 for i in data["items"] if i["direction"] == "payment") == 5

        # 总额/余额是全量聚合：55*10 - 5*2 = 540，而非截断子集的 45*10 - 5*2
        assert data["total_purchases"] == 550.0
        assert data["total_payments"] == 10.0
        assert data["current_balance"] == 540.0

        # 与 /accounts/supplier-balance（全量聚合端点）口径一致
        balance = await client.get(f"/api/v1/accounts/supplier-balance/{supplier_id}")
        assert balance.status_code == 200, balance.text
        assert balance.json()["data"]["balance"] == data["current_balance"]

    async def test_statement_totals_match_balance_list_endpoint(self, client, db_session):
        """总额一致性也应体现在 supplier-balance 汇总列表端点。"""
        supplier_id = await _seed_supplier_with_ledger(db_session, purchases=3, payments=1)

        statement = await client.get(f"/api/v1/accounts/supplier/{supplier_id}/statement")
        summary = await client.get("/api/v1/accounts/supplier-balance")
        assert statement.status_code == 200
        assert summary.status_code == 200

        row = next(
            r
            for r in summary.json()["data"]["items"]
            if r["supplier_id"] == str(supplier_id)
        )
        assert row["balance"] == statement.json()["data"]["current_balance"]


class TestSupplierPaymentBoundary:
    async def _seed_payable(self, db_session, amount: str):
        async with db_session() as session:
            supplier = Supplier(
                merchant_id=uuid.UUID(TEST_MERCHANT_ID),
                name=f"付款供应商-{uuid.uuid4().hex[:6]}",
            )
            session.add(supplier)
            await session.flush()
            payable = SupplierPayable(
                merchant_id=uuid.UUID(TEST_MERCHANT_ID),
                supplier_id=supplier.id,
                direction="purchase",
                amount=Decimal(amount),
            )
            session.add(payable)
            await session.commit()
            return supplier.id, payable.id

    async def test_payment_equal_to_balance_succeeds(self, client, db_session):
        """付款金额 = 当前余额：应成功结清，余额归零（advisory lock 后的既有行为）。"""
        supplier_id, payable_id = await self._seed_payable(db_session, "100")
        response = await client.post(
            "/api/v1/accounts/supplier-payment",
            json={
                "supplier_id": str(supplier_id),
                "payable_ids": [str(payable_id)],
                "amount": 100,
                "method": "cash",
                "idempotency_key": f"pay-full-{uuid.uuid4()}",
            },
        )
        assert response.status_code == 200, response.text
        assert response.json()["data"]["new_balance"] == 0.0

        # 结清后再付 0.01 应被拒绝（所选应付余额与净余额均已为 0）
        over = await client.post(
            "/api/v1/accounts/supplier-payment",
            json={
                "supplier_id": str(supplier_id),
                "payable_ids": [str(payable_id)],
                "amount": 0.01,
                "method": "cash",
                "idempotency_key": f"pay-over-{uuid.uuid4()}",
            },
        )
        assert over.status_code == 400
        assert "不能超过" in over.json()["detail"]

    async def test_payment_above_net_balance_rejected(self, client, db_session):
        """选中应付有余额、但净余额被未分摊 payment 流水抵减时，按净余额拒绝。"""
        async with db_session() as session:
            supplier = Supplier(
                merchant_id=uuid.UUID(TEST_MERCHANT_ID),
                name=f"退货供应商-{uuid.uuid4().hex[:6]}",
            )
            session.add(supplier)
            await session.flush()
            payable = SupplierPayable(
                merchant_id=uuid.UUID(TEST_MERCHANT_ID),
                supplier_id=supplier.id,
                direction="purchase",
                amount=Decimal("100"),
            )
            # 退货抵扣：direction=payment 且未分摊到 settled_amount
            offset = SupplierPayable(
                merchant_id=uuid.UUID(TEST_MERCHANT_ID),
                supplier_id=supplier.id,
                direction="payment",
                amount=Decimal("60"),
                note="退货抵扣",
                settled=True,
                settled_amount=Decimal("0"),
            )
            session.add_all([payable, offset])
            await session.commit()
            supplier_id, payable_id = supplier.id, payable.id

        # 所选应付剩余 100，但供应商净余额 = 100 - 60 = 40 → 50 应被拒
        response = await client.post(
            "/api/v1/accounts/supplier-payment",
            json={
                "supplier_id": str(supplier_id),
                "payable_ids": [str(payable_id)],
                "amount": 50,
                "method": "wechat",
                "idempotency_key": f"pay-net-{uuid.uuid4()}",
            },
        )
        assert response.status_code == 400
        assert "不能超过供应商当前应付净余额" in response.json()["detail"]

        # 恰好等于净余额 40 应成功
        exact = await client.post(
            "/api/v1/accounts/supplier-payment",
            json={
                "supplier_id": str(supplier_id),
                "payable_ids": [str(payable_id)],
                "amount": 40,
                "method": "wechat",
                "idempotency_key": f"pay-net-exact-{uuid.uuid4()}",
            },
        )
        assert exact.status_code == 200, exact.text
        assert exact.json()["data"]["new_balance"] == 0.0
