"""租户侧 API — 租户管理员查看自己的订阅/用量/发票。

鉴权：复用 Merchant JWT（iss="qiantan-brain"），通过 merchant.tenant_id 确定租户上下文。
仅允许 owner / tenant_admin 角色访问。
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import get_current_merchant
from app.core.tenant_context import set_tenant_id
from app.database import get_db
from app.models.merchant import Merchant
from app.models.saas import Invoice, Plan, Subscription, Tenant


router = APIRouter(prefix="/api/tenant", tags=["tenant"])


async def get_tenant_merchant(
    merchant: Merchant = Depends(get_current_merchant),
) -> Merchant:
    """确保商户绑定了租户，并注入 tenant_id 到上下文。"""
    if not merchant.tenant_id:
        raise HTTPException(status_code=403, detail="商户未绑定租户")
    if merchant.role not in ("owner", "tenant_admin"):
        raise HTTPException(status_code=403, detail="无权访问租户信息")
    set_tenant_id(merchant.tenant_id)
    return merchant


def _require_tenant_id(merchant: Merchant) -> uuid.UUID:
    """Narrow the legacy nullable ORM field before service/DB boundaries."""
    tenant_id = merchant.tenant_id
    if tenant_id is None:
        raise HTTPException(status_code=403, detail="商户未绑定租户")
    return tenant_id


# ── 订阅信息 ──────────────────────────────────────


class TenantSubscriptionInfo(BaseModel):
    plan_code: str | None
    plan_name: str | None
    billing_cycle: str | None
    status: str | None
    current_period_start: str | None
    current_period_end: str | None
    auto_renew: bool
    max_merchants: int
    max_api_calls_monthly: int
    max_storage_mb: int


@router.get("/subscription", response_model=TenantSubscriptionInfo)
async def get_my_subscription(
    merchant: Merchant = Depends(get_tenant_merchant),
    db: AsyncSession = Depends(get_db),
):
    """查看本租户的订阅信息。"""
    tenant_id = _require_tenant_id(merchant)
    result = await db.execute(
        select(Subscription).where(
            Subscription.tenant_id == tenant_id,
            Subscription.status.in_(["trialing", "active"]),
        )
    )
    sub = result.scalar_one_or_none()

    if sub is None:
        tenant = await db.get(Tenant, tenant_id)
        plan = await db.get(Plan, tenant.plan_id) if tenant and tenant.plan_id else None
        return TenantSubscriptionInfo(
            plan_code=plan.code if plan else None,
            plan_name=plan.name if plan else None,
            billing_cycle=None,
            status=None,
            current_period_start=None,
            current_period_end=None,
            auto_renew=False,
            max_merchants=plan.max_merchants if plan else 0,
            max_api_calls_monthly=plan.max_api_calls_monthly if plan else 0,
            max_storage_mb=plan.max_storage_mb if plan else 0,
        )

    plan = await db.get(Plan, sub.plan_id)
    return TenantSubscriptionInfo(
        plan_code=plan.code if plan else None,
        plan_name=plan.name if plan else None,
        billing_cycle=sub.billing_cycle,
        status=sub.status,
        current_period_start=sub.current_period_start.isoformat()
        if sub.current_period_start
        else None,
        current_period_end=sub.current_period_end.isoformat() if sub.current_period_end else None,
        auto_renew=sub.auto_renew,
        max_merchants=plan.max_merchants if plan else 0,
        max_api_calls_monthly=plan.max_api_calls_monthly if plan else 0,
        max_storage_mb=plan.max_storage_mb if plan else 0,
    )


# ── 用量配额 ──────────────────────────────────────


@router.get("/usage/quotas")
async def get_my_quotas(
    merchant: Merchant = Depends(get_tenant_merchant),
    db: AsyncSession = Depends(get_db),
):
    """查看本租户的用量配额状态。"""
    from app.core.quota import get_all_quotas

    tenant_id = _require_tenant_id(merchant)
    quotas = await get_all_quotas(db, tenant_id)
    return {"quotas": quotas}


@router.get("/usage/trend/{metric}")
async def get_my_usage_trend(
    metric: str,
    merchant: Merchant = Depends(get_tenant_merchant),
    db: AsyncSession = Depends(get_db),
):
    """查看本租户最近 30 天的用量趋势。"""
    from app.core.quota import get_usage_trend

    tenant_id = _require_tenant_id(merchant)
    trend = await get_usage_trend(db, tenant_id, metric, 30)
    return {"metric": metric, "trend": trend}


# ── 发票 ──────────────────────────────────────────


@router.get("/invoices")
async def get_my_invoices(
    merchant: Merchant = Depends(get_tenant_merchant),
    db: AsyncSession = Depends(get_db),
):
    """查看本租户的发票列表。"""
    tenant_id = _require_tenant_id(merchant)
    result = await db.execute(
        select(Invoice).where(Invoice.tenant_id == tenant_id).order_by(Invoice.created_at.desc())
    )
    invoices = result.scalars().all()
    return [
        {
            "id": str(inv.id),
            "invoice_no": inv.invoice_no,
            "amount": str(inv.amount),
            "currency": inv.currency,
            "status": inv.status,
            "due_date": inv.due_date.isoformat() if inv.due_date else None,
            "paid_at": inv.paid_at.isoformat() if inv.paid_at else None,
            "created_at": inv.created_at.isoformat() if inv.created_at else None,
        }
        for inv in invoices
    ]
