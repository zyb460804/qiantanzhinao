"""租户上下文管理 — 请求级 tenant_id 注入与隔离校验。

设计：
  - ContextVar 确保异步请求隔离，每个请求有独立的 tenant_id 上下文
  - TenantContextMiddleware 在请求开始时清除 ContextVar，保证干净状态
  - 租户 API：get_current_merchant 加载 Merchant 后自动注入 tenant_id
  - 管理后台 API：tenant_id 从路径参数中显式设置（无自动注入）

SaaS 执行门禁链（新增）：
  Request → JWT Auth → Merchant 解析 → Tenant 注入（security.py）
    → require_active_tenant()     → 检查租户未被停用/删除
    → require_active_subscription() → 检查订阅有效（active/trialing）
    → require_plan_feature("pos")  → 检查套餐是否包含某功能
    → require_quota_check("api_calls") → 检查 + 记录用量

行级隔离原则：
  - 所有租户数据查询必须带 WHERE tenant_id = get_current_tenant_id()
  - 管理后台 API 绕过隔离（平台级操作），但需显式传入 tenant_id
"""

from __future__ import annotations

import logging
import uuid
from contextvars import ContextVar
from typing import Any

from fastapi import Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from app.core.quota import check_quota, record_usage
from app.database import get_db
from app.models.saas import Plan, Subscription, Tenant


logger = logging.getLogger(__name__)

_tenant_id_var: ContextVar[uuid.UUID | None] = ContextVar("tenant_id", default=None)

# ── 过渡期配置：tenant_id 为空时是否阻断请求 ──
# True: 缺 tenant 时直接 403（上线后启用）
# False: 缺 tenant 时仅 WARNING 日志，不阻断（迁移过渡期）
STRICT_TENANT_REQUIRED = False


def get_current_tenant_id() -> uuid.UUID | None:
    """获取当前请求的 tenant_id（从 ContextVar）。"""
    return _tenant_id_var.get()


def set_tenant_id(tenant_id: uuid.UUID | str | None) -> None:
    """设置当前请求的 tenant_id。"""
    if tenant_id is None:
        _tenant_id_var.set(None)
    elif isinstance(tenant_id, str):
        _tenant_id_var.set(uuid.UUID(tenant_id))
    else:
        _tenant_id_var.set(tenant_id)


def clear_tenant_id() -> None:
    """清除当前请求的 tenant_id。"""
    _tenant_id_var.set(None)


class TenantContextMiddleware(BaseHTTPMiddleware):
    """请求级 tenant_id 上下文中间件。

    在请求开始时清除 ContextVar，确保每个请求有独立的 tenant_id 上下文。
    实际的 tenant_id 注入由依赖项（如 get_current_merchant）或路由处理器完成。
    """

    async def dispatch(self, request: Request, call_next: Any) -> Response:
        token = _tenant_id_var.set(None)
        try:
            response = await call_next(request)
            return response
        finally:
            _tenant_id_var.reset(token)


def require_tenant_id() -> uuid.UUID:
    """获取当前 tenant_id，若无则抛 403。

    用于需要租户上下文的端点，确保 tenant_id 已被注入。
    """
    tenant_id = get_current_tenant_id()
    if tenant_id is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="缺少租户上下文，无法执行此操作",
        )
    return tenant_id


# ═══════════════════════════════════════════════════════════════════
# SaaS 执行门禁依赖（FastAPI Depends）— 按顺序串联即可形成完整门禁链
# ═══════════════════════════════════════════════════════════════════


async def require_active_tenant(
    db: AsyncSession = Depends(get_db),
) -> Tenant:
    """门禁 1：校验租户存在且状态正常。

    检查顺序：tenant_id 存在 → Tenant 记录存在 → status 不为 suspended/deleted。

    过渡期（STRICT_TENANT_REQUIRED=False）：tenant_id 为空时仅 WARNING，不阻断。
    完成后切换为 True 强制要求。
    """
    tenant_id = get_current_tenant_id()

    if tenant_id is None:
        if STRICT_TENANT_REQUIRED:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="商户未绑定租户，无法使用此功能",
            )
        logger.warning("tenant_id 为空，跳过租户状态检查（过渡期）")
        return None  # type: ignore[return-value]

    tenant = await db.get(Tenant, tenant_id)
    if tenant is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="租户不存在",
        )
    if tenant.status in ("suspended", "deleted"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"租户已被{'停用' if tenant.status == 'suspended' else '删除'}，请联系管理员",
        )
    return tenant


async def require_active_subscription(
    tenant: Tenant = Depends(require_active_tenant),
    db: AsyncSession = Depends(get_db),
) -> Subscription | None:
    """门禁 2：校验租户有有效订阅。

    允许状态：trialing（试用期）/ active（正常付费）
    拒绝状态：past_due（逾期）/ canceled（已取消）/ expired（已过期）

    过渡期：租户为 None（无 tenant_id）时放行。
    """
    if tenant is None:
        return None  # 过渡期放行

    result = await db.execute(
        select(Subscription).where(
            Subscription.tenant_id == tenant.id,
            Subscription.status.in_(("trialing", "active")),
        ).order_by(Subscription.created_at.desc()).limit(1)
    )
    sub = result.scalar_one_or_none()

    if sub is None:
        # 检查是否有逾期/过期订阅（给出更明确的错误信息）
        result2 = await db.execute(
            select(Subscription).where(
                Subscription.tenant_id == tenant.id,
            ).order_by(Subscription.created_at.desc()).limit(1)
        )
        latest = result2.scalar_one_or_none()
        if latest:
            status_map = {
                "past_due": "订阅已逾期，请续费后使用",
                "canceled": "订阅已取消",
                "expired": "订阅已过期，请续费",
            }
            detail = status_map.get(latest.status, "无有效订阅")
        else:
            detail = "未找到订阅记录，请先开通套餐"
        raise HTTPException(
            status_code=status.HTTP_402_PAYMENT_REQUIRED,
            detail=detail,
        )

    return sub


async def require_plan_feature(
    feature: str,
    tenant: Tenant = Depends(require_active_tenant),
    db: AsyncSession = Depends(get_db),
) -> Plan:
    """门禁 3：校验租户套餐包含指定功能。

    用法：Depends(lambda: require_plan_feature("ai_advisor"))

    检查 plan.features JSON 中是否包含对应 key 且为 truthy。
    过渡期：租户为 None 时放行。
    """
    _ = feature  # 闭包捕获

    if tenant is None or tenant.plan_id is None:
        return None  # type: ignore[return-value]

    plan = await db.get(Plan, tenant.plan_id)
    if plan is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="套餐不存在",
        )

    features = plan.features or {}
    if not features.get(feature):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"当前套餐不支持「{feature}」功能，请升级套餐",
        )

    return plan


async def require_quota_check(
    metric: str,
    increment: int = 1,
    tenant: Tenant = Depends(require_active_tenant),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """门禁 4：检查配额未超限，并自动记录用量。

    用法：Depends(lambda: require_quota_check("api_calls"))
    或：  Depends(lambda: require_quota_check("storage_mb", increment=5))

    返回配额状态 dict：{exceeded, current, limit, remaining, metric}
    超限时抛 429 Too Many Requests。
    过渡期：租户为 None 时放行。
    """
    _ = metric  # 闭包捕获
    _inc = increment  # 闭包捕获

    if tenant is None:
        return {"exceeded": False, "metric": metric, "current": 0, "limit": -1, "remaining": -1}

    # 先检查
    quota_info = await check_quota(db, tenant.id, metric)
    if quota_info["exceeded"]:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"「{metric}」用量已超限（{quota_info['current']}/{quota_info['limit']}），"
                   f"请升级套餐或等待下个计费周期",
        )

    # 后记录（增量）
    await record_usage(db, tenant.id, metric, _inc)
    return quota_info


# ── 便捷工厂函数：一键创建带参数的门禁依赖 ──


def PlanFeature(feature: str):
    """工厂：生成 require_plan_feature(feature) 的依赖。

    用法：
        @router.post("/pos/checkout")
        async def checkout(
            merchant_id=Depends(get_merchant_id),
            plan=Depends(PlanFeature("pos")),   # ← 一行搞定
        ):
    """

    async def _check(
        tenant: Tenant = Depends(require_active_tenant),
        db: AsyncSession = Depends(get_db),
    ) -> Plan:
        return await require_plan_feature(feature, tenant, db)

    return _check


def QuotaCheck(metric: str, increment: int = 1):
    """工厂：生成 require_quota_check(metric, increment) 的依赖。

    用法：
        @router.get("/api/v1/inventory")
        async def list_inventory(
            merchant_id=Depends(get_merchant_id),
            _quota=Depends(QuotaCheck("api_calls")),  # ← 一行搞定
        ):
    """

    async def _check(
        tenant: Tenant = Depends(require_active_tenant),
        db: AsyncSession = Depends(get_db),
    ) -> dict[str, Any]:
        return await require_quota_check(metric, increment, tenant, db)

    return _check
