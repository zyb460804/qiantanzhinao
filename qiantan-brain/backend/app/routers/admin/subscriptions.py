"""管理后台订阅管理 API — 列表/详情/创建/升级/取消。"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.admin_permissions import (
    SUBSCRIPTION_CHANGE,
    SUBSCRIPTION_CREATE,
    SUBSCRIPTION_READ,
    require_admin_permission,
)
from app.core.admin_security import get_current_admin
from app.core.audit import log_action
from app.database import get_db
from app.models.saas import Plan, PlatformAdmin, Subscription, Tenant
from app.services.state_machine import validate_subscription_transition


router = APIRouter(
    prefix="/api/admin/subscriptions",
    tags=["admin-subscriptions"],
)


class SubscriptionInfo(BaseModel):
    id: uuid.UUID
    tenant_id: uuid.UUID
    tenant_name: str
    plan_id: uuid.UUID
    plan_code: str
    plan_name: str
    billing_cycle: str
    status: str
    current_period_start: datetime | None
    current_period_end: datetime | None
    canceled_at: datetime | None
    auto_renew: bool
    created_at: datetime

    model_config = {"from_attributes": True}


class PaginatedSubscriptions(BaseModel):
    items: list[SubscriptionInfo]
    total: int
    page: int
    page_size: int


class SubscriptionCreate(BaseModel):
    tenant_id: uuid.UUID
    plan_id: uuid.UUID
    billing_cycle: str = "monthly"
    auto_renew: bool = True


class SubscriptionUpdate(BaseModel):
    plan_id: uuid.UUID | None = None
    billing_cycle: str | None = None
    auto_renew: bool | None = None


def _build_info(sub: Subscription, tenant: Tenant | None, plan: Plan | None) -> SubscriptionInfo:
    return SubscriptionInfo(
        id=sub.id,
        tenant_id=sub.tenant_id,
        tenant_name=tenant.name if tenant else "N/A",
        plan_id=sub.plan_id,
        plan_code=plan.code if plan else "N/A",
        plan_name=plan.name if plan else "N/A",
        billing_cycle=sub.billing_cycle,
        status=sub.status,
        current_period_start=sub.current_period_start,
        current_period_end=sub.current_period_end,
        canceled_at=sub.canceled_at,
        auto_renew=sub.auto_renew,
        created_at=sub.created_at,
    )


@router.get("", response_model=PaginatedSubscriptions)
async def list_subscriptions(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    status_filter: str | None = Query(None, alias="status"),
    tenant_id: uuid.UUID | None = Query(None),
    db: AsyncSession = Depends(get_db),
    _perm=Depends(require_admin_permission(SUBSCRIPTION_READ)),
):
    """分页查询订阅列表。"""
    base_query = (
        select(Subscription, Tenant, Plan)
        .outerjoin(Tenant, Tenant.id == Subscription.tenant_id)
        .outerjoin(Plan, Plan.id == Subscription.plan_id)
    )
    count_query = select(func.count(Subscription.id))

    if status_filter:
        base_query = base_query.where(Subscription.status == status_filter)
        count_query = count_query.where(Subscription.status == status_filter)

    if tenant_id:
        base_query = base_query.where(Subscription.tenant_id == tenant_id)
        count_query = count_query.where(Subscription.tenant_id == tenant_id)

    total = await db.scalar(count_query) or 0

    base_query = (
        base_query.order_by(Subscription.created_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    result = await db.execute(base_query)
    rows = result.all()
    items = [_build_info(sub, tenant, plan) for sub, tenant, plan in rows]

    return PaginatedSubscriptions(items=items, total=total, page=page, page_size=page_size)


@router.get("/{sub_id}", response_model=SubscriptionInfo)
async def get_subscription_detail(
    sub_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _perm=Depends(require_admin_permission(SUBSCRIPTION_READ)),
):
    """获取订阅详情。"""
    sub = await db.get(Subscription, sub_id)
    if sub is None:
        raise HTTPException(status_code=404, detail="订阅不存在")
    tenant = await db.get(Tenant, sub.tenant_id)
    plan = await db.get(Plan, sub.plan_id)
    return _build_info(sub, tenant, plan)


@router.post("", response_model=SubscriptionInfo, status_code=status.HTTP_201_CREATED)
async def create_subscription(
    req: SubscriptionCreate,
    request: Request,
    db: AsyncSession = Depends(get_db),
    admin: PlatformAdmin = Depends(get_current_admin),
    _perm=Depends(require_admin_permission(SUBSCRIPTION_CREATE)),
):
    """创建订阅（仅 billing_admin/super_admin）。"""
    tenant = await db.get(Tenant, req.tenant_id)
    if tenant is None:
        raise HTTPException(status_code=404, detail="租户不存在")
    plan = await db.get(Plan, req.plan_id)
    if plan is None:
        raise HTTPException(status_code=404, detail="套餐不存在")
    if not plan.is_active:
        raise HTTPException(status_code=400, detail="套餐已停用")
    if req.billing_cycle not in ("monthly", "yearly"):
        raise HTTPException(status_code=400, detail="计费周期须为 monthly 或 yearly")

    existing = await db.scalar(
        select(Subscription.id).where(
            Subscription.tenant_id == req.tenant_id,
            Subscription.status.in_(["trialing", "active", "past_due"]),
        )
    )
    if existing is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="该租户已有有效订阅，请升级、激活或取消现有订阅",
        )

    now = datetime.now(UTC)
    period_end = now + (
        timedelta(days=365) if req.billing_cycle == "yearly" else timedelta(days=30)
    )

    sub = Subscription(
        tenant_id=req.tenant_id,
        plan_id=req.plan_id,
        billing_cycle=req.billing_cycle,
        status="active",
        current_period_start=now,
        current_period_end=period_end,
        auto_renew=req.auto_renew,
    )
    db.add(sub)
    tenant.plan_id = req.plan_id
    if tenant.status == "trial":
        tenant.status = "active"

    await log_action(
        db,
        admin.id,
        admin.email,
        "create",
        resource_type="subscription",
        resource_id=str(sub.id),
        detail={"tenant": tenant.name, "plan": plan.code, "cycle": req.billing_cycle},
        request=request,
    )
    await db.commit()
    await db.refresh(sub)
    return _build_info(sub, tenant, plan)


@router.put("/{sub_id}", response_model=SubscriptionInfo)
async def update_subscription(
    sub_id: uuid.UUID,
    update: SubscriptionUpdate,
    request: Request,
    db: AsyncSession = Depends(get_db),
    admin: PlatformAdmin = Depends(get_current_admin),
    _perm=Depends(require_admin_permission(SUBSCRIPTION_CHANGE)),
):
    """更新订阅 — 升级/降级套餐或变更计费周期。"""
    sub = await db.get(Subscription, sub_id)
    if sub is None:
        raise HTTPException(status_code=404, detail="订阅不存在")

    old_plan_id = sub.plan_id
    old_cycle = sub.billing_cycle

    if update.plan_id is not None:
        plan = await db.get(Plan, update.plan_id)
        if plan is None:
            raise HTTPException(status_code=404, detail="套餐不存在")
        if not plan.is_active:
            raise HTTPException(status_code=400, detail="套餐已停用")
        sub.previous_plan_id = sub.plan_id
        sub.plan_id = update.plan_id
        tenant = await db.get(Tenant, sub.tenant_id)
        if tenant:
            tenant.plan_id = update.plan_id

    if update.billing_cycle is not None:
        if update.billing_cycle not in ("monthly", "yearly"):
            raise HTTPException(status_code=400, detail="计费周期须为 monthly 或 yearly")
        sub.billing_cycle = update.billing_cycle

    if update.auto_renew is not None:
        sub.auto_renew = update.auto_renew

    await log_action(
        db,
        admin.id,
        admin.email,
        "update",
        resource_type="subscription",
        resource_id=str(sub.id),
        detail={
            "plan": f"{old_plan_id} -> {sub.plan_id}",
            "cycle": f"{old_cycle} -> {sub.billing_cycle}",
        },
        request=request,
    )
    await db.commit()
    await db.refresh(sub)

    tenant = await db.get(Tenant, sub.tenant_id)
    plan = await db.get(Plan, sub.plan_id)
    return _build_info(sub, tenant, plan)


@router.post("/{sub_id}/cancel")
async def cancel_subscription(
    sub_id: uuid.UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
    admin: PlatformAdmin = Depends(get_current_admin),
    _perm=Depends(require_admin_permission(SUBSCRIPTION_CHANGE)),
    reason: str | None = Body(None),
):
    """取消订阅。"""
    sub = await db.get(Subscription, sub_id)
    if sub is None:
        raise HTTPException(status_code=404, detail="订阅不存在")

    validate_subscription_transition(sub.status, "canceled")
    old_status = sub.status
    sub.status = "canceled"
    sub.canceled_at = datetime.now(UTC)
    sub.auto_renew = False

    await log_action(
        db,
        admin.id,
        admin.email,
        "cancel",
        resource_type="subscription",
        resource_id=str(sub.id),
        detail={"previous_status": old_status, "reason": reason},
        request=request,
    )
    await db.commit()
    return {
        "message": "订阅已取消",
        "current_period_end": sub.current_period_end.isoformat()
        if sub.current_period_end
        else None,
    }


@router.post("/{sub_id}/activate")
async def activate_subscription(
    sub_id: uuid.UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
    admin: PlatformAdmin = Depends(get_current_admin),
    _perm=Depends(require_admin_permission(SUBSCRIPTION_CHANGE)),
):
    """激活订阅（如从试用转正式）。"""
    sub = await db.get(Subscription, sub_id)
    if sub is None:
        raise HTTPException(status_code=404, detail="订阅不存在")

    validate_subscription_transition(sub.status, "active")
    conflicting = await db.scalar(
        select(Subscription.id).where(
            Subscription.tenant_id == sub.tenant_id,
            Subscription.id != sub.id,
            Subscription.status == "active",
        )
    )
    if conflicting is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="该租户已有活跃订阅",
        )
    old_status = sub.status
    now = datetime.now(UTC)
    sub.status = "active"
    sub.canceled_at = None
    sub.current_period_start = now
    sub.current_period_end = now + (
        timedelta(days=365) if sub.billing_cycle == "yearly" else timedelta(days=30)
    )
    tenant = await db.get(Tenant, sub.tenant_id)
    if tenant:
        tenant.status = "active"

    await log_action(
        db,
        admin.id,
        admin.email,
        "activate",
        resource_type="subscription",
        resource_id=str(sub.id),
        detail={"previous_status": old_status},
        request=request,
    )
    await db.commit()
    return {"message": "订阅已激活", "status": "active"}
