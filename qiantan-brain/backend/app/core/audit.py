"""审计日志工具 — 记录管理员操作。

审计日志与业务操作共享同一个数据库事务。
调用方负责在完成所有业务操作和审计记录后统一 commit，
避免"业务已生效但审计写入失败导致 500"的事务不一致问题。
"""

from __future__ import annotations

import json
import uuid
from typing import Any

from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.admin_audit import AdminAuditLog


async def log_action(
    db: AsyncSession,
    admin_id: uuid.UUID | str,
    admin_email: str,
    action: str,
    resource_type: str | None = None,
    resource_id: str | None = None,
    detail: dict[str, Any] | None = None,
    request: Request | None = None,
) -> AdminAuditLog:
    """记录一条管理员审计日志（仅 add，不 commit）。

    返回创建的 AdminAuditLog 实例，调用方在统一 commit 前可进一步操作。
    """
    ip = None
    ua = None
    if request:
        ip = request.client.host if request.client else None
        ua = request.headers.get("User-Agent", "")

    log = AdminAuditLog(
        admin_id=str(admin_id),
        admin_email=admin_email,
        action=action,
        resource_type=resource_type,
        resource_id=str(resource_id) if resource_id else None,
        detail=json.dumps(detail, ensure_ascii=False, default=str) if detail else None,
        ip_address=ip,
        user_agent=ua,
    )
    db.add(log)
    return log
