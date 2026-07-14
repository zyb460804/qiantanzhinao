"""员工管理与权限 API (section 4.17).

提供：
- 角色/权限定义查询
- 员工 CRUD
- require_permission 依赖：路由级权限执行
"""

import uuid

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import get_current_merchant
from app.database import get_db
from app.models.merchant import Merchant
from app.models.staff import ROLE_PERMISSIONS, StaffMember
from app.schemas.common import AnyResponse


router = APIRouter(prefix="/api/v1/staff", tags=["staff"])


# ═══════════════════════════════════════════════════════════════
# 权限依赖（供其他路由使用）
# ═══════════════════════════════════════════════════════════════


class PermissionContext:
    """权限上下文 — 记录当前操作者身份和权限检查结果."""

    def __init__(
        self, merchant_id: uuid.UUID, staff_id: uuid.UUID | None, role: str, permissions: set[str]
    ):
        self.merchant_id = merchant_id
        self.staff_id = staff_id
        self.role = role
        self.permissions = permissions


def require_permission(permission: str):
    """路由级权限依赖工厂。用法: Depends(require_permission("void_record")).

    在路由层直接拦截无权限用户, 返回 403。
    当前通过 X-Staff-Id header 区分员工（过渡方案），
    未来应改为员工 JWT token。
    """

    async def _check(
        request: Request,
        merchant: Merchant = Depends(get_current_merchant),
        db: AsyncSession = Depends(get_db),
    ) -> PermissionContext:
        role = "owner"
        staff_id: uuid.UUID | None = None

        staff_header = request.headers.get("X-Staff-Id")
        if staff_header:
            try:
                sid = uuid.UUID(staff_header)
                staff = await db.get(StaffMember, sid)
                if staff and staff.merchant_id == merchant.id and staff.is_active:
                    role = staff.role
                    staff_id = sid
            except ValueError:
                pass

        perms = ROLE_PERMISSIONS.get(role, set())
        if permission not in perms:
            raise HTTPException(
                status_code=403,
                detail=f"角色 {role} 无权限执行 {permission}",
            )
        return PermissionContext(
            merchant_id=merchant.id,
            staff_id=staff_id,
            role=role,
            permissions=perms,
        )

    return _check


# ═══════════════════════════════════════════════════════════════
# 角色与员工 CRUD
# ═══════════════════════════════════════════════════════════════


@router.get("/roles", response_model=AnyResponse)
async def list_roles():
    """Return available roles and their permissions."""
    return {
        "code": 0,
        "data": [
            {"role": name, "permissions": sorted(perms)} for name, perms in ROLE_PERMISSIONS.items()
        ],
    }


@router.get("", response_model=AnyResponse)
async def list_staff(
    merchant: Merchant = Depends(get_current_merchant),
    db: AsyncSession = Depends(get_db),
):
    rows = (
        (
            await db.execute(
                select(StaffMember).where(
                    StaffMember.merchant_id == merchant.id, StaffMember.is_active.is_(True)
                )
            )
        )
        .scalars()
        .all()
    )
    return {
        "code": 0,
        "data": [
            {
                "staff_id": str(s.id),
                "name": s.name,
                "phone": s.phone,
                "role": s.role,
                "permissions": sorted(ROLE_PERMISSIONS.get(s.role, set())),
            }
            for s in rows
        ],
    }


@router.post("", response_model=AnyResponse)
async def create_staff(
    body: dict,
    merchant: Merchant = Depends(get_current_merchant),
    db: AsyncSession = Depends(get_db),
):
    name = (body.get("name") or "").strip()
    role = body.get("role", "cashier")
    if not name:
        raise HTTPException(status_code=400, detail="姓名不能为空")
    if role not in ROLE_PERMISSIONS:
        raise HTTPException(status_code=400, detail=f"无效角色: {role}")

    s = StaffMember(
        merchant_id=merchant.id,
        name=name,
        phone=body.get("phone"),
        role=role,
        pin_code=body.get("pin_code"),
    )
    db.add(s)
    await db.commit()
    await db.refresh(s)
    return {"code": 0, "data": {"staff_id": str(s.id), "name": s.name, "role": s.role}}


@router.put("/{staff_id}", response_model=AnyResponse)
async def update_staff(
    staff_id: uuid.UUID,
    body: dict,
    merchant: Merchant = Depends(get_current_merchant),
    db: AsyncSession = Depends(get_db),
):
    s = await db.get(StaffMember, staff_id)
    if not s or s.merchant_id != merchant.id:
        raise HTTPException(status_code=404, detail="员工不存在")
    for f in ("name", "phone", "pin_code"):
        if f in body:
            setattr(s, f, body[f])
    if "role" in body:
        if body["role"] not in ROLE_PERMISSIONS:
            raise HTTPException(status_code=400, detail="无效角色")
        s.role = body["role"]
    if "is_active" in body:
        s.is_active = bool(body["is_active"])
    await db.commit()
    return {"code": 0, "data": {"staff_id": str(s.id), "name": s.name, "role": s.role}}


@router.delete("/{staff_id}", response_model=AnyResponse)
async def deactivate_staff(
    staff_id: uuid.UUID,
    merchant: Merchant = Depends(get_current_merchant),
    db: AsyncSession = Depends(get_db),
):
    s = await db.get(StaffMember, staff_id)
    if not s or s.merchant_id != merchant.id:
        raise HTTPException(status_code=404, detail="员工不存在")
    s.is_active = False
    await db.commit()
    return {"code": 0, "message": f"已停用 {s.name}"}


@router.get("/permissions/check", response_model=AnyResponse)
async def check_permission(
    action: str,
    merchant: Merchant = Depends(get_current_merchant),
):
    """Return whether the current user (owner) has a given permission."""
    owner_perms = ROLE_PERMISSIONS.get("owner", set())
    return {"code": 0, "data": {"action": action, "allowed": action in owner_perms}}
