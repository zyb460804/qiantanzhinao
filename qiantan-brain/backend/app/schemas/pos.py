"""POS request/response schemas — 组合支付 / 退款退货 / 挂单取单."""

from __future__ import annotations

import uuid
from typing import Literal

from pydantic import BaseModel, Field, model_validator

from app.schemas.common import ApiResponse


PaymentMethod = Literal["cash", "wechat", "alipay", "card", "credit"]


# ---------------------------------------------------------------------------
# 组合支付
# ---------------------------------------------------------------------------


class PaymentItem(BaseModel):
    """单笔支付项 — 组合支付时一笔订单可拆成多笔。"""

    method: PaymentMethod
    amount: float = Field(gt=0, le=1000000)


class CreateSaleOrderItem(BaseModel):
    product_id: int
    sku_id: uuid.UUID | None = None
    quantity: float = Field(gt=0, le=100000)
    unit_price: float | None = Field(default=None, gt=0)
    unit: str = Field(default="斤", min_length=1, max_length=20)


class CreateSaleOrderRequest(BaseModel):
    items: list[CreateSaleOrderItem] = Field(min_length=1, max_length=100)
    payment_method: PaymentMethod = "cash"  # 保留兼容：单一支付方式
    payments: list[PaymentItem] | None = Field(
        default=None, max_length=10
    )  # 新：组合支付（优先于 payment_method）
    discount_amount: float = Field(default=0, ge=0)
    customer_name: str | None = Field(default=None, max_length=100)
    client_id: str | None = Field(default=None, min_length=8, max_length=64)
    note: str | None = Field(default=None, max_length=500)

    @model_validator(mode="after")
    def validate_payments(self):
        # 组合支付模式下，每笔 credit 都需要客户名
        payments = self.payments or []
        has_credit = any(p.method == "credit" for p in payments)
        if has_credit or self.payment_method == "credit":
            if not (self.customer_name or "").strip():
                raise ValueError("赊账订单必须填写客户名称")
        return self


class PaySaleOrderRequest(BaseModel):
    amount: float = Field(gt=0)
    method: Literal["cash", "wechat", "alipay", "card"] = "cash"
    payments: list[PaymentItem] | None = Field(
        default=None, max_length=10
    )  # 组合支付（优先于 method）
    transaction_id: str | None = Field(default=None, max_length=64)
    note: str | None = Field(default=None, max_length=500)


# ---------------------------------------------------------------------------
# 退款 / 退货
# ---------------------------------------------------------------------------


class RefundItemRequest(BaseModel):
    """单品退款行。不传 items 则整单退款。"""

    item_id: uuid.UUID
    quantity: float = Field(gt=0)  # 退款数量，不能超过原购买量
    return_to_stock: bool = True  # 是否退回可售库存


class RefundOrderRequest(BaseModel):
    reason: str = Field(min_length=1, max_length=500)
    items: list[RefundItemRequest] | None = Field(default=None, max_length=100)  # None = 整单退款
    return_to_stock: bool = True  # 整单退款时是否全部退回库存


class RefundResultItem(BaseModel):
    item_id: str
    product_name: str
    original_qty: float
    refund_qty: float
    refund_amount: float
    returned_to_stock: bool


class RefundResult(BaseModel):
    order_id: str
    order_no: str
    refunded_amount: float
    remaining_amount: float
    new_status: str
    items: list[RefundResultItem]


# ---------------------------------------------------------------------------
# 挂单 / 取单
# ---------------------------------------------------------------------------


class HoldOrderRequest(BaseModel):
    items: list[CreateSaleOrderItem] = Field(min_length=1, max_length=100)
    discount_amount: float = Field(default=0, ge=0)
    customer_name: str | None = Field(default=None, max_length=100)
    client_id: str | None = Field(default=None, min_length=8, max_length=64)
    note: str | None = Field(default=None, max_length=500)


class ResumeHeldOrderRequest(BaseModel):
    payment_method: PaymentMethod = "cash"
    payments: list[PaymentItem] | None = Field(default=None, max_length=10)
    customer_name: str | None = Field(default=None, max_length=100)
    discount_amount: float | None = Field(default=None, ge=0)
    note: str | None = Field(default=None, max_length=500)


class HeldOrderSummary(BaseModel):
    order_id: str
    order_no: str
    item_count: int
    total_amount: float
    customer_name: str | None = None
    held_at: str | None = None


# ---------------------------------------------------------------------------
# 响应
# ---------------------------------------------------------------------------


class SaleOrderItemData(BaseModel):
    product_id: int
    product_name: str
    quantity: float
    unit_price: float
    line_total: float


class SaleOrderData(BaseModel):
    order_id: str
    order_no: str
    status: str
    total_amount: float
    paid_amount: float
    refunded_amount: float | None = None
    item_count: int
    items: list[dict] | None = None
    created_at: str | None = None


class PaymentData(BaseModel):
    payment_id: str
    order_id: str
    amount: float
    method: str
    paid_at: str | None = None


class DailySettlementData(BaseModel):
    settle_date: str
    status: str
    total_orders: int
    total_revenue: float
    total_cost: float
    gross_profit: float
    payments: list[dict] | None = None
    reconciliation: dict | None = None


class OrderListItem(BaseModel):
    id: str
    order_no: str
    status: str
    total_amount: float
    created_at: str | None = None


CreateOrderResponse = ApiResponse[SaleOrderData]
ListOrdersResponse = ApiResponse[list[OrderListItem]]
PayOrderResponse = ApiResponse[PaymentData]
RefundOrderResponse = ApiResponse[RefundResult]
HoldOrderResponse = ApiResponse[SaleOrderData]
ListHeldOrdersResponse = ApiResponse[list[HeldOrderSummary]]
CloseSettlementResponse = ApiResponse[DailySettlementData]
GetSettlementResponse = ApiResponse[DailySettlementData]
