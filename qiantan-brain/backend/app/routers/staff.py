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

from app.core.security import create_access_token, get_current_merchant
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


# 高风险权限：即使 owner 默认全权，这些操作仍建议显式记录审计日志
# 修复（审计 P1-7）：原实现 role 硬编码 "owner"，员工通过省略 X-Staff-Id 头即可冒充 owner。
HIGH_RISK_PERMISSIONS: set[str] = {
    "order_refund",
    "daily_settle",
    "void_record",
    "record_waste",
}


def require_permission(permission: str):
    """路由级权限依赖工厂。用法: Depends(require_permission("void_record")).

    在路由层直接拦截无权限用户, 返回 403。

    修复（审计 P1-7）：role 从 token 注入（get_current_merchant 已将 token 的
    role/staff_id 写到 merchant._token_role / _token_staff_id），不再硬编码 "owner"。
    兼容保留 X-Staff-Id 头作为补充来源，但优先取 token claim。
    未来员工有自己的 JWT 时，token role 即员工角色，此检查自动生效。
    """

    async def _check(
        request: Request,
        merchant: Merchant = Depends(get_current_merchant),
        db: AsyncSession = Depends(get_db),
    ) -> PermissionContext:
        # 优先从 token 取 role（审计 P1-7：不再硬编码 "owner"）
        role = getattr(merchant, "_token_role", None) or "owner"
        staff_id: uuid.UUID | None = None

        # 优先从 token 的 staff_id claim 取员工身份
        token_staff_id = getattr(merchant, "_token_staff_id", None)
        if token_staff_id:
            try:
                sid = uuid.UUID(str(token_staff_id))
                staff = await db.get(StaffMember, sid)
                if staff and staff.merchant_id == merchant.id and staff.is_active:
                    role = staff.role
                    staff_id = sid
            except (ValueError, TypeError):
                pass

        # 兼容：X-Staff-Id 头（过渡方案，优先级低于 token claim）
        if staff_id is None:
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


@router.post("/login", response_model=AnyResponse)
async def staff_login(
    body: dict,
    merchant: Merchant = Depends(get_current_merchant),
    db: AsyncSession = Depends(get_db),
):
    """员工 PIN 登录：验证员工身份后签发带 staff_id claim 的 JWT。

    商户（owner）已登录状态下，员工输入 PIN 切换身份。前端拿到 staff token
    后替换 owner token，后续请求的 require_permission 会从 token 读
    role/staff_id 执行权限拦截。
    """
    staff_id = body.get("staff_id")
    pin_code = body.get("pin_code")
    if not staff_id or not pin_code:
        raise HTTPException(status_code=400, detail="需要 staff_id 和 pin_code")
    try:
        sid = uuid.UUID(str(staff_id))
    except (ValueError, TypeError) as err:
        raise HTTPException(status_code=400, detail="staff_id 格式无效") from err
    staff = await db.get(StaffMember, sid)
    if not staff or staff.merchant_id != merchant.id:
        raise HTTPException(status_code=404, detail="员工不存在")
    if not staff.is_active:
        raise HTTPException(status_code=403, detail="员工已停用")
    if not staff.pin_code or staff.pin_code != str(pin_code):
        raise HTTPException(status_code=401, detail="PIN 码错误")
    token = create_access_token(merchant.id, role=staff.role, staff_id=staff.id)
    return {
        "code": 0,
        "data": {
            "token": token,
            "staff_id": str(staff.id),
            "name": staff.name,
            "role": staff.role,
            "permissions": sorted(ROLE_PERMISSIONS.get(staff.role, set())),
        },
    }


@router.get("", response_model=AnyResponse)
async def list_staff(
    include_inactive: bool = False,
    merchant: Merchant = Depends(get_current_merchant),
    db: AsyncSession = Depends(get_db),
):
    """获取员工列表。

    - 默认仅返回 is_active=True 的员工（向后兼容）。
    - include_inactive=true 时返回全部员工（含已停用），用于"已停用员工"恢复入口。
    """
    stmt = select(StaffMember).where(StaffMember.merchant_id == merchant.id)
    if not include_inactive:
        stmt = stmt.where(StaffMember.is_active.is_(True))
    rows = (await db.execute(stmt)).scalars().all()
    return {
        "code": 0,
        "data": [
            {
                "staff_id": str(s.id),
                "name": s.name,
                "phone": s.phone,
                "role": s.role,
                "is_active": s.is_active,
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
    """更新员工字段。

    支持 name / phone / pin_code / role / is_active 字段更新。
    pin_code 字段：
      - 不传：保持原值（默认）
      - 传 ''（空字符串）：显式清空已有 PIN（区别于"不修改"）
      - 传非空字符串：更新为新 PIN
    """
    s = await db.get(StaffMember, staff_id)
    if not s or s.merchant_id != merchant.id:
        raise HTTPException(status_code=404, detail="员工不存在")
    for f in ("name", "phone", "pin_code"):
        if f in body:
            # pin_code 允许显式清空（空字符串）；其余字段空值跳过更新
            value = body[f]
            if value == "" and f != "pin_code":
                continue
            setattr(s, f, value if value != "" else None)
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
