"""客户赊账流水全量聚合测试（同型修复）。

背景：/ops/customers/{name}/ledger 曾只累加分页截断后的最近 limit 条明细，
流水超过 limit 后 total_charge/total_repay/balance 失真，与
/accounts/customer-balance（全量聚合）各说各话。总额/余额现在对该客户
全部流水聚合，明细列表保持分页。
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from tests.conftest import TEST_MERCHANT_ID

from app.models.accounts import CustomerReceivable


async def _seed_customer_ledger(db_session, charges: int, repays: int) -> str:
    """播种客户 + 指定条数流水：先 charge（更旧）后 repay（更新）。"""
    customer_name = f"长流水客户-{uuid.uuid4().hex[:6]}"
    async with db_session() as session:
        rows = []
        for i in range(charges):
            rows.append(
                CustomerReceivable(
                    merchant_id=uuid.UUID(TEST_MERCHANT_ID),
                    customer_name=customer_name,
                    direction="charge",
                    amount=Decimal("10.00"),
                    created_at=datetime.now(UTC).replace(tzinfo=None)
                    - timedelta(minutes=charges + repays - i),
                )
            )
        for i in range(repays):
            rows.append(
                CustomerReceivable(
                    merchant_id=uuid.UUID(TEST_MERCHANT_ID),
                    customer_name=customer_name,
                    direction="repay",
                    amount=Decimal("2.00"),
                    created_at=datetime.now(UTC).replace(tzinfo=None)
                    - timedelta(minutes=repays - i),
                )
            )
        session.add_all(rows)
        await session.commit()
    return customer_name


async def test_customer_ledger_totals_full_aggregation_beyond_limit(client, db_session):
    """流水 60 条 > limit 50：总额/余额必须按全量算，且与 customer-balance 一致。"""
    customer_name = await _seed_customer_ledger(db_session, charges=55, repays=5)

    ledger = await client.get(f"/api/v1/ops/customers/{customer_name}/ledger")
    assert ledger.status_code == 200, ledger.text
    data = ledger.json()["data"]

    # 明细保持分页：只返回最近 50 条（45 charge + 5 repay）
    assert len(data["items"]) == 50
    assert sum(1 for i in data["items"] if i["direction"] == "charge") == 45
    assert sum(1 for i in data["items"] if i["direction"] == "repay") == 5

    # 总额/余额是全量聚合：55*10 - 5*2 = 540，而非截断子集的 45*10 - 5*2
    assert data["total_charge"] == 550.0
    assert data["total_repay"] == 10.0
    assert data["balance"] == 540.0

    # 与 /accounts/customer-balance（全量聚合端点）口径一致
    balance = await client.get(f"/api/v1/accounts/customer-balance/{customer_name}")
    assert balance.status_code == 200, balance.text
    assert balance.json()["data"]["balance"] == data["balance"]


async def test_customer_ledger_explicit_limit_still_aggregates_full(client, db_session):
    """limit 显式传小值时明细截断更狠，总额/余额仍按全量算。"""
    customer_name = await _seed_customer_ledger(db_session, charges=10, repays=2)

    ledger = await client.get(f"/api/v1/ops/customers/{customer_name}/ledger?limit=3")
    assert ledger.status_code == 200, ledger.text
    data = ledger.json()["data"]

    assert len(data["items"]) == 3
    assert data["total_charge"] == 100.0
    assert data["total_repay"] == 4.0
    assert data["balance"] == 96.0
