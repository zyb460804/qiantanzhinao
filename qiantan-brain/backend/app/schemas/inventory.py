"""Inventory 路由的 Pydantic 响应模型。"""

from __future__ import annotations

from pydantic import BaseModel

from app.schemas.common import ApiResponse, PaginatedResponse


class CurrentInventoryItem(BaseModel):
    product_id: int
    product_name: str
    total_qty: float
    unit: str
    sku_id: str | None = None
    sku_name: str | None = None


class InventoryHistoryItem(BaseModel):
    id: str
    product_id: int
    product_name: str | None = None
    sku_id: str | None = None
    quantity: float
    unit: str
    unit_cost: float | None = None
    unit_price: float | None = None
    total_amount: float | None = None
    event_type: str
    event_time: str | None = None
    is_voided: bool = False
    source: str | None = None


class AlertItem(BaseModel):
    product_id: int
    product_name: str
    type: str
    message: str
    current_qty: float
    threshold: float | None = None


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
    book_qty: float
    actual_qty: float
    diff: float
    diff_reason: str | None = None


class StocktakeCompleteData(BaseModel):
    session_id: str
    status: str
    total_book: float
    total_actual: float
    total_diff: float
    waste_amount: float | None = None
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
    actual_qty: float
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
    quantity: float | None = None
    unit: str = "斤"
    unit_cost: float | None = None
    unit_price: float | None = None
    total_amount: float | None = None
    event_time: str | None = None
    notes: str = ""
    source: str = "offline"
    client_id: str | None = None
    client_reference: str | None = None


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
