"""往来账 API — 供应商应付 / 客户应收 / 供应商付款 / 对账单。

写流水由业务服务在采购确认、语音记账、供应商付款时自动产生。
余额由流水聚合得到，不维护可变字段（见 models/accounts.py）。
"""

from __future__ import annotations

import uuid
from decimal import Decimal

from fastapi import APIRouter, Body, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import get_current_merchant
from app.database import get_db
from app.models.audit import AuditLog
from app.models.catalog import Supplier
from app.models.merchant import Merchant
from app.schemas.accounts import (
    CustomerBalanceDetail,
    CustomerBalanceDetailResponse,
    CustomerBalanceList,
    CustomerBalanceListResponse,
    CustomerBalanceRow,
    SupplierBalanceDetail,
    SupplierBalanceDetailResponse,
    SupplierBalanceList,
    SupplierBalanceListResponse,
    SupplierBalanceRow,
)
from app.schemas.common import AnyResponse
from app.services.accounts_service import (
    get_customer_balance,
    get_supplier_balance,
    get_supplier_statement,
    list_customer_balances,
    list_supplier_balances,
    record_supplier_payment,
)


router = APIRouter(prefix="/api/v1/accounts", tags=["accounts"])


@router.get("/supplier-balance", response_model=SupplierBalanceListResponse)
async def supplier_balance_summary(
    merchant: Merchant = Depends(get_current_merchant),
    db: AsyncSession = Depends(get_db),
):
    """Return all supplier balances for current merchant."""
    rows = await list_supplier_balances(db, merchant.id)
    items = [
        SupplierBalanceRow(
            supplier_id=str(r["supplier_id"]),
            supplier_name=r.get("supplier_name"),
            balance=float(r["balance"]),
        )
        for r in rows
    ]
    total = sum(float(r["balance"]) for r in rows)
    return SupplierBalanceListResponse(
        code=0,
        data=SupplierBalanceList(items=items, total_balance=total),
    )


@router.get("/supplier-balance/{supplier_id}", response_model=SupplierBalanceDetailResponse)
async def supplier_balance_detail(
    supplier_id: uuid.UUID,
    merchant: Merchant = Depends(get_current_merchant),
    db: AsyncSession = Depends(get_db),
):
    """Return balance for a specific supplier."""
    balance = await get_supplier_balance(db, merchant.id, supplier_id)
    return SupplierBalanceDetailResponse(
        code=0,
        data=SupplierBalanceDetail(
            supplier_id=str(supplier_id),
            balance=float(balance),
        ),
    )


@router.get("/customer-balance", response_model=CustomerBalanceListResponse)
async def customer_balance_summary(
    merchant: Merchant = Depends(get_current_merchant),
    db: AsyncSession = Depends(get_db),
):
    """Return all customer receivable balances for current merchant."""
    rows = await list_customer_balances(db, merchant.id)
    items = [
        CustomerBalanceRow(customer_name=r["customer_name"], balance=float(r["balance"]))
        for r in rows
    ]
    total = sum(float(r["balance"]) for r in rows)
    return CustomerBalanceListResponse(
        code=0,
        data=CustomerBalanceList(items=items, total_balance=total),
    )


@router.get("/customer-balance/{customer_name}", response_model=CustomerBalanceDetailResponse)
async def customer_balance_detail(
    customer_name: str,
    merchant: Merchant = Depends(get_current_merchant),
    db: AsyncSession = Depends(get_db),
):
    """Return balance for a specific customer."""
    balance = await get_customer_balance(db, merchant.id, customer_name)
    return CustomerBalanceDetailResponse(
        code=0,
        data=CustomerBalanceDetail(customer_name=customer_name, balance=float(balance)),
    )


# ---------------------------------------------------------------------------
# 供应商付款（阶段A）
# ---------------------------------------------------------------------------


@router.post("/supplier-payment", response_model=AnyResponse)
async def pay_supplier(
    body: dict = Body(...),
    merchant: Merchant = Depends(get_current_merchant),
    db: AsyncSession = Depends(get_db),
):
    """Record a payment to a supplier, reducing the outstanding payable."""
    supplier_id = uuid.UUID(body["supplier_id"])
    amount = Decimal(str(body["amount"]))
    method = body.get("method", "cash")
    note = body.get("note")
    idempotency_key = body.get("idempotency_key")
    try:
        payable_ids = [uuid.UUID(value) for value in body.get("payable_ids", [])]
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail="应付账款ID格式错误") from exc
    if not payable_ids:
        raise HTTPException(status_code=400, detail="付款必须选择应付账款")

    supplier = await db.get(Supplier, supplier_id)
    if not supplier or supplier.merchant_id != merchant.id:
        raise HTTPException(status_code=404, detail="供应商不存在")

    try:
        payment = await record_supplier_payment(
            db,
            merchant_id=merchant.id,
            supplier_id=supplier_id,
            payable_ids=payable_ids,
            amount=amount,
            note=note or f"{method}付款",
            idempotency_key=idempotency_key,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    db.add(
        AuditLog(
            merchant_id=merchant.id,
            action="supplier_payment",
            target_table="supplier_payables",
            target_id=str(payment.id),
            after_data={"supplier_id": str(supplier_id), "amount": float(amount), "method": method},
            reason=note,
            operator="merchant",
        )
    )
    await db.commit()

    new_balance = await get_supplier_balance(db, merchant.id, supplier_id)

    return {
        "code": 0,
        "message": f"已向供应商付款 ¥{float(amount)}",
        "data": {
            "payment_id": str(payment.id),
            "supplier_id": str(supplier_id),
            "supplier_name": supplier.name,
            "amount": float(amount),
            "method": method,
            "new_balance": float(new_balance),
        },
    }


@router.get("/supplier/{supplier_id}/statement", response_model=AnyResponse)
async def supplier_statement(
    supplier_id: uuid.UUID,
    limit: int = 50,
    merchant: Merchant = Depends(get_current_merchant),
    db: AsyncSession = Depends(get_db),
):
    """Get supplier statement (all purchase/payment/return transactions)."""
    supplier = await db.get(Supplier, supplier_id)
    if not supplier or supplier.merchant_id != merchant.id:
        raise HTTPException(status_code=404, detail="供应商不存在")

    statement = await get_supplier_statement(db, merchant.id, supplier_id, limit=limit)
    return {"code": 0, "data": statement}
