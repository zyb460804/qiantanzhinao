"""Purchase schemas — 采购验收 / 供应商付款 / 退货."""

from __future__ import annotations

import uuid
from typing import Literal

from pydantic import BaseModel, Field, model_validator

from app.schemas.common import ApiResponse


# ---------------------------------------------------------------------------
# 到货验收
# ---------------------------------------------------------------------------


class AcceptanceItemRequest(BaseModel):
    """单条验收明细。"""

    item_id: uuid.UUID
    arrival_qty: float = Field(ge=0)  # 实际到货
    accepted_qty: float = Field(ge=0)  # 合格可入库
    shortage_qty: float = Field(default=0, ge=0)
    damaged_qty: float = Field(default=0, ge=0)
    rejected_qty: float = Field(default=0, ge=0)
    returned_qty: float = Field(default=0, ge=0)
    replenish_qty: float = Field(default=0, ge=0)
    package_count: int | None = Field(default=None, ge=0)
    gross_weight: float | None = Field(default=None, ge=0)
    tare_weight: float | None = Field(default=None, ge=0)
    net_weight: float | None = Field(default=None, ge=0)
    actual_unit_cost: float | None = Field(default=None, ge=0)  # 实际单价
    quality_ok: bool = True
    acceptance_photos: str | None = Field(default=None, max_length=2000)
    certificates: str | None = Field(default=None, max_length=2000)
    acceptance_notes: str | None = Field(default=None, max_length=500)

    @model_validator(mode="after")
    def validate_acceptance_quantities(self):
        if self.accepted_qty > self.arrival_qty:
            raise ValueError("合格数量不能大于到货数量")
        for value, label in (
            (self.damaged_qty, "破损数量"),
            (self.rejected_qty, "拒收数量"),
            (self.returned_qty, "退回数量"),
        ):
            if value > self.arrival_qty:
                raise ValueError(f"{label}不能大于到货数量")
        if (
            self.gross_weight is not None
            and self.tare_weight is not None
            and self.tare_weight > self.gross_weight
        ):
            raise ValueError("皮重不能大于毛重")
        if (
            self.gross_weight is not None
            and self.net_weight is not None
            and self.net_weight > self.gross_weight
        ):
            raise ValueError("净重不能大于毛重")
        return self


class RecordAcceptanceRequest(BaseModel):
    """记录到货验收。"""

    items: list[AcceptanceItemRequest] = Field(min_length=1, max_length=100)
    notes: str | None = Field(default=None, max_length=500)


class ConfirmAcceptanceRequest(BaseModel):
    """确认验收 → 批次入库 + 库存流水 + 供应商应付。"""

    notes: str | None = Field(default=None, max_length=500)


class PurchaseItemUpdateRequest(BaseModel):
    actual_qty: float | None = Field(default=None, ge=0, le=1000000)
    actual_unit_cost: float | None = Field(default=None, ge=0, le=10000000)
    supplier_id: uuid.UUID | None = None


# ---------------------------------------------------------------------------
# 供应商付款
# ---------------------------------------------------------------------------


class SupplierPaymentRequest(BaseModel):
    supplier_id: uuid.UUID
    payable_ids: list[uuid.UUID] = Field(min_length=1, max_length=100)
    amount: float = Field(gt=0, le=10000000)
    method: Literal["cash", "wechat", "alipay", "bank_transfer"] = "cash"
    note: str | None = Field(default=None, max_length=500)
    idempotency_key: str | None = Field(default=None, max_length=64)


# ---------------------------------------------------------------------------
# 采购退货
# ---------------------------------------------------------------------------


class PurchaseReturnRequest(BaseModel):
    """退货给供应商。"""

    item_id: uuid.UUID
    return_qty: float = Field(gt=0)
    reason: str = Field(min_length=1, max_length=500)
    offset_payable: bool = True  # 是否抵扣应付


# ---------------------------------------------------------------------------
# 供应商对账单
# ---------------------------------------------------------------------------


class SupplierStatementItem(BaseModel):
    id: str
    direction: str  # purchase / payment / return
    amount: float
    note: str | None = None
    created_at: str | None = None


class SupplierStatementData(BaseModel):
    supplier_id: str
    supplier_name: str | None = None
    total_purchases: float
    total_payments: float
    total_returns: float
    current_balance: float
    items: list[SupplierStatementItem]


# ---------------------------------------------------------------------------
# 响应数据
# ---------------------------------------------------------------------------


class PurchaseItemData(BaseModel):
    item_id: str
    product_id: int
    product_name: str
    recommended_qty: float | None = None
    actual_qty: float
    unit: str
    estimated_unit_cost: float | None = None
    actual_unit_cost: float | None = None
    estimated_cost: float | None = None
    actual_cost: float | None = None
    deviation_ratio: float | None = None
    status: str
    # 验收字段
    arrival_qty: float | None = None
    accepted_qty: float | None = None
    shortage_qty: float | None = None
    damaged_qty: float | None = None
    rejected_qty: float | None = None
    returned_qty: float | None = None
    package_count: int | None = None
    net_weight: float | None = None
    quality_ok: bool | None = None


class PurchaseListData(BaseModel):
    list_id: str
    status: str
    total_estimated_cost: float
    total_actual_cost: float | None = None
    item_count: int
    payment_status: str | None = None
    paid_amount: float | None = None
    created_at: str | None = None
    confirmed_at: str | None = None
    accepted_at: str | None = None
    items: list[PurchaseItemData]


class PurchaseCreateData(BaseModel):
    list_id: str
    status: str
    total_estimated_cost: float
    item_count: int


class AcceptanceResult(BaseModel):
    list_id: str
    status: str
    items_processed: int
    total_accepted_qty: float
    total_shortage: float
    total_damaged: float
    total_rejected: float


class PurchaseConfirmData(BaseModel):
    list_id: str
    status: str
    confirmed_count: int
    total_actual_cost: float
    records: list[dict]


class SupplierPaymentResult(BaseModel):
    payment_id: str
    supplier_id: str
    amount: float
    method: str
    new_balance: float


class PurchaseReturnResult(BaseModel):
    item_id: str
    return_qty: float
    reason: str
    offset_payable: bool
    new_item_status: str


# ── 响应信封 ─────────────────────────────────────────────

PurchaseCreateResponse = ApiResponse[PurchaseCreateData]
PurchaseTodayResponse = ApiResponse[PurchaseListData]
PurchaseUpdateItemResponse = ApiResponse[PurchaseItemData]
PurchaseDeleteItemResponse = ApiResponse[dict]
AcceptanceResponse = ApiResponse[AcceptanceResult]
PurchaseConfirmResponse = ApiResponse[PurchaseConfirmData]
PurchaseCancelResponse = ApiResponse[dict]
SupplierPaymentResponse = ApiResponse[SupplierPaymentResult]
SupplierStatementResponse = ApiResponse[SupplierStatementData]
PurchaseReturnResponse = ApiResponse[PurchaseReturnResult]
