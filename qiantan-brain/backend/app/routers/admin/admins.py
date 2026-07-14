"""平台管理员管理 API — CRUD + 角色分配（仅 super_admin）。"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.admin_permissions import ADMIN_MANAGE, require_admin_permission
from app.core.admin_security import get_current_admin, hash_password
from app.core.audit import log_action
from app.database import get_db
from app.models.saas import PlatformAdmin


router = APIRouter(prefix="/api/admin/admins", tags=["admin-admins"])

ROLE_CHOICES = ["super_admin", "ops_admin", "billing_admin", "support_admin", "auditor"]


class AdminInfo(BaseModel):
    id: uuid.UUID
    email: str
    name: str
    role: str
    is_active: bool
    last_login_at: str | None = None
    created_at: str | None = None
    model_config = {"from_attributes": True}


class AdminCreate(BaseModel):
    email: EmailStr
    password: str = Field(..., min_length=8, max_length=64)
    name: str = Field(..., max_length=60)
    role: str = Field(default="ops_admin")


class AdminUpdate(BaseModel):
    name: str | None = None
    role: str | None = None
    is_active: bool | None = None
    password: str | None = Field(None, min_length=8, max_length=64)


def _serialize(admin: PlatformAdmin) -> AdminInfo:
    return AdminInfo(
        id=admin.id,
        email=admin.email,
        name=admin.name,
        role=admin.role,
        is_active=admin.is_active,
        last_login_at=admin.last_login_at.isoformat() if admin.last_login_at else None,
        created_at=admin.created_at.isoformat() if admin.created_at else None,
    )


@router.get("", response_model=list[AdminInfo])
async def list_admins(
    db: AsyncSession = Depends(get_db),
    _perm=Depends(require_admin_permission(ADMIN_MANAGE)),
):
    result = await db.execute(select(PlatformAdmin).order_by(PlatformAdmin.created_at.desc()))
    return [_serialize(a) for a in result.scalars().all()]


@router.post("", response_model=AdminInfo, status_code=status.HTTP_201_CREATED)
async def create_admin(
    req: AdminCreate,
    request: Request,
    db: AsyncSession = Depends(get_db),
    admin: PlatformAdmin = Depends(get_current_admin),
    _perm=Depends(require_admin_permission(ADMIN_MANAGE)),
):
    if req.role not in ROLE_CHOICES:
        raise HTTPException(status_code=400, detail=f"角色无效，允许: {', '.join(ROLE_CHOICES)}")

    existing = await db.execute(select(PlatformAdmin).where(PlatformAdmin.email == req.email))
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=409, detail=f"邮箱 {req.email} 已被注册")

    new_admin = PlatformAdmin(
        email=req.email,
        name=req.name,
        role=req.role,
        password_hash=hash_password(req.password),
        is_active=True,
    )
    db.add(new_admin)

    await log_action(
        db,
        admin.id,
        admin.email,
        "create_admin",
        resource_type="platform_admin",
        resource_id=str(new_admin.id),
        detail={"email": new_admin.email, "role": new_admin.role},
        request=request,
    )
    await db.commit()
    await db.refresh(new_admin)

    return _serialize(new_admin)


@router.put("/{admin_id}", response_model=AdminInfo)
async def update_admin(
    admin_id: uuid.UUID,
    req: AdminUpdate,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_admin: PlatformAdmin = Depends(get_current_admin),
    _perm=Depends(require_admin_permission(ADMIN_MANAGE)),
):
    target = await db.get(PlatformAdmin, admin_id)
    if target is None:
        raise HTTPException(status_code=404, detail="管理员不存在")

    changes = {}
    if req.name is not None:
        target.name = req.name
        changes["name"] = req.name
    if req.role is not None:
        if req.role not in ROLE_CHOICES:
            raise HTTPException(status_code=400, detail="角色无效")
        changes["role"] = f"{target.role} -> {req.role}"
        target.role = req.role
    if req.is_active is not None:
        changes["is_active"] = f"{target.is_active} -> {req.is_active}"
        target.is_active = req.is_active
    if req.password is not None:
        target.password_hash = hash_password(req.password)
        changes["password"] = "***"

    await log_action(
        db,
        current_admin.id,
        current_admin.email,
        "update_admin",
        resource_type="platform_admin",
        resource_id=str(target.id),
        detail=changes,
        request=request,
    )
    await db.commit()
    await db.refresh(target)

    return _serialize(target)
