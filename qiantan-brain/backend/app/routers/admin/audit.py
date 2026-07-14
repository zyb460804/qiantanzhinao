"""管理后台审计日志查询 API。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.admin_permissions import AUDIT_READ, require_admin_permission
from app.database import get_db
from app.models.admin_audit import AdminAuditLog


router = APIRouter(prefix="/api/admin", tags=["admin-audit"])


class AuditLogItem(BaseModel):
    id: str
    admin_id: str
    admin_email: str
    action: str
    resource_type: str | None
    resource_id: str | None
    detail: str | None
    ip_address: str | None
    user_agent: str | None
    created_at: str
    model_config = {"from_attributes": True}


class AuditLogList(BaseModel):
    items: list[AuditLogItem]
    total: int
    page: int
    page_size: int


@router.get("/audit-logs", response_model=AuditLogList)
async def get_audit_logs(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    action: str | None = Query(None, description="筛选操作类型"),
    db: AsyncSession = Depends(get_db),
    _perm=Depends(require_admin_permission(AUDIT_READ)),
):
    """分页查询管理员审计日志。仅 super_admin/ops_admin/auditor 可访问。"""
    base = select(AdminAuditLog)
    if action:
        base = base.where(AdminAuditLog.action == action)
    total = await db.scalar(select(func.count()).select_from(base.subquery()))
    query = (
        base.order_by(desc(AdminAuditLog.created_at))
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    result = await db.execute(query)
    rows = result.scalars().all()
    return AuditLogList(
        items=[
            AuditLogItem(
                id=str(r.id),
                admin_id=r.admin_id,
                admin_email=r.admin_email,
                action=r.action,
                resource_type=r.resource_type,
                resource_id=r.resource_id,
                detail=r.detail,
                ip_address=r.ip_address,
                user_agent=r.user_agent,
                created_at=r.created_at.isoformat() if r.created_at else "",
            )
            for r in rows
        ],
        total=total or 0,
        page=page,
        page_size=page_size,
    )
