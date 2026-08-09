"""Accounts 路由的 Pydantic 响应模型。"""

from __future__ import annotations

from pydantic import BaseModel

from app.schemas.common import ApiResponse, DecimalNum


class SupplierBalanceRow(BaseModel):
    supplier_id: str
    supplier_name: str | None = None
    balance: DecimalNum

    model_config = {"from_attributes": True}


class SupplierBalanceList(BaseModel):
    items: list[SupplierBalanceRow]
    total_balance: DecimalNum


class SupplierBalanceDetail(BaseModel):
    supplier_id: str
    balance: DecimalNum


class CustomerBalanceRow(BaseModel):
    customer_name: str
    balance: DecimalNum

    model_config = {"from_attributes": True}


class CustomerBalanceList(BaseModel):
    items: list[CustomerBalanceRow]
    total_balance: DecimalNum


class CustomerBalanceDetail(BaseModel):
    customer_name: str
    balance: DecimalNum


# ── 响应信封类型别名 ──────────────────────────────────────

SupplierBalanceListResponse = ApiResponse[SupplierBalanceList]
SupplierBalanceDetailResponse = ApiResponse[SupplierBalanceDetail]
CustomerBalanceListResponse = ApiResponse[CustomerBalanceList]
CustomerBalanceDetailResponse = ApiResponse[CustomerBalanceDetail]
