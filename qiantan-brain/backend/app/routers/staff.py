"""员工管理与权限 API (section 4.17).

提供：
- 角色/权限定义查询
- 员工 CRUD
- require_permission 依赖：路由级权限执行
"""

import hmac
import uuid

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.rate_limiter import (
    check_rate_limit,
    clear_attempts,
    record_failed_attempt,
)
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
                else:
                    # 修复（F1）：token 携带 staff_id 但员工已停用/不存在/不属于此商户 → 拒绝。
                    # 原实现仅不进入 if 分支，role 仍保持 token 中的角色，权限残留。
                    raise HTTPException(status_code=403, detail="员工账号已停用或不存在")
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
        # 租户/平台管理员虽然不在 staff 的 ROLE_PERMISSIONS 中，但按安全要求
        # 可以管理员工（尤其是授予 market_admin 角色）。
        if role in ("tenant_admin", "platform_admin") and permission == "manage_staff":
            perms = {permission}
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
    request: Request,
    merchant: Merchant = Depends(get_current_merchant),
    db: AsyncSession = Depends(get_db),
):
    """员工 PIN 登录：验证员工身份后签发带 staff_id claim 的 JWT。

    商户（owner）已登录状态下，员工输入 PIN 切换身份。前端拿到 staff token
    后替换 owner token，后续请求的 require_permission 会从 token 读
    role/staff_id 执行权限拦截。

    修复（审计 C-6/H2）：
      - 接入登录限流（staff_id 作为 key，5 次失败锁 15 分钟），防止 PIN 暴力破解。
      - PIN 比较改为 hmac.compare_digest 常量时间比较，杜绝时序侧信道。

    修复（F2 限流投毒 DoS）：
      - 限流 key 改为 f"{merchant.id}:{staff_id}"。原 key 仅用 staff_id（全局），
        恶意 owner 可对任意员工 UUID 发 10 次错 PIN 把该员工在所有商户下锁定 1 小时。
        加 merchant_id 前缀后，限流维度与归属校验保持一致。
      - 顺序调整：先做 staff 存在性 + 归属校验（404），通过后才进入限流/PIN 校验。
        避免攻击者用 404 探测的方式把不存在的 staff_id 计入失败次数。
    """
    staff_id = body.get("staff_id")
    pin_code = body.get("pin_code")
    if not staff_id or not pin_code:
        raise HTTPException(status_code=400, detail="需要 staff_id 和 pin_code")
    try:
        sid = uuid.UUID(str(staff_id))
    except (ValueError, TypeError) as err:
        raise HTTPException(status_code=400, detail="staff_id 格式无效") from err
    # 限流 key 加 merchant_id 前缀，防止跨商户投毒锁定他人员工
    rl_key = f"{merchant.id}:{sid}"
    # 先做归属校验：员工不存在/不属于此商户 → 404，不计入限流（避免 404 探测投毒）
    staff = await db.get(StaffMember, sid)
    if not staff or staff.merchant_id != merchant.id:
        raise HTTPException(status_code=404, detail="员工不存在")
    # 通过归属校验后再检查限流（C-6）：超限抛 429
    await check_rate_limit(request, rl_key)
    if not staff.is_active:
        await record_failed_attempt(request, rl_key)
        raise HTTPException(status_code=403, detail="员工已停用")
    # 应用层断言（V3-H1）：owner 只能由商户本人（merchants）承载，员工表
    # 不允许 owner 行 —— create/update 已禁、迁移 n6d7e8f9a0b1 清理存量、
    # seed 不再生成。此处兜底：未来任何写入路径复活 role='owner' 员工行，
    # 也绝不为其签发带 owner 全权限的员工 token。
    if staff.role == "owner":
        raise HTTPException(status_code=403, detail="owner 角色不允许员工登录")
    # 常量时间比较（H2）：避免时序侧信道泄露正确 PIN 的前缀
    if not staff.pin_code or not hmac.compare_digest(staff.pin_code, str(pin_code)):
        await record_failed_attempt(request, rl_key)
        raise HTTPException(status_code=401, detail="PIN 码错误")
    # 登录成功，清除该限流 key 的失败记录
    await clear_attempts(request, rl_key)
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
    _perm=Depends(require_permission("manage_staff")),
):
    name = (body.get("name") or "").strip()
    role = body.get("role", "cashier")
    if not name:
        raise HTTPException(status_code=400, detail="姓名不能为空")
    if role not in ROLE_PERMISSIONS:
        raise HTTPException(status_code=400, detail=f"无效角色: {role}")
    # 安全（垂直提权修复）：owner 只能是商户本人，不允许经员工体系创建
    if role == "owner":
        raise HTTPException(status_code=400, detail="不允许创建 owner 角色的员工")
    # 安全（审计 R1）：market_admin 属于管理员角色，仅租户/平台管理员可授予；
    # owner 等普通商户操作者只能创建 manager/cashier/purchaser/stocker 等普通员工。
    if role == "market_admin" and _perm.role not in ("tenant_admin", "platform_admin"):
        raise HTTPException(status_code=403, detail="仅租户/平台管理员可创建市场管理员")

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
    _perm=Depends(require_permission("manage_staff")),
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
        # 安全（垂直提权修复）：禁止把员工（含自己）提升为 owner —— owner 只能是商户本人
        if body["role"] == "owner":
            raise HTTPException(status_code=400, detail="不允许将员工角色修改为 owner")
        # 安全（审计 R1）：市场管理员角色仅租户/平台管理员可授予，普通操作者不能
        # 把已有员工（或自己）提升为 market_admin。
        if body["role"] == "market_admin" and _perm.role not in ("tenant_admin", "platform_admin"):
            raise HTTPException(status_code=403, detail="仅租户/平台管理员可授予市场管理员角色")
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
    _perm=Depends(require_permission("manage_staff")),
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
