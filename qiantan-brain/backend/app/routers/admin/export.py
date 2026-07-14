"""管理后台 CSV 导出 API — 统一数据导出（租户/订阅/发票）。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.admin_permissions import EXPORT_DATA, require_admin_permission
from app.core.admin_security import get_current_admin
from app.core.audit import log_action
from app.core.export import export_csv_response
from app.database import get_db
from app.models.saas import Invoice, Plan, PlatformAdmin, Subscription, Tenant


router = APIRouter(prefix="/api/admin/export", tags=["admin-export"])


_EXPORT_TYPES = ["tenants", "subscriptions", "invoices"]


@router.get("/{data_type}")
async def export_data(
    data_type: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    admin: PlatformAdmin = Depends(get_current_admin),
    _perm=Depends(require_admin_permission(EXPORT_DATA)),
):
    """导出数据为 CSV 文件 — 高风险操作，需审计。"""
    if data_type not in _EXPORT_TYPES:
        raise HTTPException(
            status_code=400, detail=f"不支持的类型，允许: {', '.join(_EXPORT_TYPES)}"
        )

    await log_action(
        db,
        admin.id,
        admin.email,
        "export",
        resource_type="export",
        resource_id=data_type,
        detail={"data_type": data_type},
        request=request,
    )
    await db.commit()

    if data_type == "tenants":
        tenant_result = await db.execute(select(Tenant).order_by(Tenant.created_at.desc()))
        tenant_rows = tenant_result.scalars().all()
        return export_csv_response(
            [
                {
                    "ID": str(tenant.id),
                    "名称": tenant.name,
                    "Slug": tenant.slug,
                    "状态": tenant.status,
                    "联系邮箱": tenant.contact_email or "",
                    "联系电话": tenant.contact_phone or "",
                    "试用到期": tenant.trial_ends_at.isoformat() if tenant.trial_ends_at else "",
                    "创建时间": tenant.created_at.isoformat() if tenant.created_at else "",
                }
                for tenant in tenant_rows
            ],
            "tenants",
        )

    if data_type == "subscriptions":
        subscription_result = await db.execute(
            select(Subscription, Tenant.name, Plan.name)
            .select_from(Subscription)
            .outerjoin(Tenant, Subscription.tenant_id == Tenant.id)
            .outerjoin(Plan, Subscription.plan_id == Plan.id)
            .order_by(Subscription.created_at.desc())
        )
        subscription_rows = subscription_result.all()
        return export_csv_response(
            [
                {
                    "ID": str(subscription.id),
                    "租户": tenant_name or "",
                    "套餐": plan_name or "",
                    "计费周期": "年付" if subscription.billing_cycle == "yearly" else "月付",
                    "状态": subscription.status,
                    "周期开始": (
                        subscription.current_period_start.isoformat()
                        if subscription.current_period_start
                        else ""
                    ),
                    "周期结束": (
                        subscription.current_period_end.isoformat()
                        if subscription.current_period_end
                        else ""
                    ),
                    "自动续费": "是" if subscription.auto_renew else "否",
                    "创建时间": subscription.created_at.isoformat()
                    if subscription.created_at
                    else "",
                }
                for subscription, tenant_name, plan_name in subscription_rows
            ],
            "subscriptions",
        )

    invoice_result = await db.execute(
        select(Invoice, Tenant.name)
        .select_from(Invoice)
        .outerjoin(Tenant, Invoice.tenant_id == Tenant.id)
        .order_by(Invoice.created_at.desc())
    )
    invoice_rows = invoice_result.all()
    return export_csv_response(
        [
            {
                "ID": str(invoice.id),
                "发票号": invoice.invoice_no,
                "租户": tenant_name or "",
                "金额": str(invoice.amount),
                "币种": invoice.currency,
                "状态": invoice.status,
                "到期日": invoice.due_date.isoformat() if invoice.due_date else "",
                "支付时间": invoice.paid_at.isoformat() if invoice.paid_at else "",
                "支付方式": invoice.payment_method or "",
                "创建时间": invoice.created_at.isoformat() if invoice.created_at else "",
            }
            for invoice, tenant_name in invoice_rows
        ],
        "invoices",
    )
