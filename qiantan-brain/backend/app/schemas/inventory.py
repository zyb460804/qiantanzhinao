"""Inventory 路由的 Pydantic 响应模型。"""

from __future__ import annotations

from pydantic import BaseModel, Field, field_validator

from app.schemas.common import ApiResponse, DecimalNum, PaginatedResponse


# RA-11：offline-sync 事件类型白名单 —— 仅允许四类业务事件（大小写归一后校验）。
# 此前 "Sale"/"mystery_type" 等任意值原样落库，绕过符号归一与批次消耗分支
# （流水 +qty 不扣批次 → /current 与批次余量双向分歧）。非法值在 schema 层
# 422 拒绝并列出合法值；合法值统一小写归一，保证下游精确匹配。
OFFLINE_EVENT_TYPES = ("sale", "purchase", "refund", "waste")


class CurrentInventoryItem(BaseModel):
    product_id: int
    product_name: str
    total_qty: DecimalNum
    unit: str
    sku_id: str | None = None
    sku_name: str | None = None


class InventoryHistoryItem(BaseModel):
    id: str
    product_id: int
    product_name: str | None = None
    sku_id: str | None = None
    quantity: DecimalNum
    unit: str
    unit_cost: DecimalNum | None = None
    unit_price: DecimalNum | None = None
    total_amount: DecimalNum | None = None
    event_type: str
    event_time: str | None = None
    is_voided: bool = False
    source: str | None = None


class AlertItem(BaseModel):
    product_id: int
    product_name: str
    type: str
    message: str
    current_qty: DecimalNum
    threshold: DecimalNum | None = None


class VoidResult(BaseModel):
    record_id: str
    batch_summary: list | None = None


class StocktakeStartData(BaseModel):
    session_id: str
    items: list[dict]


class StocktakeSubmitData(BaseModel):
    item_id: str
    product_id: int
    product_name: str
    book_qty: DecimalNum
    actual_qty: DecimalNum = Field(ge=0)
    diff: DecimalNum
    diff_reason: str | None = None


class StocktakeCompleteData(BaseModel):
    session_id: str
    status: str
    total_book: DecimalNum
    total_actual: DecimalNum
    total_diff: DecimalNum
    waste_amount: DecimalNum | None = None
    items: list[dict]


class StocktakeSessionItem(BaseModel):
    id: str
    status: str
    created_at: str | None = None
    completed_at: str | None = None
    item_count: int | None = None


class OfflineSyncData(BaseModel):
    accepted: int
    rejected: int
    details: list[dict]


# ── 请求模型 ──────────────────────────────────────────────


class VoidRequest(BaseModel):
    reason: str = ""


class StocktakeSubmitRequest(BaseModel):
    product_id: int
    actual_qty: DecimalNum = Field(ge=0)
    unit: str | None = None
    diff_reason: str | None = None
    variance_reason: str | None = None


class StocktakeCompleteRequest(BaseModel):
    notes: str | None = None


class OfflineSyncItem(BaseModel):
    idempotency_key: str
    event_type: str = "sale"
    product_id: int | None = None
    product_name: str | None = None
    quantity: DecimalNum | None = None
    unit: str = "斤"
    unit_cost: DecimalNum | None = None
    unit_price: DecimalNum | None = None
    total_amount: DecimalNum | None = None
    event_time: str | None = None
    notes: str = ""
    source: str = "offline"
    client_id: str | None = None
    client_reference: str | None = None

    @field_validator("event_type", mode="before")
    @classmethod
    def _normalize_event_type(cls, v):
        # 大小写归一（"Sale" → "sale"）；非字符串交给类型校验报错。
        if isinstance(v, str):
            return v.strip().lower()
        return v

    @field_validator("event_type")
    @classmethod
    def _validate_event_type(cls, v):
        if v not in OFFLINE_EVENT_TYPES:
            allowed = " / ".join(OFFLINE_EVENT_TYPES)
            raise ValueError(f"event_type 必须为 {allowed} 之一")
        return v


class OfflineSyncRequest(BaseModel):
    items: list[OfflineSyncItem]


# ── 响应信封 ─────────────────────────────────────────────

CurrentInventoryResponse = ApiResponse[list[CurrentInventoryItem]]
HistoryResponse = PaginatedResponse[InventoryHistoryItem]
AlertsResponse = ApiResponse[list[AlertItem]]
VoidResponse = ApiResponse[VoidResult]
StocktakeStartResponse = ApiResponse[StocktakeStartData]
StocktakeSubmitResponse = ApiResponse[StocktakeSubmitData]
StocktakeCompleteResponse = ApiResponse[StocktakeCompleteData]
StocktakeHistoryResponse = ApiResponse[list[StocktakeSessionItem]]
OfflineSyncResponse = ApiResponse[OfflineSyncData]
