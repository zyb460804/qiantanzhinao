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
import logging
import uuid

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from app.core.quota import record_usage
from app.models.saas import Plan, Tenant, UsageRecord


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


# ═══════════════════════════════════════════════════════════════════
# require_quota_check 软限额语义（并发 H4 残余）：
# check→record 窗口内并发可把用量推过上限 —— 超限告警但不阻断（计量非硬配额）
# ═══════════════════════════════════════════════════════════════════


async def _make_tenant_with_plan(db_session, api_limit: int) -> uuid.UUID:
    """插入绑定了套餐（指定 api_calls 上限）的租户，返回租户 id。"""
    tenant_id = uuid.uuid4()
    async with db_session() as session:
        plan = Plan(
            code=f"soft-limit-{uuid.uuid4().hex[:10]}",
            name="软限额测试套餐",
            max_api_calls_monthly=api_limit,
        )
        session.add(plan)
        await session.flush()
        session.add(
            Tenant(
                id=tenant_id,
                name="软限额测试租户",
                slug=f"soft-limit-{uuid.uuid4().hex[:12]}",
                status="active",
                plan_id=plan.id,
            )
        )
        await session.commit()
    return tenant_id


async def test_quota_check_soft_limit_warns_without_blocking(db_session, caplog):
    """入口未超限、记录后超限（并发窗口）→ 不抛异常，logger.warning 含
    tenant/metric/超限量。"""
    from app.core.tenant_context import require_quota_check

    tenant_id = await _make_tenant_with_plan(db_session, api_limit=2)

    async with db_session() as session:
        # 预置当前周期用量 1（< 上限 2）：模拟并发窗口——检查时未超，
        # 一次大增量（5）落账后越限
        await record_usage(session, tenant_id, "api_calls", 1)

    async with db_session() as session:
        tenant = await session.get(Tenant, tenant_id)
        with caplog.at_level(logging.WARNING, logger="app.core.tenant_context"):
            info = await require_quota_check("api_calls", 5, tenant, session)

    # 预检查视角：未超限（软限额语义 —— 记录后越限不回滚、不阻断）
    assert info["exceeded"] is False

    warning_records = [r for r in caplog.records if "soft-limit exceeded" in r.getMessage()]
    assert len(warning_records) == 1
    message = warning_records[0].getMessage()
    assert str(tenant_id) in message
    assert "api_calls" in message
    assert "overage=+4" in message  # 记录后 6，上限 2

    # 用量已如实落账（计量完整性优先）
    async with db_session() as session:
        record = (
            await session.execute(
                select(UsageRecord).where(
                    UsageRecord.tenant_id == tenant_id,
                    UsageRecord.metric == "api_calls",
                )
            )
        ).scalar_one()
        assert record.value == 1 + 5


async def test_quota_check_blocks_when_already_over_limit(db_session):
    """入口检查已超限 → 仍按既有硬闸门抛 429（软限额只针对并发窗口残余）。"""
    from app.core.tenant_context import require_quota_check

    tenant_id = await _make_tenant_with_plan(db_session, api_limit=2)
    async with db_session() as session:
        await record_usage(session, tenant_id, "api_calls", 2)  # 已达上限

    async with db_session() as session:
        tenant = await session.get(Tenant, tenant_id)
        with pytest.raises(HTTPException) as exc_info:
            await require_quota_check("api_calls", 1, tenant, session)
    assert exc_info.value.status_code == 429


async def test_quota_check_under_limit_no_warning(db_session, caplog):
    """正常用量（未越限）→ 不产生软限额告警。"""
    from app.core.tenant_context import require_quota_check

    tenant_id = await _make_tenant_with_plan(db_session, api_limit=100)
    async with db_session() as session:
        tenant = await session.get(Tenant, tenant_id)
        with caplog.at_level(logging.WARNING, logger="app.core.tenant_context"):
            info = await require_quota_check("api_calls", 1, tenant, session)

    assert info["exceeded"] is False
    assert not [r for r in caplog.records if "soft-limit exceeded" in r.getMessage()]
