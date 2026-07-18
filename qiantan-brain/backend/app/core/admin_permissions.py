"""管理后台 RBAC 权限系统。

设计参考商户侧 app/models/staff.py 的 ROLE_PERMISSIONS 和
app/routers/staff.py 的 require_permission 依赖工厂模式。

权限命名: resource.action
角色: super_admin, ops_admin, billing_admin, support_admin, auditor
"""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.admin_security import get_current_admin
from app.database import get_db
from app.models.saas import PlatformAdmin


# ═══════════════════════════════════════════
# 权限点定义
# ═══════════════════════════════════════════

# Dashboard
DASHBOARD_READ = "dashboard.read"

# Tenant
TENANT_READ = "tenant.read"
TENANT_CREATE = "tenant.create"
TENANT_UPDATE = "tenant.update"
TENANT_SUSPEND = "tenant.suspend"  # 暂停/恢复租户

# Plan
PLAN_READ = "plan.read"
PLAN_CREATE = "plan.create"
PLAN_UPDATE = "plan.update"
PLAN_DELETE = "plan.delete"

# Subscription
SUBSCRIPTION_READ = "subscription.read"
SUBSCRIPTION_CREATE = "subscription.create"
SUBSCRIPTION_CHANGE = "subscription.change"

# Invoice
INVOICE_READ = "invoice.read"
INVOICE_CREATE = "invoice.create"
INVOICE_UPDATE = "invoice.update"
INVOICE_MARK_PAID = "invoice.mark_paid"

# Usage
USAGE_READ = "usage.read"
USAGE_ADJUST = "usage.adjust"  # 人工记账（高风险）

# Export
EXPORT_DATA = "export.data"

# AI Ops (future proof)
AI_ACTION_READ = "ai_action.read"
AI_ACTION_APPROVE = "ai_action.approve"

# Admin management
ADMIN_MANAGE = "admin.manage"

# Audit
AUDIT_READ = "audit.read"

# ═══════════════════════════════════════════
# 角色 → 权限点映射
# ═══════════════════════════════════════════

ROLE_PERMISSIONS: dict[str, set[str]] = {
    "super_admin": {
        DASHBOARD_READ,
        TENANT_READ,
        TENANT_CREATE,
        TENANT_UPDATE,
        TENANT_SUSPEND,
        PLAN_READ,
        PLAN_CREATE,
        PLAN_UPDATE,
        PLAN_DELETE,
        SUBSCRIPTION_READ,
        SUBSCRIPTION_CREATE,
        SUBSCRIPTION_CHANGE,
        INVOICE_READ,
        INVOICE_CREATE,
        INVOICE_UPDATE,
        INVOICE_MARK_PAID,
        USAGE_READ,
        USAGE_ADJUST,
        EXPORT_DATA,
        AI_ACTION_READ,
        AI_ACTION_APPROVE,
        ADMIN_MANAGE,
        AUDIT_READ,
    },
    "ops_admin": {
        DASHBOARD_READ,
        TENANT_READ,
        TENANT_CREATE,
        TENANT_UPDATE,
        SUBSCRIPTION_READ,
        INVOICE_READ,
        USAGE_READ,
        AI_ACTION_READ,
        AI_ACTION_APPROVE,
        AUDIT_READ,
    },
    "billing_admin": {
        DASHBOARD_READ,
        TENANT_READ,
        PLAN_READ,
        PLAN_CREATE,
        PLAN_UPDATE,
        PLAN_DELETE,
        SUBSCRIPTION_READ,
        SUBSCRIPTION_CREATE,
        SUBSCRIPTION_CHANGE,
        INVOICE_READ,
        INVOICE_CREATE,
        INVOICE_UPDATE,
        INVOICE_MARK_PAID,
        USAGE_READ,
        USAGE_ADJUST,
        EXPORT_DATA,
        AUDIT_READ,
    },
    "support_admin": {
        DASHBOARD_READ,
        TENANT_READ,
        PLAN_READ,
        SUBSCRIPTION_READ,
        INVOICE_READ,
        USAGE_READ,
    },
    "auditor": {
        DASHBOARD_READ,
        TENANT_READ,
        PLAN_READ,
        SUBSCRIPTION_READ,
        INVOICE_READ,
        USAGE_READ,
        AUDIT_READ,
        EXPORT_DATA,
    },
}

# 所有有效的权限点（用于验证）
ALL_PERMISSIONS: set[str] = set()
for _perms in ROLE_PERMISSIONS.values():
    ALL_PERMISSIONS.update(_perms)


# ═══════════════════════════════════════════
# 依赖工厂
# ═══════════════════════════════════════════


class AdminPermissionContext:
    """权限上下文 — 记录当前管理员身份和权限。"""

    def __init__(
        self,
        admin_id: uuid.UUID,
        admin_email: str,
        role: str,
        permissions: set[str],
    ):
        self.admin_id = admin_id
        self.admin_email = admin_email
        self.role = role
        self.permissions = permissions


def require_admin_permission(permission: str):
    """路由级权限依赖工厂。

    用法:
        _perm = Depends(require_admin_permission("tenant.create"))

    未登录 → 401
    无权限 → 403 + request_id + 稳定错误码
    super_admin 自动拥有所有已注册权限
    """

    if permission not in ALL_PERMISSIONS:
        raise ValueError(f"未注册的权限点: {permission}")

    async def _check(
        request: Request,
        admin: PlatformAdmin = Depends(get_current_admin),
        db: AsyncSession = Depends(get_db),
    ) -> AdminPermissionContext:
        role = admin.role or "ops_admin"

        # super_admin 自动全权
        if role == "super_admin":
            all_perms = ROLE_PERMISSIONS.get("super_admin", set())
            return AdminPermissionContext(
                admin_id=admin.id,
                admin_email=admin.email,
                role=role,
                permissions=all_perms,
            )

        perms = ROLE_PERMISSIONS.get(role, set())
        if permission not in perms:
            request_id = getattr(request.state, "request_id", "unknown")
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={
                    "code": "FORBIDDEN",
                    "message": f"角色 {role} 无权限执行 {permission}",
                    "request_id": request_id,
                },
            )

        # 审计日志（高风险操作）
        if _is_high_risk(permission):
            await _log_permission_check(db, admin, permission, request)

        return AdminPermissionContext(
            admin_id=admin.id,
            admin_email=admin.email,
            role=role,
            permissions=perms,
        )

    return _check


# ═══════════════════════════════════════════
# 高风险操作标记
# ═══════════════════════════════════════════

HIGH_RISK_PERMISSIONS: set[str] = {
    INVOICE_MARK_PAID,
    USAGE_ADJUST,
    TENANT_SUSPEND,
    PLAN_DELETE,
    EXPORT_DATA,
    ADMIN_MANAGE,
}


def _is_high_risk(permission: str) -> bool:
    return permission in HIGH_RISK_PERMISSIONS


async def _log_permission_check(
    db: AsyncSession,
    admin: PlatformAdmin,
    permission: str,
    request: Request,
) -> None:
    """记录高风险操作权限检查审计。"""
    from app.core.audit import log_action

    await log_action(
        db=db,
        admin_id=admin.id,
        admin_email=admin.email,
        action=f"permission_check.{permission}",
        resource_type="permission",
        detail={"permission": permission, "role": admin.role},
        request=request,
    )


def check_suspend_permission(admin: PlatformAdmin) -> None:
    """内联检查管理员是否可以暂停/恢复租户。

    相比路由级 require_admin_permission，此函数用于函数体内条件检查。
    super_admin 自动通过。其他角色必须显式持有 TENANT_SUSPEND。
    """
    if admin.role == "super_admin":
        return
    perms = ROLE_PERMISSIONS.get(admin.role, set())
    if TENANT_SUSPEND not in perms:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "code": "FORBIDDEN",
                "message": f"角色 {admin.role} 无权限执行租户暂停/恢复操作",
            },
        )


# ═══════════════════════════════════════════
# 前端权限列表导出
# ═══════════════════════════════════════════


def get_permission_manifest() -> dict[str, Any]:
    """返回所有权限点和角色映射，供前端 /api/admin/permissions 使用。"""
    return {
        "permissions": sorted(ALL_PERMISSIONS),
        "roles": {role: sorted(perms) for role, perms in ROLE_PERMISSIONS.items()},
        "high_risk": sorted(HIGH_RISK_PERMISSIONS),
    }
