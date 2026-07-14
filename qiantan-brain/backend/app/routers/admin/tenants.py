"""管理后台租户管理 API — 接入 / 列表 / 详情 / 更新。"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, EmailStr, Field, field_validator
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.admin_permissions import (
    AUDIT_READ,
    TENANT_CREATE,
    TENANT_READ,
    TENANT_UPDATE,
    USAGE_READ,
    check_suspend_permission,
    require_admin_permission,
)
from app.core.admin_security import get_current_admin
from app.core.audit import log_action
from app.database import get_db
from app.models.merchant import Merchant
from app.models.admin_audit import AdminAuditLog
from app.models.ai_action import AIAction
from app.models.audit import AuditLog
from app.models.device import Device
from app.models.edge_event import EdgeEvent
from app.models.saas import Plan, PlatformAdmin, Subscription, Tenant, UsageRecord
from app.models.voice import VoiceLog
from app.services.state_machine import validate_tenant_transition


router = APIRouter(
    prefix="/api/admin/tenants",
    tags=["admin-tenants"],
)


class TenantListItem(BaseModel):
    id: str
    name: str
    slug: str
    status: str
    plan_code: str | None
    plan_name: str | None
    merchant_count: int
    contact_email: str | None
    created_at: datetime

    model_config = {"from_attributes": True}


class TenantDetail(BaseModel):
    id: str
    name: str
    slug: str
    status: str
    plan_id: str | None
    plan_code: str | None
    plan_name: str | None
    contact_email: str | None
    contact_phone: str | None
    trial_ends_at: datetime | None
    admin_notes: str | None
    created_at: datetime
    updated_at: datetime
    merchant_count: int
    subscription_status: str | None

    model_config = {"from_attributes": True}


class TenantUpdate(BaseModel):
    name: str | None = Field(None, min_length=2, max_length=100)
    status: str | None = None
    contact_email: EmailStr | None = None
    contact_phone: str | None = Field(None, max_length=30)
    admin_notes: str | None = Field(None, max_length=2000)
    plan_id: uuid.UUID | None = None


class PaginatedTenants(BaseModel):
    items: list[TenantListItem]
    total: int
    page: int
    page_size: int


# ── 设备与同步 ──────────────────────────────────────────────


class DeviceItem(BaseModel):
    model_config = {"protected_namespaces": ()}

    device_id: str
    device_name: str
    merchant_name: str
    online_status: bool
    last_heartbeat: datetime | None
    model_version: str | None
    sync_count: int


class TenantDevicesResponse(BaseModel):
    items: list[DeviceItem]
    total: int


# ── AI 使用统计 ─────────────────────────────────────────────


class AIUsageResponse(BaseModel):
    dates: list[str]
    vision_counts: list[int]
    voice_counts: list[int]
    advice_counts: list[int]


# ── 风险与审计 ──────────────────────────────────────────────


class RiskAuditResponse(BaseModel):
    merchant_count: int
    total_audit_events_last_30d: int
    abnormal_patterns: list[str]


@router.get("", response_model=PaginatedTenants)
async def list_tenants(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    status_filter: str | None = Query(None, alias="status"),
    search: str | None = Query(None),
    db: AsyncSession = Depends(get_db),
    _perm=Depends(require_admin_permission(TENANT_READ)),
):
    """分页查询租户列表。"""
    merchant_counts = (
        select(
            Merchant.tenant_id.label("tenant_id"),
            func.count(Merchant.id).label("merchant_count"),
        )
        .group_by(Merchant.tenant_id)
        .subquery()
    )
    base_query = (
        select(
            Tenant,
            func.coalesce(merchant_counts.c.merchant_count, 0),
            Plan.code,
            Plan.name,
        )
        .outerjoin(merchant_counts, merchant_counts.c.tenant_id == Tenant.id)
        .outerjoin(Plan, Plan.id == Tenant.plan_id)
    )
    count_query = select(func.count(Tenant.id))

    if status_filter:
        base_query = base_query.where(Tenant.status == status_filter)
        count_query = count_query.where(Tenant.status == status_filter)

    if search:
        pattern = f"%{search}%"
        base_query = base_query.where(
            (Tenant.name.ilike(pattern)) | (Tenant.slug.ilike(pattern))
        )
        count_query = count_query.where(
            (Tenant.name.ilike(pattern)) | (Tenant.slug.ilike(pattern))
        )

    total = await db.scalar(count_query) or 0

    base_query = (
        base_query.order_by(Tenant.created_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    result = await db.execute(base_query)
    rows = result.all()

    items = []
    for tenant, merchant_count, plan_code, plan_name in rows:
        items.append(
            TenantListItem(
                id=str(tenant.id),
                name=tenant.name,
                slug=tenant.slug,
                status=tenant.status,
                plan_code=plan_code,
                plan_name=plan_name,
                merchant_count=merchant_count,
                contact_email=tenant.contact_email,
                created_at=tenant.created_at,
            )
        )

    return PaginatedTenants(
        items=items,
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get("/{tenant_id}", response_model=TenantDetail)
async def get_tenant_detail(
    tenant_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _perm=Depends(require_admin_permission(TENANT_READ)),
):
    """获取租户详情。"""
    tenant = await db.get(Tenant, tenant_id)
    if tenant is None:
        raise HTTPException(status_code=404, detail="租户不存在")

    # 商户数
    merchant_count = await db.scalar(
        select(func.count(Merchant.id)).where(Merchant.tenant_id == tenant_id)
    )

    # 套餐信息
    plan_code = None
    plan_name = None
    if tenant.plan_id:
        plan = await db.get(Plan, tenant.plan_id)
        if plan:
            plan_code = plan.code
            plan_name = plan.name

    # 订阅状态
    sub_result = await db.execute(
        select(Subscription.status).where(
            Subscription.tenant_id == tenant_id,
            Subscription.status.in_(["active", "trialing"]),
        )
    )
    sub_status = sub_result.scalar_one_or_none()

    return TenantDetail(
        id=str(tenant.id),
        name=tenant.name,
        slug=tenant.slug,
        status=tenant.status,
        plan_id=str(tenant.plan_id) if tenant.plan_id else None,
        plan_code=plan_code,
        plan_name=plan_name,
        contact_email=tenant.contact_email,
        contact_phone=tenant.contact_phone,
        trial_ends_at=tenant.trial_ends_at,
        admin_notes=tenant.admin_notes,
        created_at=tenant.created_at,
        updated_at=tenant.updated_at,
        merchant_count=merchant_count or 0,
        subscription_status=sub_status,
    )


@router.put("/{tenant_id}", response_model=TenantDetail)
async def update_tenant(
    tenant_id: uuid.UUID,
    update: TenantUpdate,
    request: Request,
    db: AsyncSession = Depends(get_db),
    admin: PlatformAdmin = Depends(get_current_admin),
    _perm=Depends(require_admin_permission(TENANT_UPDATE)),
):
    """更新租户信息（状态、联系方式、备注、套餐）。"""
    tenant = await db.get(Tenant, tenant_id)
    if tenant is None:
        raise HTTPException(status_code=404, detail="租户不存在")

    old_status = tenant.status

    if update.name is not None:
        tenant.name = update.name
    if update.status is not None:
        valid = {"trial", "active", "suspended", "expired"}
        if update.status not in valid:
            raise HTTPException(
                status_code=400,
                detail=f"状态无效，允许: {', '.join(valid)}",
            )
        # 暂停/恢复租户需要 TENANT_SUSPEND 权限
        is_suspend_transition = (
            (update.status == "suspended" and tenant.status != "suspended") or
            (tenant.status == "suspended" and update.status != "suspended")
        )
        if is_suspend_transition:
            check_suspend_permission(admin)
        validate_tenant_transition(tenant.status, update.status, tenant.name)
        tenant.status = update.status
    if update.contact_email is not None:
        tenant.contact_email = update.contact_email
    if update.contact_phone is not None:
        tenant.contact_phone = update.contact_phone
    if update.admin_notes is not None:
        tenant.admin_notes = update.admin_notes
    if update.plan_id is not None:
        plan = await db.get(Plan, update.plan_id)
        if plan is None:
            raise HTTPException(status_code=400, detail="套餐不存在")
        tenant.plan_id = update.plan_id

    # 审计日志（与业务变更同事务）
    await log_action(
        db, admin.id, admin.email, "update",
        resource_type="tenant", resource_id=str(tenant_id),
        detail={
            "name": tenant.name,
            "status": f"{old_status} -> {tenant.status}",
            "plan_id": str(tenant.plan_id) if tenant.plan_id else None,
        },
        request=request,
    )
    await db.commit()
    await db.refresh(tenant)
    
    # 返回详情
    merchant_count = await db.scalar(
        select(func.count(Merchant.id)).where(Merchant.tenant_id == tenant_id)
    )
    plan_code = None
    plan_name = None
    if tenant.plan_id:
        plan = await db.get(Plan, tenant.plan_id)
        if plan:
            plan_code = plan.code
            plan_name = plan.name
    sub_result = await db.execute(
        select(Subscription.status).where(
            Subscription.tenant_id == tenant_id,
            Subscription.status.in_(["active", "trialing"]),
        )
    )
    sub_status = sub_result.scalar_one_or_none()

    return TenantDetail(
        id=str(tenant.id),
        name=tenant.name,
        slug=tenant.slug,
        status=tenant.status,
        plan_id=str(tenant.plan_id) if tenant.plan_id else None,
        plan_code=plan_code,
        plan_name=plan_name,
        contact_email=tenant.contact_email,
        contact_phone=tenant.contact_phone,
        trial_ends_at=tenant.trial_ends_at,
        admin_notes=tenant.admin_notes,
        created_at=tenant.created_at,
        updated_at=tenant.updated_at,
        merchant_count=merchant_count or 0,
        subscription_status=sub_status,
    )


# ── 设备与同步 ──────────────────────────────────────────────


@router.get("/{tenant_id}/devices", response_model=TenantDevicesResponse)
async def get_tenant_devices(
    tenant_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _perm=Depends(require_admin_permission(USAGE_READ)),
):
    """获取租户下所有设备及同步状态。"""
    # 获取该租户下所有商户的设备
    result = await db.execute(
        select(Device, Merchant.name)
        .join(Merchant, Device.merchant_id == Merchant.id)
        .where(Merchant.tenant_id == tenant_id)
    )
    rows = result.all()

    if not rows:
        return TenantDevicesResponse(items=[], total=0)

    device_ids = [row[0].id for row in rows]
    device_id_strs = [str(did) for did in device_ids]

    # 批量获取 sync_count（EdgeEvent 按 device_id 分组计数）
    sync_result = await db.execute(
        select(
            EdgeEvent.device_id,
            func.count(EdgeEvent.id).label("count"),
        )
        .where(EdgeEvent.device_id.in_(device_id_strs))
        .group_by(EdgeEvent.device_id)
    )
    sync_map: dict[str, int] = {row[0]: row[1] for row in sync_result.all()}

    # 批量获取最新 model_version
    mv_result = await db.execute(
        select(
            EdgeEvent.device_id,
            EdgeEvent.model_version,
        )
        .where(
            EdgeEvent.device_id.in_(device_id_strs),
            EdgeEvent.model_version.isnot(None),
        )
        .order_by(EdgeEvent.created_at.desc())
    )
    mv_map: dict[str, str] = {}
    for did, mv in mv_result.all():
        if did not in mv_map:
            mv_map[did] = mv

    now = datetime.now(UTC)
    items: list[DeviceItem] = []
    for device, merchant_name in rows:
        did_str = str(device.id)
        online = (
            device.last_heartbeat is not None
            and (now - device.last_heartbeat).total_seconds() < 3600
        )
        items.append(
            DeviceItem(
                device_id=did_str,
                device_name=device.device_name,
                merchant_name=merchant_name,
                online_status=online,
                last_heartbeat=device.last_heartbeat,
                model_version=mv_map.get(did_str) or device.firmware_version,
                sync_count=sync_map.get(did_str, 0),
            )
        )

    return TenantDevicesResponse(items=items, total=len(items))


# ── AI 使用统计 ─────────────────────────────────────────────


@router.get("/{tenant_id}/ai-usage", response_model=AIUsageResponse)
async def get_tenant_ai_usage(
    tenant_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _perm=Depends(require_admin_permission(USAGE_READ)),
):
    """获取租户近 30 天 AI 使用统计（视觉识别、语音识别、AI 建议）。"""
    start_dt = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=29)

    # 获取该租户的所有商户 ID
    mid_result = await db.execute(
        select(Merchant.id).where(Merchant.tenant_id == tenant_id)
    )
    merchant_ids = [row[0] for row in mid_result.all()]

    if not merchant_ids:
        # 生成空日期序列
        all_dates = [
            (start_dt + timedelta(days=i)).strftime("%Y-%m-%d")
            for i in range(30)
        ]
        return AIUsageResponse(
            dates=all_dates,
            vision_counts=[0] * 30,
            voice_counts=[0] * 30,
            advice_counts=[0] * 30,
        )

    # 视觉检测事件（EdgeEvent event_type=vision）
    vision_rows = await db.execute(
        select(
            func.date(EdgeEvent.occurred_at).label("date"),
            func.count(EdgeEvent.id).label("count"),
        )
        .where(
            EdgeEvent.tenant_id == tenant_id,
            EdgeEvent.event_type == "vision",
            EdgeEvent.occurred_at >= start_dt,
        )
        .group_by(func.date(EdgeEvent.occurred_at))
        .order_by("date")
    )
    vision_map: dict[str, int] = {row[0]: row[1] for row in vision_rows.all()}

    # 语音识别日志（VoiceLog → merchant_id IN tenant's merchants）
    voice_rows = await db.execute(
        select(
            func.date(VoiceLog.created_at).label("date"),
            func.count(VoiceLog.id).label("count"),
        )
        .where(
            VoiceLog.merchant_id.in_(merchant_ids),
            VoiceLog.created_at >= start_dt,
        )
        .group_by(func.date(VoiceLog.created_at))
        .order_by("date")
    )
    voice_map: dict[str, int] = {row[0]: row[1] for row in voice_rows.all()}

    # AI 建议动作（AIAction → merchant_id IN tenant's merchants）
    advice_rows = await db.execute(
        select(
            func.date(AIAction.created_at).label("date"),
            func.count(AIAction.id).label("count"),
        )
        .where(
            AIAction.merchant_id.in_(merchant_ids),
            AIAction.created_at >= start_dt,
        )
        .group_by(func.date(AIAction.created_at))
        .order_by("date")
    )
    advice_map: dict[str, int] = {row[0]: row[1] for row in advice_rows.all()}

    # 生成 30 天日期序列
    all_dates: list[str] = []
    for i in range(30):
        d = start_dt + timedelta(days=i)
        all_dates.append(d.strftime("%Y-%m-%d"))

    return AIUsageResponse(
        dates=all_dates,
        vision_counts=[vision_map.get(d, 0) for d in all_dates],
        voice_counts=[voice_map.get(d, 0) for d in all_dates],
        advice_counts=[advice_map.get(d, 0) for d in all_dates],
    )


# ── 风险与审计 ──────────────────────────────────────────────


@router.get("/{tenant_id}/risk-audit", response_model=RiskAuditResponse)
async def get_tenant_risk_audit(
    tenant_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _perm=Depends(require_admin_permission(AUDIT_READ)),
):
    """获取租户安全/风险概览。"""
    thirty_days_ago = datetime.now(UTC) - timedelta(days=30)

    # 商户数
    merchant_count = await db.scalar(
        select(func.count(Merchant.id)).where(Merchant.tenant_id == tenant_id)
    ) or 0

    # 商户审计日志（通过 merchant_id → tenant_id 过滤）
    audit_count = await db.scalar(
        select(func.count(AuditLog.id))
        .join(Merchant, AuditLog.merchant_id == Merchant.id)
        .where(
            Merchant.tenant_id == tenant_id,
            AuditLog.created_at >= thirty_days_ago,
        )
    ) or 0

    # 管理员操作日志（针对此租户的操作）
    admin_audit_count = await db.scalar(
        select(func.count(AdminAuditLog.id)).where(
            AdminAuditLog.resource_type == "tenant",
            AdminAuditLog.resource_id == str(tenant_id),
            AdminAuditLog.created_at >= thirty_days_ago,
        )
    ) or 0

    return RiskAuditResponse(
        merchant_count=merchant_count,
        total_audit_events_last_30d=audit_count + admin_audit_count,
        abnormal_patterns=[],
    )


# ── 租户接入流程 ──────────────────────────────────────────────


class TenantCreate(BaseModel):
    """创建租户请求。"""
    name: str = Field(..., min_length=2, max_length=100, description="租户名称")
    slug: str = Field(
        ...,
        min_length=2,
        max_length=60,
        pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$",
        description="URL 友好标识，唯一",
    )
    plan_id: uuid.UUID = Field(..., description="初始套餐 ID")
    contact_email: EmailStr | None = None
    contact_phone: str | None = Field(None, max_length=30)
    merchant_name: str = Field(
        ...,
        min_length=2,
        max_length=100,
        description="首个商户（摊主）名称",
    )
    admin_notes: str | None = Field(None, max_length=2000)
    trial_days: int = Field(14, ge=1, le=90, description="试用期天数")

    @field_validator("name", "merchant_name")
    @classmethod
    def strip_required_names(cls, value: str) -> str:
        value = value.strip()
        if len(value) < 2:
            raise ValueError("名称至少需要 2 个字符")
        return value

    @field_validator("slug", mode="before")
    @classmethod
    def normalize_slug(cls, value: object) -> object:
        return value.strip().lower() if isinstance(value, str) else value


class TenantOnboardResult(BaseModel):
    """租户接入结果。"""
    tenant_id: uuid.UUID
    tenant_name: str
    slug: str
    status: str
    plan_code: str
    plan_name: str
    trial_ends_at: datetime | None
    subscription_id: uuid.UUID
    subscription_status: str
    merchant_id: uuid.UUID
    merchant_name: str
    created_at: datetime


@router.post("", response_model=TenantOnboardResult, status_code=status.HTTP_201_CREATED)
async def create_tenant(
    req: TenantCreate,
    request: Request,
    db: AsyncSession = Depends(get_db),
    admin: PlatformAdmin = Depends(get_current_admin),
    _perm=Depends(require_admin_permission(TENANT_CREATE)),
):
    """租户接入流程 — 一步完成注册 + 试用订阅 + 初始化首个商户。

    流程：
      1. 校验 slug 唯一性 + 套餐有效性
      2. 创建 Tenant（status=trial, trial_ends_at=now+trial_days）
      3. 创建 Subscription（status=trialing, plan_id=指定套餐）
      4. 创建首个 Merchant（role=owner, tenant_id=绑定）
      5. 初始化当天 UsageRecord（api_calls=0, storage_mb=0, merchant_count=1）
    """
    # 1. 校验 slug 唯一
    existing = await db.execute(
        select(Tenant.id).where(func.lower(Tenant.slug) == req.slug)
    )
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=409, detail=f"slug '{req.slug}' 已存在")

    # 2. 校验套餐
    plan = await db.get(Plan, req.plan_id)
    if plan is None:
        raise HTTPException(status_code=404, detail="套餐不存在")
    if not plan.is_active:
        raise HTTPException(status_code=400, detail="套餐已停用，无法用于接入")

    now = datetime.now(UTC)
    trial_end = now + timedelta(days=req.trial_days)

    # 3. 创建 Tenant
    tenant = Tenant(
        name=req.name,
        slug=req.slug,
        plan_id=req.plan_id,
        status="trial",
        contact_email=req.contact_email,
        contact_phone=req.contact_phone,
        trial_ends_at=trial_end,
        admin_notes=req.admin_notes,
    )
    db.add(tenant)
    await db.flush()  # 获取 tenant.id

    # 4. 创建试用 Subscription
    sub = Subscription(
        tenant_id=tenant.id,
        plan_id=req.plan_id,
        billing_cycle="monthly",
        status="trialing",
        current_period_start=now,
        current_period_end=trial_end,
        auto_renew=True,
    )
    db.add(sub)

    # 5. 创建首个 Merchant
    merchant = Merchant(
        name=req.merchant_name,
        tenant_id=tenant.id,
        role="owner",
    )
    db.add(merchant)

    # 6. 初始化当天 UsageRecord
    today = now.strftime("%Y-%m-%d")
    for metric, val in [("api_calls", 0), ("storage_mb", 0), ("merchant_count", 1)]:
        db.add(UsageRecord(
            tenant_id=tenant.id,
            metric=metric,
            recorded_date=today,
            value=val,
        ))

    # 审计日志（与业务创建同事务）
    await log_action(
        db, admin.id, admin.email, "create",
        resource_type="tenant", resource_id=str(tenant.id),
        detail={
            "tenant_name": tenant.name,
            "slug": tenant.slug,
            "plan": plan.code,
            "trial_days": req.trial_days,
            "merchant_name": merchant.name,
        },
        request=request,
    )
    await db.commit()
    await db.refresh(tenant)
    await db.refresh(sub)
    await db.refresh(merchant)

    return TenantOnboardResult(
        tenant_id=tenant.id,
        tenant_name=tenant.name,
        slug=tenant.slug,
        status=tenant.status,
        plan_code=plan.code,
        plan_name=plan.name,
        trial_ends_at=tenant.trial_ends_at,
        subscription_id=sub.id,
        subscription_status=sub.status,
        merchant_id=merchant.id,
        merchant_name=merchant.name,
        created_at=tenant.created_at,
    )
