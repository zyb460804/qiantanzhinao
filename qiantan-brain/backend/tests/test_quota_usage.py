"""SaaS 计量 record_usage 的累加与并发语义测试。

回归（CRITICAL）：record_usage 旧实现是无锁的应用层读-改-写
（``record.value += value``），IntegrityError 重试只覆盖插入竞态，
并发写同一 (tenant_id, metric, date) 会丢失更新（20 并发协程实测
最终 value=1）。改为单条原子 UPSERT（ON CONFLICT DO UPDATE SET
value = usage_records.value + excluded.value）后，本文件验证：
  - 顺序 upsert 重复累加正确（首次 insert → 后续 upsert 累加）
  - asyncio.gather 并发 N 次后 value == N * inc（无丢失更新）
"""

import asyncio
import uuid

from sqlalchemy import select

from app.core.quota import record_usage
from app.models.saas import Tenant, UsageRecord


async def _make_tenant_id(db_session) -> uuid.UUID:
    """插入一个租户并返回其 id（usage_records.tenant_id 外键指向 tenants）。"""
    tenant_id = uuid.uuid4()
    async with db_session() as session:
        session.add(
            Tenant(
                id=tenant_id,
                name="计量测试租户",
                slug=f"quota-test-{uuid.uuid4().hex[:12]}",
            )
        )
        await session.commit()
    return tenant_id


async def test_record_usage_sequential_upserts_accumulate(db_session):
    """首次 insert、后续 upsert 均按增量累加，且当天只落一行。"""
    tenant_id = await _make_tenant_id(db_session)

    async with db_session() as session:
        await record_usage(session, tenant_id, "api_calls", 2)
        await record_usage(session, tenant_id, "api_calls", 3)
        await record_usage(session, tenant_id, "api_calls", 5)

    async with db_session() as session:
        rows = (
            (await session.execute(select(UsageRecord).where(UsageRecord.tenant_id == tenant_id)))
            .scalars()
            .all()
        )
        assert len(rows) == 1  # 唯一约束 uq_usage_per_tenant_metric_date 只保留一行
        assert rows[0].metric == "api_calls"
        assert rows[0].value == 2 + 3 + 5


async def test_record_usage_concurrent_calls_do_not_lose_updates(db_session):
    """并发 N 次 record_usage 同一 (tenant, metric, date) → 最终 value == N * inc。

    旧实现（应用层读-改-写 + 仅插入竞态重试）在本测试下丢失更新或
    重试耗尽抛 IntegrityError。
    """
    tenant_id = await _make_tenant_id(db_session)
    n = 20
    inc = 1

    async def one_call() -> None:
        async with db_session() as session:
            await record_usage(session, tenant_id, "api_calls", inc)

    await asyncio.gather(*(one_call() for _ in range(n)))

    async with db_session() as session:
        record = (
            await session.execute(
                select(UsageRecord).where(
                    UsageRecord.tenant_id == tenant_id,
                    UsageRecord.metric == "api_calls",
                )
            )
        ).scalar_one()
        assert record.value == n * inc
