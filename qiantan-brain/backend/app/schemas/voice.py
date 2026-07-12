"""Voice 路由的 Pydantic 请求/响应模型。"""

from __future__ import annotations

from uuid import UUID

from pydantic import BaseModel

from app.schemas.common import ApiResponse, PaginatedResponse


# ── 请求模型 ──────────────────────────────────────────────


class VoiceConfirmRequest(BaseModel):
    voice_log_id: UUID


class VoiceCorrectRequest(BaseModel):
    voice_log_id: UUID
    corrections: dict


class VoiceParseTextRequest(BaseModel):
    text: str
    client_id: str | None = None


class VoiceVoidRequest(BaseModel):
    reason: str = ""


class VoiceEditRequest(BaseModel):
    product: str | None = None
    quantity: float | None = None
    unit: str | None = None
    unit_cost: float | None = None
    unit_price: float | None = None
    total_amount: float | None = None
    reason: str = "修改已确认记录"


# ── 响应数据模型 ──────────────────────────────────────────


class VoiceUploadData(BaseModel):
    voice_log_id: str
    asr_text: str
    parsed: dict | None = None


class VoiceParseTextData(BaseModel):
    voice_log_id: str
    asr_text: str
    parsed: dict | None = None


class VoiceTodayCountData(BaseModel):
    today_count: int


class VoiceLogItem(BaseModel):
    id: str
    merchant_id: str
    audio_url: str | None = None
    asr_text: str | None = None
    parsed_event: dict | None = None
    status: str
    created_at: str | None = None


class VoiceCorrectData(BaseModel):
    voice_log_id: str
    parsed: dict | None = None


class VoiceConfirmData(BaseModel):
    voice_log_id: str
    event_type: str
    product: str
    product_id: int | None = None
    quantity: float
    unit: str
    total_amount: float
    consumed_from_batches: list | None = None
    idempotent: bool = False


class VoiceVoidData(BaseModel):
    voice_log_id: str
    record_id: str | None = None
    batch_summary: list | None = None


class VoiceEditData(BaseModel):
    voice_log_id: str
    old_record_id: str
    new_record_id: str
    product: str
    quantity: float
    unit: str
    consumed_from_batches: list | None = None


# ── 响应信封类型别名 ──────────────────────────────────────

VoiceUploadResponse = ApiResponse[VoiceUploadData]
VoiceParseTextResponse = ApiResponse[VoiceParseTextData]
VoiceTodayCountResponse = ApiResponse[VoiceTodayCountData]
VoiceLogsResponse = PaginatedResponse[VoiceLogItem]
VoiceCorrectResponse = ApiResponse[VoiceCorrectData]
VoiceConfirmResponse = ApiResponse[VoiceConfirmData]
VoiceVoidResponse = ApiResponse[VoiceVoidData]
VoiceEditResponse = ApiResponse[VoiceEditData]
