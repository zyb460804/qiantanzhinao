"""管理后台套餐管理 API — CRUD。"""

from __future__ import annotations

import uuid
from decimal import Decimal
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.admin_permissions import (
    PLAN_CREATE,
    PLAN_DELETE,
    PLAN_READ,
    PLAN_UPDATE,
    require_admin_permission,
)
from app.core.admin_security import get_current_admin
from app.core.audit import log_action
from app.database import get_db
from app.models.saas import Plan, PlatformAdmin


router = APIRouter(prefix="/api/admin/plans", tags=["admin-plans"])


class PlanBase(BaseModel):
    code: str = Field(..., max_length=30)
    name: str = Field(..., max_length=60)
    price_monthly: Decimal = Decimal("0")
    price_yearly: Decimal = Decimal("0")
    max_merchants: int = 1
    max_api_calls_monthly: int = 1000
    max_storage_mb: int = 100
    features: dict[str, Any] | None = None
    is_public: bool = True
    is_active: bool = True
    sort_order: int = 0


class PlanCreate(PlanBase):
    pass


class PlanUpdate(BaseModel):
    name: str | None = Field(None, max_length=60)
    price_monthly: Decimal | None = None
    price_yearly: Decimal | None = None
    max_merchants: int | None = None
    max_api_calls_monthly: int | None = None
    max_storage_mb: int | None = None
    features: dict[str, Any] | None = None
    is_public: bool | None = None
    is_active: bool | None = None
    sort_order: int | None = None


class PlanResponse(PlanBase):
    id: uuid.UUID

    model_config = {"from_attributes": True}


@router.get("", response_model=list[PlanResponse])
async def list_plans(
    db: AsyncSession = Depends(get_db),
    _perm=Depends(require_admin_permission(PLAN_READ)),
):
    """获取所有套餐列表。"""
    result = await db.execute(select(Plan).order_by(Plan.sort_order, Plan.created_at))
    return result.scalars().all()


@router.post("", response_model=PlanResponse, status_code=status.HTTP_201_CREATED)
async def create_plan(
    req: PlanCreate,
    request: Request,
    db: AsyncSession = Depends(get_db),
    admin: PlatformAdmin = Depends(get_current_admin),
    _perm=Depends(require_admin_permission(PLAN_CREATE)),
):
    """创建套餐（仅 billing_admin/super_admin）。"""
    existing = await db.execute(select(Plan).where(Plan.code == req.code))
    if existing.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=f"套餐代码 {req.code} 已存在"
        )

    plan = Plan(
        id=uuid.uuid4(),
        code=req.code,
        name=req.name,
        price_monthly=req.price_monthly,
        price_yearly=req.price_yearly,
        max_merchants=req.max_merchants,
        max_api_calls_monthly=req.max_api_calls_monthly,
        max_storage_mb=req.max_storage_mb,
        features=req.features,
        is_public=req.is_public,
        is_active=req.is_active,
        sort_order=req.sort_order,
    )
    db.add(plan)

    await log_action(
        db,
        admin.id,
        admin.email,
        "create",
        resource_type="plan",
        resource_id=str(plan.id),
        detail={"code": plan.code, "name": plan.name},
        request=request,
    )
    await db.commit()
    await db.refresh(plan)
    return plan


@router.put("/{plan_id}", response_model=PlanResponse)
async def update_plan(
    plan_id: str,
    req: PlanUpdate,
    request: Request,
    db: AsyncSession = Depends(get_db),
    admin: PlatformAdmin = Depends(get_current_admin),
    _perm=Depends(require_admin_permission(PLAN_UPDATE)),
):
    """更新套餐信息（仅 billing_admin/super_admin）。"""
    try:
        pid = uuid.UUID(plan_id)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="无效的套餐 ID"
        ) from exc

    plan = await db.get(Plan, pid)
    if plan is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="套餐不存在")

    old_values = {
        "name": plan.name,
        "is_active": plan.is_active,
        "price_monthly": str(plan.price_monthly),
    }
    update_data = req.model_dump(exclude_unset=True)
    for key, value in update_data.items():
        setattr(plan, key, value)

    await log_action(
        db,
        admin.id,
        admin.email,
        "update",
        resource_type="plan",
        resource_id=str(plan.id),
        detail={"code": plan.code, "before": old_values, "after": update_data},
        request=request,
    )
    await db.commit()
    await db.refresh(plan)
    return plan


@router.delete("/{plan_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_plan(
    plan_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    admin: PlatformAdmin = Depends(get_current_admin),
    _perm=Depends(require_admin_permission(PLAN_DELETE)),
):
    """删除套餐 — 软删除，标记 is_active=False（仅 billing_admin/super_admin）。"""
    try:
        pid = uuid.UUID(plan_id)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="无效的套餐 ID"
        ) from exc

    plan = await db.get(Plan, pid)
    if plan is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="套餐不存在")

    plan.is_active = False

    await log_action(
        db,
        admin.id,
        admin.email,
        "delete",
        resource_type="plan",
        resource_id=str(plan.id),
        detail={"code": plan.code, "name": plan.name, "action": "soft_delete"},
        request=request,
    )
    await db.commit()
