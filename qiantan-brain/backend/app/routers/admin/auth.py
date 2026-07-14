"""管理后台鉴权 API — 登录 / 登出 / 当前管理员信息。

登录限流：5 分钟内最多 5 次失败尝试，超过锁定 15 分钟。
审计日志：登录/登出操作自动记录到 admin_audit_logs 表。
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, EmailStr
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.admin_permissions import (
    DASHBOARD_READ,
    ROLE_PERMISSIONS,
    get_permission_manifest,
    require_admin_permission,
)
from app.core.admin_security import (
    create_admin_token,
    decode_admin_token,
    extract_admin_token,
    get_current_admin,
    revoke_admin_token,
    verify_password,
)
from app.core.audit import log_action
from app.core.rate_limiter import (
    check_rate_limit,
    clear_attempts,
    record_failed_attempt,
)
from app.database import get_db
from app.models.saas import PlatformAdmin


router = APIRouter(prefix="/api/admin", tags=["admin-auth"])


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class AdminInfo(BaseModel):
    id: uuid.UUID
    email: str
    name: str
    role: str
    permissions: list[str]

    model_config = {"from_attributes": True}


def _build_admin_info(admin: PlatformAdmin) -> AdminInfo:
    """Return identity and effective grants from the same server-side RBAC map."""
    return AdminInfo(
        id=admin.id,
        email=admin.email,
        name=admin.name,
        role=admin.role,
        permissions=sorted(ROLE_PERMISSIONS.get(admin.role, set())),
    )


class LoginResponse(BaseModel):
    admin: AdminInfo
    token: str | None = None
    token_type: str | None = None


@router.post(
    "/login",
    response_model=LoginResponse,
    response_model_exclude_none=True,
)
async def login(
    req: LoginRequest,
    request: Request,
    response: Response,
    db: AsyncSession = Depends(get_db),
):
    """平台管理员登录 — 邮箱 + 密码 → JWT。

    限流：5 分钟内最多 5 次失败尝试，超限锁定 15 分钟。
    """
    # 限流检查
    check_rate_limit(request, req.email)

    result = await db.execute(
        select(PlatformAdmin).where(PlatformAdmin.email == req.email)
    )
    admin = result.scalar_one_or_none()

    if admin is None or not verify_password(req.password, admin.password_hash):
        record_failed_attempt(request, req.email)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="邮箱或密码错误",
        )
    if not admin.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="账号已停用",
        )

    # 登录成功，清除失败记录
    clear_attempts(request, req.email)

    admin.last_login_at = datetime.now(UTC)

    token = create_admin_token(admin.id, admin.role)

    # 审计日志（与 last_login_at 同事务）
    await log_action(db, admin.id, admin.email, "login", request=request)
    await db.commit()

    response.set_cookie(
        key=settings.admin_cookie_name,
        value=token,
        max_age=settings.admin_jwt_expire_minutes * 60,
        httponly=True,
        secure=not settings.debug,
        samesite="strict",
        path="/api/admin",
    )

    # Bearer clients must explicitly opt in; browser JS never receives the token.
    expose_token = request.headers.get("X-Admin-Token-Response", "").lower() in {
        "1",
        "true",
    }
    return LoginResponse(
        token=token if expose_token else None,
        token_type="Bearer" if expose_token else None,
        admin=_build_admin_info(admin),
    )


@router.post("/logout")
async def logout(
    request: Request,
    response: Response,
    db: AsyncSession = Depends(get_db),
):
    """管理员登出 — 幂等吊销当前 token，并始终清除 HttpOnly Cookie。"""
    token = extract_admin_token(request)
    admin = None
    if token:
        try:
            payload = decode_admin_token(token)
            jti = payload.get("jti")
            if jti:
                await revoke_admin_token(db, jti, payload.get("exp"))
            admin = await db.get(PlatformAdmin, uuid.UUID(payload["sub"]))
        except (HTTPException, ValueError, KeyError):
            # 过期或无效 cookie 也必须能够被服务端清除。
            admin = None

    if admin is not None:
        await log_action(db, admin.id, admin.email, "logout", request=request)
    await db.commit()

    response.delete_cookie(
        key=settings.admin_cookie_name,
        path="/api/admin",
        secure=not settings.debug,
        httponly=True,
        samesite="strict",
    )
    return {"message": "已退出登录"}


@router.get("/me", response_model=AdminInfo)
async def get_me(admin: PlatformAdmin = Depends(get_current_admin)):
    """获取当前管理员信息。"""
    return _build_admin_info(admin)


@router.get("/permissions")
async def get_permissions(
    admin: PlatformAdmin = Depends(get_current_admin),
    _perm=Depends(require_admin_permission(DASHBOARD_READ)),
):
    """获取当前管理员的权限列表和全部权限矩阵。"""
    manifest = get_permission_manifest()
    manifest["my_role"] = admin.role
    manifest["my_permissions"] = sorted(
        ROLE_PERMISSIONS.get(admin.role, ROLE_PERMISSIONS.get("ops_admin", set()))
    ) if admin.role != "super_admin" else sorted(manifest["roles"]["super_admin"])
    return manifest
