"""费用管理 + 月度利润报表 API (sections 4.19)."""

import csv
import io
import uuid
from datetime import date
from datetime import datetime as dt
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import get_current_merchant
from app.database import get_db
from app.models.accounts import SupplierPayable
from app.models.expense import Expense, Invoice
from app.models.merchant import Merchant
from app.models.pos import DailySettlement
from app.schemas.common import AnyResponse


router = APIRouter(prefix="/api/v1/expenses", tags=["expenses"])


@router.get("", response_model=AnyResponse)
async def list_expenses(start: date, end: date, category: str | None = None, merchant: Merchant = Depends(get_current_merchant), db: AsyncSession = Depends(get_db)):
    filters = [Expense.merchant_id == merchant.id, Expense.expense_date >= start, Expense.expense_date <= end]
    if category: filters.append(Expense.category == category)
    rows = (await db.execute(select(Expense).where(*filters).order_by(Expense.expense_date.desc()))).scalars().all()
    return {"code": 0, "data": [{"id": str(r.id), "category": r.category, "amount": float(r.amount), "description": r.description, "expense_date": r.expense_date.isoformat()} for r in rows]}


@router.post("", response_model=AnyResponse)
async def create_expense(body: dict, merchant: Merchant = Depends(get_current_merchant), db: AsyncSession = Depends(get_db)):
    e = Expense(merchant_id=merchant.id, category=body["category"], amount=Decimal(str(body["amount"])),
                description=body.get("description"), expense_date=date.fromisoformat(body["expense_date"]), payment_method=body.get("payment_method"))
    db.add(e); await db.commit(); await db.refresh(e)
    return {"code": 0, "data": {"id": str(e.id), "amount": float(e.amount)}}


@router.delete("/{expense_id}", response_model=AnyResponse)
async def delete_expense(expense_id: uuid.UUID, merchant: Merchant = Depends(get_current_merchant), db: AsyncSession = Depends(get_db)):
    e = await db.get(Expense, expense_id)
    if not e or e.merchant_id != merchant.id: raise HTTPException(status_code=404, detail="费用不存在")
    await db.delete(e); await db.commit()
    return {"code": 0, "message": "费用已删除"}


# ═══ 月度利润报表 ═══

@router.get("/monthly-report", response_model=AnyResponse)
async def monthly_report(month: str = Query(description="YYYY-MM"), merchant: Merchant = Depends(get_current_merchant), db: AsyncSession = Depends(get_db)):
    y, m = int(month[:4]), int(month[5:7])
    start = date(y, m, 1)
    if m == 12: end = date(y + 1, 1, 1)
    else: end = date(y, m + 1, 1)

    # Revenue
    revenue_row = (await db.execute(select(func.coalesce(func.sum(DailySettlement.total_sales), Decimal("0"))).where(DailySettlement.merchant_id == merchant.id, DailySettlement.date >= start, DailySettlement.date < end))).scalar() or Decimal("0")

    # Purchase cost
    purchase_row = (await db.execute(select(func.coalesce(func.sum(SupplierPayable.amount), Decimal("0"))).where(SupplierPayable.merchant_id == merchant.id, SupplierPayable.direction == "purchase", SupplierPayable.created_at >= dt.combine(start, dt.min.time()), SupplierPayable.created_at < dt.combine(end, dt.min.time())))).scalar() or Decimal("0")

    # Expenses
    expenses_row = (await db.execute(select(func.coalesce(func.sum(Expense.amount), Decimal("0"))).where(Expense.merchant_id == merchant.id, Expense.expense_date >= start, Expense.expense_date < end))).scalar() or Decimal("0")

    # By category
    expense_by_cat = (await db.execute(select(Expense.category, func.sum(Expense.amount)).where(Expense.merchant_id == merchant.id, Expense.expense_date >= start, Expense.expense_date < end).group_by(Expense.category))).all()

    gross_profit = revenue_row - purchase_row
    net_profit = gross_profit - expenses_row

    return {"code": 0, "data": {
        "month": month, "revenue": float(revenue_row), "purchase_cost": float(purchase_row),
        "gross_profit": float(gross_profit), "expenses": float(expenses_row),
        "net_profit": float(net_profit),
        "expense_breakdown": [{"category": c, "amount": float(a)} for c, a in expense_by_cat],
    }}


@router.get("/export/monthly")
async def export_monthly(month: str = Query(), merchant: Merchant = Depends(get_current_merchant), db: AsyncSession = Depends(get_db)):
    y, m = int(month[:4]), int(month[5:7])
    start = date(y, m, 1)
    end = date(y, m + 1, 1) if m < 12 else date(y + 1, 1, 1)

    # Get all expenses
    expenses = (await db.execute(select(Expense).where(Expense.merchant_id == merchant.id, Expense.expense_date >= start, Expense.expense_date < end).order_by(Expense.expense_date))).scalars().all()

    output = io.StringIO()
    w = csv.writer(output)
    w.writerow(["千摊智脑 — 月度经营报表", month])
    w.writerow([])
    w.writerow(["日期", "类别", "金额", "描述"])
    for e in expenses:
        w.writerow([e.expense_date.isoformat(), e.category, float(e.amount), e.description or ""])

    output.seek(0)
    return StreamingResponse(iter([output.getvalue()]), media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename=monthly_report_{month}.csv"})


# ═══ 发票 ═══

@router.get("/invoices", response_model=AnyResponse)
async def list_invoices(merchant: Merchant = Depends(get_current_merchant), db: AsyncSession = Depends(get_db)):
    rows = (await db.execute(select(Invoice).where(Invoice.merchant_id == merchant.id).order_by(Invoice.invoice_date.desc()).limit(50))).scalars().all()
    return {"code": 0, "data": [{"id": str(i.id), "invoice_number": i.invoice_number, "supplier_name": i.supplier_name, "amount": float(i.amount), "tax_amount": float(i.tax_amount) if i.tax_amount else None, "invoice_date": i.invoice_date.isoformat()} for i in rows]}


@router.post("/invoices", response_model=AnyResponse)
async def create_invoice(body: dict, merchant: Merchant = Depends(get_current_merchant), db: AsyncSession = Depends(get_db)):
    i = Invoice(merchant_id=merchant.id, invoice_number=body["invoice_number"], invoice_type=body.get("invoice_type", "electronic"),
                supplier_name=body.get("supplier_name"), amount=Decimal(str(body["amount"])), tax_amount=Decimal(str(body["tax_amount"])) if body.get("tax_amount") else None,
                invoice_date=date.fromisoformat(body["invoice_date"]), file_url=body.get("file_url"), notes=body.get("notes"))
    db.add(i); await db.commit()
    return {"code": 0, "data": {"id": str(i.id), "invoice_number": i.invoice_number}}
