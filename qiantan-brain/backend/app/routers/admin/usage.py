"""管理后台用量监控 API — 配额状态/趋势/手动记录。"""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.admin_permissions import (
    USAGE_ADJUST,
    USAGE_READ,
    require_admin_permission,
)
from app.core.admin_security import get_current_admin
from app.core.audit import log_action
from app.core.quota import (
    check_quota,
    get_all_quotas,
    get_current_usage,
    get_tenant_plan,
    get_usage_trend,
    record_usage,
)
from app.database import get_db
from app.models.saas import PlatformAdmin, Tenant


router = APIRouter(prefix="/api/admin/usage", tags=["admin-usage"])


class QuotaStatus(BaseModel):
    metric: str
    current: int
    limit: int
    remaining: int
    exceeded: bool


class UsageTrendItem(BaseModel):
    date: str
    value: int


class RecordUsageRequest(BaseModel):
    metric: str
    value: int = 1


@router.get("/{tenant_id}/quotas", response_model=list[QuotaStatus])
async def get_tenant_quotas(
    tenant_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _perm=Depends(require_admin_permission(USAGE_READ)),
):
    tenant = await db.get(Tenant, tenant_id)
    if tenant is None:
        raise HTTPException(status_code=404, detail="租户不存在")
    return await get_all_quotas(db, tenant_id)


@router.get("/{tenant_id}/current/{metric}")
async def get_tenant_current_usage(
    tenant_id: uuid.UUID,
    metric: str,
    db: AsyncSession = Depends(get_db),
    _perm=Depends(require_admin_permission(USAGE_READ)),
):
    tenant = await db.get(Tenant, tenant_id)
    if tenant is None:
        raise HTTPException(status_code=404, detail="租户不存在")
    current = await get_current_usage(db, tenant_id, metric)
    quota = await check_quota(db, tenant_id, metric)
    return {
        "metric": metric,
        "current": current,
        "limit": quota["limit"],
        "remaining": quota["remaining"],
        "exceeded": quota["exceeded"],
    }


@router.get("/{tenant_id}/trend/{metric}", response_model=list[UsageTrendItem])
async def get_trend(
    tenant_id: uuid.UUID,
    metric: str,
    days: int = Query(30, ge=1, le=365),
    db: AsyncSession = Depends(get_db),
    _perm=Depends(require_admin_permission(USAGE_READ)),
):
    tenant = await db.get(Tenant, tenant_id)
    if tenant is None:
        raise HTTPException(status_code=404, detail="租户不存在")
    return await get_usage_trend(db, tenant_id, metric, days)


@router.post("/{tenant_id}/record")
async def manual_record_usage(
    tenant_id: uuid.UUID,
    req: RecordUsageRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    admin: PlatformAdmin = Depends(get_current_admin),
    _perm=Depends(require_admin_permission(USAGE_ADJUST)),
):
    """人工记账 — 高风险操作，必须审计，仅 billing_admin/super_admin。"""
    tenant = await db.get(Tenant, tenant_id)
    if tenant is None:
        raise HTTPException(status_code=404, detail="租户不存在")

    valid_metrics = {"api_calls", "storage_mb", "merchant_count", "voice_seconds"}
    if req.metric not in valid_metrics:
        raise HTTPException(
            status_code=400, detail=f"指标无效，允许: {', '.join(sorted(valid_metrics))}"
        )

    current_before = await get_current_usage(db, tenant_id, req.metric)
    await record_usage(db, tenant_id, req.metric, req.value)
    current_after = await get_current_usage(db, tenant_id, req.metric)
    quota = await check_quota(db, tenant_id, req.metric)

    # 高风险操作审计
    await log_action(
        db,
        admin.id,
        admin.email,
        "manual_record_usage",
        resource_type="usage",
        resource_id=str(tenant_id),
        detail={
            "tenant": tenant.name,
            "metric": req.metric,
            "added": req.value,
            "before": current_before,
            "after": current_after,
        },
        request=request,
    )
    await db.commit()

    result: dict[str, Any] = {
        "message": "用量已记录",
        "metric": req.metric,
        "added": req.value,
    }
    if quota["exceeded"]:
        result["warning"] = f"配额已超出！当前 {quota['current']}/{quota['limit']}"
    return result


@router.get("/{tenant_id}/overview")
async def get_usage_overview(
    tenant_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _perm=Depends(require_admin_permission(USAGE_READ)),
):
    tenant = await db.get(Tenant, tenant_id)
    if tenant is None:
        raise HTTPException(status_code=404, detail="租户不存在")
    plan = await get_tenant_plan(db, tenant_id)
    quotas = await get_all_quotas(db, tenant_id)
    return {
        "tenant_id": str(tenant_id),
        "tenant_name": tenant.name,
        "plan_code": plan.code if plan else None,
        "plan_name": plan.name if plan else None,
        "quotas": quotas,
    }
