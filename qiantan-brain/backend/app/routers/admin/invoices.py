"""管理后台发票管理 API — 列表/详情/创建/标记已付。"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.admin_permissions import (
    INVOICE_CREATE,
    INVOICE_MARK_PAID,
    INVOICE_READ,
    INVOICE_UPDATE,
    require_admin_permission,
)
from app.core.admin_security import get_current_admin
from app.core.audit import log_action
from app.core.invoicing import get_overlapping_invoice, insert_invoice
from app.database import get_db
from app.models.saas import Invoice, Plan, PlatformAdmin, Subscription, Tenant
from app.services.state_machine import validate_invoice_transition


router = APIRouter(prefix="/api/admin/invoices", tags=["admin-invoices"])


class InvoiceInfo(BaseModel):
    id: uuid.UUID
    tenant_id: uuid.UUID
    tenant_name: str
    subscription_id: uuid.UUID | None
    invoice_no: str
    amount: Decimal
    currency: str
    status: str
    period_start: datetime | None
    period_end: datetime | None
    due_date: datetime | None
    paid_at: datetime | None
    payment_method: str | None
    transaction_id: str | None
    line_items: list[dict[str, object]] | None
    notes: str | None
    created_at: datetime
    model_config = {"from_attributes": True}


class PaginatedInvoices(BaseModel):
    items: list[InvoiceInfo]
    total: int
    page: int
    page_size: int


class InvoiceCreate(BaseModel):
    """手工开票入参 — 上界收紧（第五轮顺手项）。

    amount 上界 1e7（Numeric(12,2) 列宽内）、due_days ≤ 365、notes ≤ 2000，
    防御异常入参落库；负/零金额与负账期无业务意义，一并拒绝（422）。
    """

    tenant_id: uuid.UUID
    subscription_id: uuid.UUID | None = None
    amount: Decimal = Field(gt=0, le=Decimal("10000000"))
    currency: Literal["CNY"] = "CNY"
    period_start: datetime | None = None
    period_end: datetime | None = None
    due_days: int = Field(default=30, ge=0, le=365)
    line_items: list[dict[str, object]] | None = None
    notes: str | None = Field(default=None, max_length=2000)


class InvoiceUpdate(BaseModel):
    status: str | None = None
    payment_method: str | None = None
    transaction_id: str | None = None
    notes: str | None = None


class MarkPaidRequest(BaseModel):
    payment_method: str = "manual"
    transaction_id: str | None = None
    reason: str | None = None


def _build_info(inv: Invoice, tenant: Tenant | None) -> InvoiceInfo:
    return InvoiceInfo(
        id=inv.id,
        tenant_id=inv.tenant_id,
        tenant_name=tenant.name if tenant else "N/A",
        subscription_id=inv.subscription_id,
        invoice_no=inv.invoice_no,
        amount=inv.amount,
        currency=inv.currency,
        status=inv.status,
        period_start=inv.period_start,
        period_end=inv.period_end,
        due_date=inv.due_date,
        paid_at=inv.paid_at,
        payment_method=inv.payment_method,
        transaction_id=inv.transaction_id,
        line_items=inv.line_items,
        notes=inv.notes,
        created_at=inv.created_at,
    )


@router.get("", response_model=PaginatedInvoices)
async def list_invoices(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    status_filter: str | None = Query(None, alias="status"),
    tenant_id: uuid.UUID | None = Query(None),
    db: AsyncSession = Depends(get_db),
    _perm=Depends(require_admin_permission(INVOICE_READ)),
):
    base_query = select(Invoice, Tenant).join(Tenant, Tenant.id == Invoice.tenant_id)
    count_query = select(func.count(Invoice.id))
    if status_filter:
        base_query = base_query.where(Invoice.status == status_filter)
        count_query = count_query.where(Invoice.status == status_filter)
    if tenant_id:
        base_query = base_query.where(Invoice.tenant_id == tenant_id)
        count_query = count_query.where(Invoice.tenant_id == tenant_id)
    total = await db.scalar(count_query) or 0
    base_query = (
        base_query.order_by(Invoice.created_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    result = await db.execute(base_query)
    items = [_build_info(inv, tenant) for inv, tenant in result.all()]
    return PaginatedInvoices(items=items, total=total, page=page, page_size=page_size)


@router.get("/{invoice_id}", response_model=InvoiceInfo)
async def get_invoice_detail(
    invoice_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _perm=Depends(require_admin_permission(INVOICE_READ)),
):
    inv = await db.get(Invoice, invoice_id)
    if inv is None:
        raise HTTPException(status_code=404, detail="发票不存在")
    tenant = await db.get(Tenant, inv.tenant_id)
    return _build_info(inv, tenant)


@router.post("", response_model=InvoiceInfo, status_code=status.HTTP_201_CREATED)
async def create_invoice(
    req: InvoiceCreate,
    request: Request,
    db: AsyncSession = Depends(get_db),
    admin: PlatformAdmin = Depends(get_current_admin),
    _perm=Depends(require_admin_permission(INVOICE_CREATE)),
):
    tenant = await db.get(Tenant, req.tenant_id)
    if tenant is None:
        raise HTTPException(status_code=404, detail="租户不存在")
    now = datetime.now(UTC)
    due_date = now + timedelta(days=req.due_days)
    # invoice_no 由数据库侧 MAX+1 发号（进程重启/双副本不撞号）；
    # (subscription_id, period_start) 已有票时 insert_invoice 返回已有票 → 409。
    inv, created = await insert_invoice(
        db,
        {
            "tenant_id": req.tenant_id,
            "subscription_id": req.subscription_id,
            "amount": req.amount,
            "currency": req.currency,
            "status": "draft",
            "period_start": req.period_start,
            "period_end": req.period_end,
            "due_date": due_date,
            "line_items": req.line_items,
            "notes": req.notes,
        },
        now=now,
    )
    if not created:
        raise HTTPException(
            status_code=409,
            detail=f"该订阅在此计费周期已存在发票 {inv.invoice_no}，不可重复开具",
        )

    await log_action(
        db,
        admin.id,
        admin.email,
        "create",
        resource_type="invoice",
        resource_id=str(inv.id),
        detail={"invoice_no": inv.invoice_no, "amount": str(inv.amount), "tenant": tenant.name},
        request=request,
    )
    await db.commit()
    await db.refresh(inv)
    return _build_info(inv, tenant)


@router.put("/{invoice_id}", response_model=InvoiceInfo)
async def update_invoice(
    invoice_id: uuid.UUID,
    update: InvoiceUpdate,
    request: Request,
    db: AsyncSession = Depends(get_db),
    admin: PlatformAdmin = Depends(get_current_admin),
    _perm=Depends(require_admin_permission(INVOICE_UPDATE)),
):
    inv = await db.get(Invoice, invoice_id)
    if inv is None:
        raise HTTPException(status_code=404, detail="发票不存在")

    old_status = inv.status
    valid_statuses = {"draft", "sent", "paid", "overdue", "void"}
    if update.status is not None:
        if update.status not in valid_statuses:
            raise HTTPException(
                status_code=400,
                detail=f"状态无效，允许: {', '.join(sorted(valid_statuses))}",
            )
        if update.status == "paid":
            raise HTTPException(status_code=400, detail="请使用标记已付接口完成付款登记")
        if update.status != old_status:
            validate_invoice_transition(old_status, update.status)
            inv.status = update.status
    if update.payment_method is not None:
        inv.payment_method = update.payment_method
    if update.transaction_id is not None:
        inv.transaction_id = update.transaction_id
    if update.notes is not None:
        inv.notes = update.notes

    await log_action(
        db,
        admin.id,
        admin.email,
        "update",
        resource_type="invoice",
        resource_id=str(inv.id),
        detail={"invoice_no": inv.invoice_no, "status": f"{old_status} -> {inv.status}"},
        request=request,
    )
    await db.commit()
    await db.refresh(inv)
    tenant = await db.get(Tenant, inv.tenant_id)
    return _build_info(inv, tenant)


@router.post("/{invoice_id}/mark-paid", response_model=InvoiceInfo)
async def mark_invoice_paid(
    invoice_id: uuid.UUID,
    req: MarkPaidRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    admin: PlatformAdmin = Depends(get_current_admin),
    _perm=Depends(require_admin_permission(INVOICE_MARK_PAID)),
):
    """标记发票为已付 — 高风险操作，仅 billing_admin/super_admin。"""
    inv = await db.get(Invoice, invoice_id)
    if inv is None:
        raise HTTPException(status_code=404, detail="发票不存在")

    validate_invoice_transition(inv.status, "paid")
    old_status = inv.status
    inv.status = "paid"
    inv.paid_at = datetime.now(UTC)
    inv.payment_method = req.payment_method
    if req.transaction_id:
        inv.transaction_id = req.transaction_id

    await log_action(
        db,
        admin.id,
        admin.email,
        "mark_paid",
        resource_type="invoice",
        resource_id=str(inv.id),
        detail={
            "invoice_no": inv.invoice_no,
            "amount": str(inv.amount),
            "previous_status": old_status,
            "payment_method": req.payment_method,
            "reason": req.reason,
        },
        request=request,
    )
    await db.commit()
    await db.refresh(inv)
    tenant = await db.get(Tenant, inv.tenant_id)
    return _build_info(inv, tenant)


@router.post("/generate-from-subscription/{sub_id}")
async def generate_invoice_from_subscription(
    sub_id: uuid.UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
    admin: PlatformAdmin = Depends(get_current_admin),
    _perm=Depends(require_admin_permission(INVOICE_CREATE)),
):
    """从订阅生成续期发票 — 与 worker generate_invoices 同一键与同周期语义（V1-H1）。

    语义决策：本入口开出「下一计费周期」的续费票，period_start 锚定
    current_period_end（下一周期起点），与 worker 完全一致。原实现锚定
    current_period_start（当期补开）有两个洞：

    1. 当期费用已随开通/激活收取（admin/subscriptions.py create/activate
       收取并设定周期，不落 Invoice 行），admin 再按当期出票 = 同一笔费用
       重复出票；
    2. activate 续期会把 current_period_start 重置为 now，锚点漂移后与既有
       票（含 worker 预生成的下期票）区间重叠但 (subscription_id,
       period_start) 键不等，唯一约束拦不住。

    统一键后跨入口重试天然幂等（worker 先出下期票 → 本入口返回同一张票，
    不新增第二张）；周期起点漂移形成的重叠期票由 get_overlapping_invoice
    预检拒绝（409），宁可拒开不可重开。确需补开历史周期发票请走手工开票
    接口（POST /api/admin/invoices，显式传 period_start，键冲突返回 409）。
    """
    sub = await db.get(Subscription, sub_id)
    if sub is None:
        raise HTTPException(status_code=404, detail="订阅不存在")
    plan = await db.get(Plan, sub.plan_id)
    if plan is None:
        raise HTTPException(status_code=400, detail="套餐不存在")
    if sub.current_period_end is None:
        raise HTTPException(
            status_code=400,
            detail="订阅缺少当前计费周期结束时间，无法确定续期周期，请先修正订阅周期",
        )

    amount = plan.price_yearly if sub.billing_cycle == "yearly" else plan.price_monthly
    now = datetime.now(UTC)
    # 幂等键 = subscription_id + period_start，period_start 为票覆盖周期
    # （下一计费周期）的起点 = current_period_end，与 worker 共用同一键。
    period_start = sub.current_period_end
    cycle_days = 365 if sub.billing_cycle == "yearly" else 30
    period_end = period_start + timedelta(days=cycle_days)
    line_items = [
        {
            "name": f"{plan.name} - {sub.billing_cycle}",
            "amount": str(amount),
            "description": f"套餐 {plan.code} 的 {sub.billing_cycle} 续期费用",
        }
    ]

    # 快路径：已有该周期发票（本入口重复调用或 worker 已预生成）→ 幂等返回。
    overlapping = await get_overlapping_invoice(db, sub.id, period_start, period_end)
    if overlapping is not None and overlapping.period_start == period_start:
        return {
            "message": "该周期发票已存在，返回已有发票",
            "invoice_id": str(overlapping.id),
            "invoice_no": overlapping.invoice_no,
            "amount": str(overlapping.amount),
            "created": False,
        }
    if overlapping is not None:
        # 周期起点漂移（典型：activate 续期重置周期）后目标区间与既有票重叠。
        raise HTTPException(
            status_code=409,
            detail=(
                f"目标周期 {period_start.isoformat()} 起的续期发票与已有发票 "
                f"{overlapping.invoice_no}（周期 {overlapping.period_start.isoformat()} 起）"
                "覆盖区间重叠，已拒绝出票以避免重复计费"
            ),
        )

    # 慢路径兜底：并发下快路径漏检由 (subscription_id, period_start) 唯一约束
    # + ON CONFLICT DO NOTHING 吸收，insert_invoice 回查返回已有票。
    inv, created = await insert_invoice(
        db,
        {
            "tenant_id": sub.tenant_id,
            "subscription_id": sub.id,
            "amount": amount,
            "currency": "CNY",
            "status": "sent",
            "period_start": period_start,
            "period_end": period_end,
            "due_date": period_start + timedelta(days=7),
            "line_items": line_items,
            "notes": f"基于订阅 {sub.id} 自动生成",
        },
        now=now,
    )

    await log_action(
        db,
        admin.id,
        admin.email,
        "generate_invoice",
        resource_type="invoice",
        resource_id=str(inv.id),
        detail={
            "subscription_id": str(sub_id),
            "invoice_no": inv.invoice_no,
            "amount": str(amount),
            "created": created,
        },
        request=request,
    )
    await db.commit()
    await db.refresh(inv)
    return {
        "message": "发票已生成" if created else "该周期发票已存在，返回已有发票",
        "invoice_id": str(inv.id),
        "invoice_no": inv.invoice_no,
        "amount": str(inv.amount),
        "created": created,
    }
