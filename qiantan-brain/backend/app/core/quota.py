"""配额检查与用量记录工具。

功能：
  - check_quota: 检查租户某指标是否超出套餐配额
  - record_usage: 记录用量（累加到当天）— 带并发冲突重试
  - get_current_usage: 获取当前月份的累计用量
  - get_quota_limit: 从租户的套餐中获取配额上限
  - get_usage_trend: 获取最近 N 天的用量趋势
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.saas import Plan, Tenant, UsageRecord


logger = logging.getLogger(__name__)


async def get_tenant_plan(db: AsyncSession, tenant_id: uuid.UUID) -> Plan | None:
    """获取租户当前套餐。"""
    tenant = await db.get(Tenant, tenant_id)
    if tenant is None or tenant.plan_id is None:
        return None
    return await db.get(Plan, tenant.plan_id)


async def get_quota_limit(db: AsyncSession, tenant_id: uuid.UUID, metric: str) -> int:
    """获取租户某指标的配额上限。"""
    plan = await get_tenant_plan(db, tenant_id)
    if plan is None:
        return 0
    limits = {
        "api_calls": plan.max_api_calls_monthly,
        "storage_mb": plan.max_storage_mb,
        "merchant_count": plan.max_merchants,
    }
    return limits.get(metric, 0)


async def get_current_usage(db: AsyncSession, tenant_id: uuid.UUID, metric: str) -> int:
    """获取当前月份的累计用量。"""
    now = datetime.now(UTC)
    month_prefix = now.strftime("%Y-%m")

    result = await db.execute(
        select(func.coalesce(func.sum(UsageRecord.value), 0)).where(
            UsageRecord.tenant_id == tenant_id,
            UsageRecord.metric == metric,
            UsageRecord.recorded_date.like(f"{month_prefix}%"),
        )
    )
    return int(result.scalar() or 0)


async def check_quota(db: AsyncSession, tenant_id: uuid.UUID, metric: str) -> dict[str, Any]:
    """检查租户是否超出配额。

    返回: {exceeded, current, limit, remaining, metric}
    """
    current = await get_current_usage(db, tenant_id, metric)
    limit = await get_quota_limit(db, tenant_id, metric)
    remaining = max(0, limit - current)
    exceeded = current >= limit if limit > 0 else False

    return {
        "metric": metric,
        "current": current,
        "limit": limit,
        "remaining": remaining,
        "exceeded": exceeded,
    }


async def record_usage(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    metric: str,
    value: int = 1,
    max_retries: int = 3,
) -> None:
    """记录用量（累加到当天，不存在则创建）。

    处理并发冲突：当多请求同时写入同一 (tenant_id, metric, date)
    组合时，SELECT-then-INSERT 会产生唯一约束冲突。
    此时自动重试（先 SELECT 已有记录 → UPDATE）。
    """
    today = datetime.now(UTC).strftime("%Y-%m-%d")

    for attempt in range(max_retries):
        try:
            # 查找当天记录
            result = await db.execute(
                select(UsageRecord).where(
                    UsageRecord.tenant_id == tenant_id,
                    UsageRecord.metric == metric,
                    UsageRecord.recorded_date == today,
                )
            )
            record = result.scalar_one_or_none()

            if record:
                record.value += value
            else:
                record = UsageRecord(
                    tenant_id=tenant_id,
                    metric=metric,
                    recorded_date=today,
                    value=value,
                )
                db.add(record)
            await db.commit()
            return
        except IntegrityError:
            await db.rollback()
            if attempt < max_retries - 1:
                logger.debug(
                    "record_usage 并发冲突，重试 %d/%d (tenant=%s metric=%s)",
                    attempt + 1,
                    max_retries,
                    tenant_id,
                    metric,
                )
                await asyncio.sleep(0.1 * (attempt + 1))  # 指数退避
            else:
                logger.error(
                    "record_usage 重试耗尽 (tenant=%s metric=%s date=%s)",
                    tenant_id,
                    metric,
                    today,
                )
                raise


async def get_usage_trend(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    metric: str,
    days: int = 30,
) -> list[dict[str, Any]]:
    """获取最近 N 天的用量趋势。"""
    end_date = datetime.now(UTC)
    start_date = end_date - timedelta(days=days)

    result = await db.execute(
        select(UsageRecord.recorded_date, UsageRecord.value).where(
            UsageRecord.tenant_id == tenant_id,
            UsageRecord.metric == metric,
            UsageRecord.recorded_date >= start_date.strftime("%Y-%m-%d"),
            UsageRecord.recorded_date <= end_date.strftime("%Y-%m-%d"),
        )
    )
    rows = result.fetchall()

    # 填充缺失的日期
    date_map = {row[0]: row[1] for row in rows}
    trend = []
    current_date = start_date
    while current_date <= end_date:
        date_str = current_date.strftime("%Y-%m-%d")
        trend.append(
            {
                "date": date_str,
                "value": date_map.get(date_str, 0),
            }
        )
        current_date += timedelta(days=1)

    return trend


async def get_all_quotas(db: AsyncSession, tenant_id: uuid.UUID) -> list[dict[str, Any]]:
    """获取租户所有指标的配额状态。"""
    metrics = ["api_calls", "storage_mb", "merchant_count"]
    results = []
    for metric in metrics:
        quota_info = await check_quota(db, tenant_id, metric)
        results.append(quota_info)
    return results
