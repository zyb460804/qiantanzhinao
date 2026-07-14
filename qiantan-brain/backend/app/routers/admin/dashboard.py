"""管理后台 Dashboard API — 统计概览数据。"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.admin_permissions import DASHBOARD_READ, require_admin_permission
from app.database import get_db
from app.models.merchant import Merchant
from app.models.saas import ApiKey, Invoice, Plan, Subscription, Tenant, UsageRecord


router = APIRouter(
    prefix="/api/admin",
    tags=["admin-dashboard"],
    dependencies=[Depends(require_admin_permission(DASHBOARD_READ))],
)


class DashboardStats(BaseModel):
    tenant_total: int
    tenant_active: int
    tenant_trial: int
    merchant_total: int
    plan_total: int
    subscription_active: int
    today_api_calls: int
    today_storage_mb: int


class TenantGrowth(BaseModel):
    date: str
    count: int


class PlanDistribution(BaseModel):
    plan_code: str
    plan_name: str
    tenant_count: int


class DashboardTrendItem(BaseModel):
    date: str
    api_calls: int
    storage_mb: int
    voice_seconds: int
    new_tenants: int


class DashboardAnalytics(BaseModel):
    range_days: int
    month_api_calls: int
    month_voice_seconds: int
    month_paid_revenue: Decimal
    active_api_keys: int
    active_tenant_rate: float
    trend: list[DashboardTrendItem]


@router.get("/dashboard", response_model=DashboardStats)
async def get_dashboard_stats(db: AsyncSession = Depends(get_db)):
    """获取 Dashboard 概览统计数据。"""
    # 租户统计
    tenant_total = await db.scalar(select(func.count(Tenant.id)))
    tenant_active = await db.scalar(select(func.count(Tenant.id)).where(Tenant.status == "active"))
    tenant_trial = await db.scalar(select(func.count(Tenant.id)).where(Tenant.status == "trial"))

    # 商户总数
    merchant_total = await db.scalar(select(func.count(Merchant.id)))

    # 套餐数
    plan_total = await db.scalar(select(func.count(Plan.id)).where(Plan.is_active == True))  # noqa: E712

    # 活跃订阅数
    subscription_active = await db.scalar(
        select(func.count(Subscription.id)).where(Subscription.status == "active")
    )

    # 今日用量
    today = datetime.now(UTC).strftime("%Y-%m-%d")
    today_api_result = await db.execute(
        select(func.coalesce(func.sum(UsageRecord.value), 0)).where(
            UsageRecord.metric == "api_calls",
            UsageRecord.recorded_date == today,
        )
    )
    today_api_calls = today_api_result.scalar() or 0

    today_storage_result = await db.execute(
        select(func.coalesce(func.sum(UsageRecord.value), 0)).where(
            UsageRecord.metric == "storage_mb",
            UsageRecord.recorded_date == today,
        )
    )
    today_storage_mb = today_storage_result.scalar() or 0

    return DashboardStats(
        tenant_total=tenant_total or 0,
        tenant_active=tenant_active or 0,
        tenant_trial=tenant_trial or 0,
        merchant_total=merchant_total or 0,
        plan_total=plan_total or 0,
        subscription_active=subscription_active or 0,
        today_api_calls=int(today_api_calls),
        today_storage_mb=int(today_storage_mb),
    )


@router.get("/dashboard/plan-distribution", response_model=list[PlanDistribution])
async def get_plan_distribution(db: AsyncSession = Depends(get_db)):
    """获取各套餐的租户分布。"""
    result = await db.execute(
        select(
            Plan.code,
            Plan.name,
            func.count(Tenant.id),
        )
        .outerjoin(Tenant, Tenant.plan_id == Plan.id)
        .where(Plan.is_active == True)  # noqa: E712
        .group_by(Plan.id, Plan.code, Plan.name)
        .order_by(Plan.sort_order)
    )
    rows = result.fetchall()
    return [
        PlanDistribution(plan_code=code, plan_name=name, tenant_count=count or 0)
        for code, name, count in rows
    ]


@router.get("/dashboard/analytics", response_model=DashboardAnalytics)
async def get_dashboard_analytics(
    days: int = Query(7, ge=7, le=30),
    db: AsyncSession = Depends(get_db),
):
    """Return platform usage and commercial trends for the admin dashboard."""
    now = datetime.now(UTC)
    today = now.date()
    start_date = today - timedelta(days=days - 1)
    month_start = today.replace(day=1).isoformat()

    usage_result = await db.execute(
        select(
            UsageRecord.recorded_date,
            func.sum(case((UsageRecord.metric == "api_calls", UsageRecord.value), else_=0)).label(
                "api_calls"
            ),
            func.sum(case((UsageRecord.metric == "storage_mb", UsageRecord.value), else_=0)).label(
                "storage_mb"
            ),
            func.sum(
                case((UsageRecord.metric == "voice_seconds", UsageRecord.value), else_=0)
            ).label("voice_seconds"),
        )
        .where(UsageRecord.recorded_date >= start_date.isoformat())
        .group_by(UsageRecord.recorded_date)
        .order_by(UsageRecord.recorded_date)
    )
    usage_by_date = {
        row.recorded_date: {
            "api_calls": int(row.api_calls or 0),
            "storage_mb": int(row.storage_mb or 0),
            "voice_seconds": int(row.voice_seconds or 0),
        }
        for row in usage_result
    }

    tenant_result = await db.execute(
        select(func.date(Tenant.created_at), func.count(Tenant.id))
        .where(Tenant.created_at >= datetime.combine(start_date, datetime.min.time()))
        .group_by(func.date(Tenant.created_at))
    )
    tenants_by_date = {str(row[0]): int(row[1] or 0) for row in tenant_result}

    trend = []
    for offset in range(days):
        day = start_date + timedelta(days=offset)
        key = day.isoformat()
        usage = usage_by_date.get(key, {})
        trend.append(
            DashboardTrendItem(
                date=key,
                api_calls=usage.get("api_calls", 0),
                storage_mb=usage.get("storage_mb", 0),
                voice_seconds=usage.get("voice_seconds", 0),
                new_tenants=tenants_by_date.get(key, 0),
            )
        )

    month_usage = await db.execute(
        select(
            func.sum(case((UsageRecord.metric == "api_calls", UsageRecord.value), else_=0)),
            func.sum(case((UsageRecord.metric == "voice_seconds", UsageRecord.value), else_=0)),
        ).where(UsageRecord.recorded_date >= month_start)
    )
    month_api_calls, month_voice_seconds = month_usage.one()

    month_paid_revenue = await db.scalar(
        select(func.coalesce(func.sum(Invoice.amount), 0)).where(
            Invoice.status == "paid",
            Invoice.paid_at.isnot(None),
            Invoice.paid_at >= datetime.combine(today.replace(day=1), datetime.min.time()),
        )
    )
    active_api_keys = await db.scalar(
        select(func.count(ApiKey.id)).where(ApiKey.is_active == True)  # noqa: E712
    )
    tenant_total = await db.scalar(select(func.count(Tenant.id))) or 0
    tenant_active = (
        await db.scalar(select(func.count(Tenant.id)).where(Tenant.status == "active")) or 0
    )

    return DashboardAnalytics(
        range_days=days,
        month_api_calls=int(month_api_calls or 0),
        month_voice_seconds=int(month_voice_seconds or 0),
        month_paid_revenue=month_paid_revenue or Decimal("0"),
        active_api_keys=active_api_keys or 0,
        active_tenant_rate=round(tenant_active / max(tenant_total, 1) * 100, 1),
        trend=trend,
    )
