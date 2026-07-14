"""管理后台运营中心 API — AiOps / 设备监控 / Dashboard 增强。

所有数据来自真实数据库查询，替代前端 Mock 数据。
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import and_, case, desc, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.admin_permissions import (
    AI_ACTION_READ,
    DASHBOARD_READ,
    require_admin_permission,
)
from app.database import get_db
from app.models.admin_audit import AdminAuditLog
from app.models.ai_action import AIAction
from app.models.device import Device
from app.models.merchant import Merchant
from app.models.saas import (
    Invoice,
    Plan,
    Subscription,
    Tenant,
    UsageRecord,
)


router = APIRouter(
    prefix="/api/admin",
    tags=["admin-operations"],
    dependencies=[Depends(require_admin_permission(DASHBOARD_READ))],
)

DEVICE_HEARTBEAT_TIMEOUT = timedelta(hours=1)


def _device_status(device: Device, now: datetime) -> str:
    """Classify a device from activation, error and heartbeat state."""
    if not device.is_active:
        return "offline"
    if device.last_error:
        return "warning"
    heartbeat = device.last_heartbeat
    if heartbeat is None:
        return "offline"
    if heartbeat.tzinfo is not None:
        heartbeat = heartbeat.astimezone(UTC).replace(tzinfo=None)
    return "online" if heartbeat >= now - DEVICE_HEARTBEAT_TIMEOUT else "offline"


# ────────────────────────────────────────────────────────────
# AiOps — AI Action 聚合
# ────────────────────────────────────────────────────────────


class AIActionItem(BaseModel):
    id: str
    tenant_name: str | None
    action_type: str
    title: str
    executed: bool
    result: str | None
    detail: str | None
    created_at: str | None


class AIActionListResponse(BaseModel):
    items: list[AIActionItem]
    total: int
    stats: dict


def _action_result_label(action: AIAction) -> str | None:
    """Map the persisted action state to the compact label used by the admin UI."""
    if action.status == "executed":
        return "success"
    if action.status == "failed":
        return "failed"
    return None


def _action_detail(action: AIAction) -> str | None:
    """Render JSON payload/result as readable text without leaking Python repr values."""
    detail = action.result if action.result is not None else action.payload
    if detail is None:
        return None
    return json.dumps(detail, ensure_ascii=False, default=str)


@router.get("/aiops/actions")
async def list_ai_actions(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    action_type: str | None = None,
    db: AsyncSession = Depends(get_db),
    _ai_permission=Depends(require_admin_permission(AI_ACTION_READ)),
) -> AIActionListResponse:
    """获取 AI Action 列表（通过 Merchant 归属聚合到所有租户）。"""
    count_query = select(func.count(AIAction.id))
    executed_query = select(func.count(AIAction.id)).where(AIAction.status == "executed")
    if action_type:
        count_query = count_query.where(AIAction.action_type == action_type)
        executed_query = executed_query.where(AIAction.action_type == action_type)

    total = await db.scalar(count_query) or 0
    executed_count = await db.scalar(executed_query) or 0

    query = (
        select(AIAction, Tenant.name)
        .select_from(AIAction)
        .outerjoin(Merchant, AIAction.merchant_id == Merchant.id)
        .outerjoin(Tenant, Merchant.tenant_id == Tenant.id)
    )
    if action_type:
        query = query.where(AIAction.action_type == action_type)
    query = (
        query.order_by(desc(AIAction.created_at)).offset((page - 1) * page_size).limit(page_size)
    )
    rows = (await db.execute(query)).all()

    items = [
        AIActionItem(
            id=str(action.id),
            tenant_name=tenant_name,
            action_type=action.action_type,
            title=action.title,
            executed=action.status == "executed",
            result=_action_result_label(action),
            detail=_action_detail(action),
            created_at=action.created_at.isoformat() if action.created_at else None,
        )
        for action, tenant_name in rows
    ]

    return AIActionListResponse(
        items=items,
        total=total,
        stats={
            "total": total,
            "executed": executed_count,
            "success": executed_count,
            "adoption_rate": f"{(executed_count / max(total, 1) * 100):.1f}%",
        },
    )


@router.get("/aiops/stats")
async def get_aiops_stats(
    db: AsyncSession = Depends(get_db),
    _ai_permission=Depends(require_admin_permission(AI_ACTION_READ)),
):
    """获取 AiOps 整体统计数据。"""
    total_actions = await db.scalar(select(func.count(AIAction.id))) or 0
    executed = (
        await db.scalar(select(func.count(AIAction.id)).where(AIAction.status == "executed")) or 0
    )
    failed = (
        await db.scalar(select(func.count(AIAction.id)).where(AIAction.status == "failed")) or 0
    )

    type_result = await db.execute(
        select(AIAction.action_type, func.count(AIAction.id))
        .group_by(AIAction.action_type)
        .order_by(desc(func.count(AIAction.id)))
        .limit(10)
    )
    by_type = {row[0]: row[1] for row in type_result.fetchall()}

    return {
        "total_actions": total_actions,
        "executed": executed,
        "successful": executed,
        "failed": failed,
        "adoption_rate": f"{(executed / max(total_actions, 1) * 100):.1f}%",
        "by_type": by_type,
    }


# ────────────────────────────────────────────────────────────
# 设备监控
# ────────────────────────────────────────────────────────────


class DeviceItem(BaseModel):
    id: str
    tenant_id: str | None
    tenant_name: str | None
    merchant_id: str
    merchant_name: str | None
    device_type: str
    device_name: str
    serial_number: str | None
    firmware_version: str | None
    status: str
    last_heartbeat: str | None
    last_error: str | None
    created_at: str | None


class DeviceListResponse(BaseModel):
    items: list[DeviceItem]
    total: int
    online: int
    offline: int
    warning: int


@router.get("/devices")
async def list_devices(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
) -> DeviceListResponse:
    """获取设备列表（所有租户聚合）。"""
    now = datetime.now(UTC).replace(tzinfo=None)
    heartbeat_threshold = now - DEVICE_HEARTBEAT_TIMEOUT
    count_result = await db.execute(
        select(
            func.count(Device.id),
            func.sum(
                case(
                    (
                        and_(
                            Device.is_active.is_(True),
                            Device.last_error.is_(None),
                            Device.last_heartbeat.isnot(None),
                            Device.last_heartbeat >= heartbeat_threshold,
                        ),
                        1,
                    ),
                    else_=0,
                )
            ),
            func.sum(
                case(
                    (
                        and_(
                            Device.is_active.is_(True),
                            Device.last_error.isnot(None),
                        ),
                        1,
                    ),
                    else_=0,
                )
            ),
        )
    )
    total, online, warning = count_result.one()
    total = int(total or 0)
    online = int(online or 0)
    warning = int(warning or 0)
    offline = max(0, total - online - warning)

    query = (
        select(Device)
        .order_by(desc(Device.created_at))
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    result = await db.execute(query)
    devices = result.scalars().all()

    # 批量加载关联数据
    merchant_ids = list({d.merchant_id for d in devices})
    merchants: dict = {}
    merchant_map: dict = {}
    tenant_map: dict = {}
    if merchant_ids:
        m_result = await db.execute(select(Merchant).where(Merchant.id.in_(merchant_ids)))
        merchants = {m.id: m for m in m_result.scalars().all()}
        merchant_map = {m.id: m.name for m in merchants.values()}

        tenant_ids_set = list({m.tenant_id for m in merchants.values() if m.tenant_id})
        if tenant_ids_set:
            t_result = await db.execute(select(Tenant).where(Tenant.id.in_(tenant_ids_set)))
            tenant_map = {t.id: t.name for t in t_result.scalars().all()}

    items = []
    for d in devices:
        m = merchants.get(d.merchant_id)
        status = _device_status(d, now)
        items.append(
            DeviceItem(
                id=str(d.id),
                tenant_id=str(m.tenant_id) if m and m.tenant_id else None,
                tenant_name=tenant_map.get(m.tenant_id) if m else None,
                merchant_id=str(d.merchant_id),
                merchant_name=merchant_map.get(d.merchant_id),
                device_type=d.device_type,
                device_name=d.device_name,
                serial_number=d.serial_number,
                firmware_version=d.firmware_version,
                status=status,
                last_heartbeat=d.last_heartbeat.isoformat() if d.last_heartbeat else None,
                last_error=d.last_error,
                created_at=d.created_at.isoformat() if d.created_at else None,
            )
        )

    return DeviceListResponse(
        items=items,
        total=total,
        online=online,
        offline=offline,
        warning=warning,
    )


# ────────────────────────────────────────────────────────────
# Dashboard 增强 — 活动流 + 待办
# ────────────────────────────────────────────────────────────


class ActivityItem(BaseModel):
    id: str
    type: str  # subscription/renewal/signup/alert/admin_action/device
    title: str
    description: str | None
    time: str
    tenant_id: str | None
    tenant_name: str | None
    target_path: str


class TodoItem(BaseModel):
    id: str
    type: str  # expiring_subscription/overdue_invoice/quota_warning/trial_expiring/device_offline
    title: str
    description: str | None
    priority: str  # high/medium/low
    tenant_id: str | None
    tenant_name: str | None
    target_path: str
    due_at: str | None = None


class EnhancedDashboardResponse(BaseModel):
    activities: list[ActivityItem]
    todos: list[TodoItem]


@router.get("/dashboard/activities", response_model=EnhancedDashboardResponse)
async def get_dashboard_activities(db: AsyncSession = Depends(get_db)):
    """获取 Dashboard 活动流和待办列表（替代 Mock 数据）。"""
    now = datetime.now(UTC).replace(tzinfo=None)

    # ── 活动流：最近订阅变更 + 新注册 + 管理员操作 ──
    activities: list[ActivityItem] = []

    # 最近 5 个订阅
    sub_result = await db.execute(
        select(Subscription, Tenant)
        .outerjoin(Tenant, Tenant.id == Subscription.tenant_id)
        .order_by(desc(Subscription.created_at))
        .limit(5)
    )
    for subscription, tenant in sub_result.all():
        activities.append(
            ActivityItem(
                id=str(subscription.id),
                type="subscription",
                title=f"{tenant.name if tenant else '未知'} 开通了订阅",
                description=f"状态: {subscription.status}",
                time=subscription.created_at.isoformat() if subscription.created_at else "",
                tenant_id=str(subscription.tenant_id),
                tenant_name=tenant.name if tenant else None,
                target_path="/subscriptions",
            )
        )

    # 最近 5 个新注册租户
    tenant_result = await db.execute(select(Tenant).order_by(desc(Tenant.created_at)).limit(5))
    for t in tenant_result.scalars().all():
        activities.append(
            ActivityItem(
                id=str(t.id),
                type="signup",
                title=f"{t.name} 新注册",
                description=f"状态: {t.status}",
                time=t.created_at.isoformat() if t.created_at else "",
                tenant_id=str(t.id),
                tenant_name=t.name,
                target_path=f"/tenants/{t.id}",
            )
        )

    # 最近 5 条管理员操作
    audit_result = await db.execute(
        select(AdminAuditLog).order_by(desc(AdminAuditLog.created_at)).limit(5)
    )
    for audit in audit_result.scalars().all():
        activities.append(
            ActivityItem(
                id=str(audit.id),
                type="admin_action",
                title=f"{audit.admin_email} {audit.action}",
                description=audit.resource_type,
                time=audit.created_at.isoformat() if audit.created_at else "",
                tenant_id=None,
                tenant_name=None,
                target_path="/audit",
            )
        )

    # 按时间排序取最近 15 条
    activities.sort(key=lambda x: x.time, reverse=True)
    activities = activities[:15]

    # ── 待办：即将到期订阅 / 逾期账单 / 配额告警 / 试用即将到期 ──
    todos: list[TodoItem] = []

    # 即将到期订阅（7天内）
    week_later = now + timedelta(days=7)
    expiring_result = await db.execute(
        select(Subscription, Tenant)
        .join(Tenant, Tenant.id == Subscription.tenant_id)
        .where(
            Subscription.status == "active",
            Subscription.current_period_end.isnot(None),
            Subscription.current_period_end <= week_later,
            Subscription.current_period_end >= now,
        )
    )
    for subscription, tenant in expiring_result.all():
        expiry_date = (
            subscription.current_period_end.strftime("%Y-%m-%d")
            if subscription.current_period_end
            else "近期"
        )
        todos.append(
            TodoItem(
                id=f"exp-{subscription.id}",
                type="expiring_subscription",
                title="订阅即将到期",
                description=f"{tenant.name} 的订阅将于 {expiry_date} 到期",
                priority="high",
                tenant_id=str(tenant.id),
                tenant_name=tenant.name,
                target_path="/subscriptions",
                due_at=(
                    subscription.current_period_end.isoformat()
                    if subscription.current_period_end
                    else None
                ),
            )
        )

    # 逾期账单
    overdue_result = await db.execute(
        select(Invoice, Tenant)
        .join(Tenant, Tenant.id == Invoice.tenant_id)
        .where(
            Invoice.status == "overdue",
        )
        .limit(10)
    )
    for invoice, tenant in overdue_result.all():
        todos.append(
            TodoItem(
                id=f"inv-{invoice.id}",
                type="overdue_invoice",
                title=f"账单逾期 ¥{invoice.amount}",
                description=f"{tenant.name} 账单 {invoice.invoice_no} 已逾期",
                priority="high",
                tenant_id=str(tenant.id),
                tenant_name=tenant.name,
                target_path="/invoices",
                due_at=invoice.due_date.isoformat() if invoice.due_date else None,
            )
        )

    # 试用即将到期
    trial_result = await db.execute(
        select(Tenant).where(
            Tenant.status == "trial",
            Tenant.trial_ends_at.isnot(None),
            Tenant.trial_ends_at <= week_later,
            Tenant.trial_ends_at >= now,
        )
    )
    for t in trial_result.scalars().all():
        trial_end = t.trial_ends_at.strftime("%Y-%m-%d") if t.trial_ends_at else "近期"
        todos.append(
            TodoItem(
                id=f"trial-{t.id}",
                type="trial_expiring",
                title="试用即将到期",
                description=f"{t.name} 试用将于 {trial_end} 到期",
                priority="medium",
                tenant_id=str(t.id),
                tenant_name=t.name,
                target_path=f"/tenants/{t.id}",
                due_at=t.trial_ends_at.isoformat() if t.trial_ends_at else None,
            )
        )

    # 本月 API 调用超过套餐 80% 的租户。
    month_prefix = now.strftime("%Y-%m")
    quota_result = await db.execute(
        select(
            Tenant,
            Plan,
            func.coalesce(func.sum(UsageRecord.value), 0),
        )
        .join(Plan, Plan.id == Tenant.plan_id)
        .outerjoin(
            UsageRecord,
            and_(
                UsageRecord.tenant_id == Tenant.id,
                UsageRecord.metric == "api_calls",
                UsageRecord.recorded_date.like(f"{month_prefix}%"),
            ),
        )
        .where(Tenant.status.in_(["trial", "active"]))
        .group_by(Tenant.id, Plan.id)
    )
    for tenant, plan, api_calls in quota_result.all():
        limit = plan.max_api_calls_monthly
        usage_percent = round(int(api_calls or 0) / limit * 100, 1) if limit > 0 else 0
        if usage_percent < 80:
            continue
        todos.append(
            TodoItem(
                id=f"quota-{tenant.id}",
                type="quota_warning",
                title=f"API 配额已使用 {usage_percent:.1f}%",
                description=(
                    f"{tenant.name} 本月已使用 {int(api_calls or 0):,} / {limit:,} 次"
                ),
                priority="high" if usage_percent >= 100 else "medium",
                tenant_id=str(tenant.id),
                tenant_name=tenant.name,
                target_path=f"/usage?tenant_id={tenant.id}",
            )
        )

    # 停用、心跳超时或上报错误的设备。
    heartbeat_threshold = now - DEVICE_HEARTBEAT_TIMEOUT
    device_result = await db.execute(
        select(Device, Tenant)
        .join(Merchant, Merchant.id == Device.merchant_id)
        .outerjoin(Tenant, Tenant.id == Merchant.tenant_id)
        .where(
            or_(
                Device.is_active.is_(False),
                Device.last_error.isnot(None),
                Device.last_heartbeat.is_(None),
                Device.last_heartbeat < heartbeat_threshold,
            )
        )
        .order_by(Device.last_heartbeat.asc())
        .limit(10)
    )
    for device, tenant in device_result.all():
        has_error = bool(device.last_error)
        todos.append(
            TodoItem(
                id=f"device-{device.id}",
                type="device_warning" if has_error else "device_offline",
                title="设备上报异常" if has_error else "设备离线",
                description=(
                    f"{device.device_name}: {device.last_error}"
                    if has_error
                    else f"{device.device_name} 超过 1 小时未上报心跳"
                ),
                priority="high" if has_error else "medium",
                tenant_id=str(tenant.id) if tenant else None,
                tenant_name=tenant.name if tenant else None,
                target_path="/devices",
                due_at=device.last_heartbeat.isoformat() if device.last_heartbeat else None,
            )
        )

    priority_order = {"high": 0, "medium": 1, "low": 2}
    todos.sort(key=lambda item: priority_order.get(item.priority, 3))

    return EnhancedDashboardResponse(activities=activities, todos=todos[:25])


# ────────────────────────────────────────────────────────────
# 运维监控 — 平台健康、设备心跳、请求量与 AI 任务
# ────────────────────────────────────────────────────────────


class MonitoringTrendItem(BaseModel):
    date: str
    api_calls: int
    ai_actions: int
    ai_failures: int


class MonitoringCheck(BaseModel):
    key: str
    name: str
    status: str
    value: str
    detail: str


class MonitoringOverview(BaseModel):
    status: str
    health_score: int
    refreshed_at: str
    range_days: int
    request_total: int
    today_requests: int
    average_daily_requests: float
    active_tenants: int
    device_total: int
    device_online: int
    device_stale: int
    device_errors: int
    ai_action_total: int
    ai_action_failed: int
    ai_success_rate: float
    checks: list[MonitoringCheck]
    trend: list[MonitoringTrendItem]


@router.get("/monitoring/overview", response_model=MonitoringOverview)
async def get_monitoring_overview(
    days: int = Query(1, ge=1, le=30),
    db: AsyncSession = Depends(get_db),
):
    """Build a truthful operational snapshot from persisted platform data."""
    from app.core.health_monitor import get_db_connectivity

    now = datetime.now(UTC)
    now_naive = now.replace(tzinfo=None)
    today = now.date()
    start_date = today - timedelta(days=days - 1)
    start_datetime = datetime.combine(start_date, datetime.min.time())
    heartbeat_threshold = now_naive - DEVICE_HEARTBEAT_TIMEOUT

    db_ok = await get_db_connectivity(db)

    usage_result = await db.execute(
        select(UsageRecord.recorded_date, func.sum(UsageRecord.value))
        .where(
            UsageRecord.metric == "api_calls",
            UsageRecord.recorded_date >= start_date.isoformat(),
        )
        .group_by(UsageRecord.recorded_date)
    )
    requests_by_date = {str(row[0]): int(row[1] or 0) for row in usage_result}
    request_total = sum(requests_by_date.values())

    device_result = await db.execute(
        select(
            func.count(Device.id),
            func.sum(
                case(
                    (
                        (Device.is_active == True)  # noqa: E712
                        & (Device.last_heartbeat >= heartbeat_threshold),
                        1,
                    ),
                    else_=0,
                )
            ),
            func.sum(
                case(
                    (
                        (Device.is_active == True)  # noqa: E712
                        & or_(
                            Device.last_heartbeat.is_(None),
                            Device.last_heartbeat < heartbeat_threshold,
                        ),
                        1,
                    ),
                    else_=0,
                )
            ),
            func.sum(case((Device.last_error.isnot(None), 1), else_=0)),
        )
    )
    device_total, device_online, device_stale, device_errors = device_result.one()
    device_total = int(device_total or 0)
    device_online = int(device_online or 0)
    device_stale = int(device_stale or 0)
    device_errors = int(device_errors or 0)

    ai_result = await db.execute(
        select(
            func.date(AIAction.created_at),
            func.count(AIAction.id),
            func.sum(case((AIAction.status == "failed", 1), else_=0)),
        )
        .where(AIAction.created_at >= start_datetime)
        .group_by(func.date(AIAction.created_at))
    )
    ai_by_date = {
        str(row[0]): {"total": int(row[1] or 0), "failed": int(row[2] or 0)} for row in ai_result
    }
    ai_action_total = sum(item["total"] for item in ai_by_date.values())
    ai_action_failed = sum(item["failed"] for item in ai_by_date.values())
    ai_success_rate = round(
        (ai_action_total - ai_action_failed) / max(ai_action_total, 1) * 100,
        1,
    )
    active_tenants = (
        await db.scalar(select(func.count(Tenant.id)).where(Tenant.status == "active")) or 0
    )
    active_subscriptions = (
        await db.scalar(select(func.count(Subscription.id)).where(Subscription.status == "active"))
        or 0
    )

    health_score = 100
    if not db_ok:
        health_score -= 50
    if device_total:
        health_score -= min(25, round(device_stale / device_total * 25))
    if ai_action_total:
        health_score -= min(25, round(ai_action_failed / ai_action_total * 25))
    health_score = max(0, health_score)
    status = "healthy" if health_score >= 85 else "warning" if health_score >= 60 else "critical"

    checks = [
        MonitoringCheck(
            key="database",
            name="数据库",
            status="normal" if db_ok else "critical",
            value="正常" if db_ok else "异常",
            detail="连接可用，统计查询响应正常" if db_ok else "数据库连接或查询失败",
        ),
        MonitoringCheck(
            key="devices",
            name="设备心跳",
            status="normal" if device_stale == 0 else "warning",
            value=f"{device_online}/{device_total} 在线",
            detail=f"{device_stale} 台超过 1 小时未上报，{device_errors} 台记录错误",
        ),
        MonitoringCheck(
            key="ai_actions",
            name="AI 任务",
            status="normal" if ai_action_failed == 0 else "warning",
            value=f"{ai_success_rate:.1f}% 成功",
            detail=f"统计期内 {ai_action_total} 个任务，失败 {ai_action_failed} 个",
        ),
        MonitoringCheck(
            key="subscriptions",
            name="订阅服务",
            status="normal",
            value=f"{active_subscriptions} 个活跃",
            detail=f"当前 {active_tenants} 个活跃租户",
        ),
    ]

    trend = []
    for offset in range(days):
        day = start_date + timedelta(days=offset)
        key = day.isoformat()
        ai = ai_by_date.get(key, {})
        trend.append(
            MonitoringTrendItem(
                date=key,
                api_calls=requests_by_date.get(key, 0),
                ai_actions=ai.get("total", 0),
                ai_failures=ai.get("failed", 0),
            )
        )

    return MonitoringOverview(
        status=status,
        health_score=health_score,
        refreshed_at=now.isoformat(),
        range_days=days,
        request_total=request_total,
        today_requests=requests_by_date.get(today.isoformat(), 0),
        average_daily_requests=round(request_total / days, 1),
        active_tenants=active_tenants,
        device_total=device_total,
        device_online=device_online,
        device_stale=device_stale,
        device_errors=device_errors,
        ai_action_total=ai_action_total,
        ai_action_failed=ai_action_failed,
        ai_success_rate=ai_success_rate,
        checks=checks,
        trend=trend,
    )


# ────────────────────────────────────────────────────────────
# Dead Letter Queue 管理
# ────────────────────────────────────────────────────────────


class DeadLetterItem(BaseModel):
    id: str
    merchant_id: str
    merchant_name: str | None
    idempotency_key: str | None
    event_type: str
    error_message: str
    retry_count: int
    max_retries: int
    next_retry_at: str | None
    status: str
    created_at: str | None
    resolved_at: str | None


class DeadLetterListResponse(BaseModel):
    items: list[DeadLetterItem]
    total: int
    page: int
    page_size: int


@router.get("/dead-letters")
async def list_dead_letters(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    status_filter: str | None = Query(None, alias="status"),
    merchant_id: str | None = Query(None),
    db: AsyncSession = Depends(get_db),
) -> DeadLetterListResponse:
    """列出死信事件，支持状态与商户筛选 + 分页。"""
    from app.models.dead_letter import DeadLetterEvent

    filters: list = []
    if status_filter:
        filters.append(DeadLetterEvent.status == status_filter)
    if merchant_id:
        try:
            from uuid import UUID

            muid = UUID(merchant_id)
            filters.append(DeadLetterEvent.merchant_id == muid)
        except ValueError:
            pass

    total = await db.scalar(
        select(func.count(DeadLetterEvent.id)).where(*filters)
    ) or 0

    query = (
        select(DeadLetterEvent)
        .where(*filters)
        .order_by(desc(DeadLetterEvent.created_at))
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    result = await db.execute(query)
    events = result.scalars().all()

    # 批量加载商户名称
    merchant_ids = list({e.merchant_id for e in events})
    merchant_names: dict = {}
    if merchant_ids:
        m_result = await db.execute(
            select(Merchant).where(Merchant.id.in_(merchant_ids))
        )
        merchant_names = {m.id: m.name for m in m_result.scalars().all()}

    items = [
        DeadLetterItem(
            id=str(e.id),
            merchant_id=str(e.merchant_id),
            merchant_name=merchant_names.get(e.merchant_id),
            idempotency_key=e.idempotency_key,
            event_type=e.event_type,
            error_message=e.error_message,
            retry_count=e.retry_count,
            max_retries=e.max_retries,
            next_retry_at=e.next_retry_at.isoformat() if e.next_retry_at else None,
            status=e.status,
            created_at=e.created_at.isoformat() if e.created_at else None,
            resolved_at=e.resolved_at.isoformat() if e.resolved_at else None,
        )
        for e in events
    ]

    return DeadLetterListResponse(
        items=items,
        total=total,
        page=page,
        page_size=page_size,
    )


@router.post("/dead-letters/{dead_letter_id}/retry")
async def retry_dead_letter(
    dead_letter_id: str,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """将死信事件标记为重试（重置计数，设置下次重试时间为现在）。"""
    from uuid import UUID

    from app.models.dead_letter import DeadLetterEvent

    dl_id = UUID(dead_letter_id)
    dead_letter = await db.get(DeadLetterEvent, dl_id)
    if not dead_letter:
        raise HTTPException(status_code=404, detail="死信事件不存在")

    dead_letter.retry_count = 0
    dead_letter.next_retry_at = datetime.now(UTC)
    dead_letter.status = "pending"
    await db.flush()

    return {"id": str(dead_letter.id), "status": dead_letter.status, "message": "已标记为重试"}


@router.post("/dead-letters/{dead_letter_id}/resolve")
async def resolve_dead_letter(
    dead_letter_id: str,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """将死信事件标记为已解决。"""
    from uuid import UUID

    from app.models.dead_letter import DeadLetterEvent

    dl_id = UUID(dead_letter_id)
    dead_letter = await db.get(DeadLetterEvent, dl_id)
    if not dead_letter:
        raise HTTPException(status_code=404, detail="死信事件不存在")

    dead_letter.status = "resolved"
    dead_letter.resolved_at = datetime.now(UTC)
    await db.flush()

    return {"id": str(dead_letter.id), "status": dead_letter.status, "message": "已标记为已解决"}


# ────────────────────────────────────────────────────────────
# 设备故障告警
# ────────────────────────────────────────────────────────────


class DeviceFaultItem(BaseModel):
    device_id: str
    device_name: str
    device_type: str
    severity: str  # alert / warning
    type: str
    detail: str
    last_heartbeat: str | None


class DeviceFaultListResponse(BaseModel):
    items: list[DeviceFaultItem]
    total: int
    alert: int
    warning: int


@router.get("/devices/faults")
async def list_device_faults(
    severity: str | None = Query(None, alias="severity"),
    tenant_id: str | None = Query(None, alias="tenant_id"),
    db: AsyncSession = Depends(get_db),
) -> DeviceFaultListResponse:
    """获取设备故障列表，支持按严重程度和租户筛选。"""
    from uuid import UUID

    from app.services.device_monitor import detect_device_faults

    faults = await detect_device_faults(db)

    # 若指定租户筛选，只保留该租户下设备的故障
    if tenant_id:
        try:
            tid = UUID(tenant_id)
        except ValueError:
            tid = None
        if tid:
            # 批量查询该租户下的所有商户
            t_result = await db.execute(
                select(Merchant.id).where(Merchant.tenant_id == tid)
            )
            merchant_ids = {row[0] for row in t_result.fetchall()}
            # 批量查询这些商户下的所有设备
            d_result = await db.execute(
                select(Device.id).where(Device.merchant_id.in_(merchant_ids))
            )
            device_ids = {str(row[0]) for row in d_result.fetchall()}
            faults = [f for f in faults if f["device_id"] in device_ids]

    # 按严重程度筛选
    if severity:
        faults = [f for f in faults if f["severity"] == severity]

    alert_count = sum(1 for f in faults if f["severity"] == "alert")
    warning_count = sum(1 for f in faults if f["severity"] == "warning")

    items = [
        DeviceFaultItem(
            device_id=f["device_id"],
            device_name=f["device_name"],
            device_type=f["device_type"],
            severity=f["severity"],
            type=f["type"],
            detail=f["detail"],
            last_heartbeat=f.get("last_heartbeat"),
        )
        for f in faults
    ]

    return DeviceFaultListResponse(
        items=items,
        total=len(items),
        alert=alert_count,
        warning=warning_count,
    )
