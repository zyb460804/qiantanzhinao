"""Voice 路由的 Pydantic 请求/响应模型。"""

from __future__ import annotations

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field

from app.schemas.common import ApiResponse, DecimalNum, PaginatedResponse


# ── 请求模型 ──────────────────────────────────────────────


class VoiceConfirmRequest(BaseModel):
    voice_log_id: UUID


class VoiceCorrection(BaseModel):
    """用户对解析结果的修正项 —— 白名单字段，每个加范围约束。

    修复 C-1：原 ``corrections: dict`` 无白名单，可注入任意字段
    （如 event_type、merchant_id、is_admin 等）。强类型化后只能传
    业务允许的字段，且数值字段都要求 >= 0。

    修复 L5（白名单过窄回归）：confirm_voice 下游从 parsed_event 读取
    event_type / party_name / is_credit / is_repay 来决定赊账/扣库存方向。
    原强类型化遗漏这四个字段，导致用户无法修正 ASR 把"卖"听成"买"等语义
    错误（库存方向不可逆）。这里加回，但仍用 Literal/长度约束收紧。
    """

    product: str | None = None
    quantity: DecimalNum | None = Field(None, ge=0)
    unit: str | None = None
    # 金额上限与 SKU 售价口径一致（pos.py 同款 le=1000000），防误录天文数字
    unit_cost: DecimalNum | None = Field(None, ge=0, le=1000000)
    unit_price: DecimalNum | None = Field(None, ge=0, le=1000000)
    total_amount: DecimalNum | None = Field(None, ge=0, le=1000000)
    # 业务语义字段（confirm 下游依赖）；expense：经营支出（摊位费/房租/水电等），
    # 确认后落 expenses 表，不产生库存/销售/成本流水
    event_type: Literal["purchase", "sale", "waste", "expense"] | None = None
    party_name: str | None = Field(None, max_length=50)
    is_credit: bool | None = None
    is_repay: bool | None = None

    model_config = {"extra": "forbid"}  # 拒绝未知字段，防注入


class VoiceCorrectRequest(BaseModel):
    voice_log_id: UUID
    corrections: VoiceCorrection


class VoiceParseTextRequest(BaseModel):
    text: str
    client_id: str | None = None


class VoiceVoidRequest(BaseModel):
    reason: str = ""


class VoiceEditRequest(BaseModel):
    product: str | None = None
    quantity: DecimalNum | None = None
    unit: str | None = None
    unit_cost: DecimalNum | None = None
    unit_price: DecimalNum | None = None
    total_amount: DecimalNum | None = None
    reason: str = "修改已确认记录"


# ── 响应数据模型 ──────────────────────────────────────────


class VoiceUploadData(BaseModel):
    voice_log_id: str
    asr_text: str
    parsed: dict | None = None
    # 多意图契约（小程序端按 events 渲染多卡片）：event=events[0] 兼容别名，
    # events 为全部识别结果，>1 笔时 warning 提示「检测到N笔，仅返回第1笔」。
    # 每笔事件独立成单：events[i] 内嵌各自的 voice_log_id（逐卡 confirm 用），
    # 顶层 voice_log_id 恒等于 events[0] 的（旧客户端兼容）。
    event: dict | None = None
    events: list[dict] = Field(default_factory=list)
    warning: str | None = None


class VoiceParseTextData(BaseModel):
    voice_log_id: str
    asr_text: str
    parsed: dict | None = None
    # 同 VoiceUploadData：parsed/event 均为 events[0]，字段名与小程序端契约一致；
    # events[i] 内嵌各自的 voice_log_id，多笔各自 confirm 互不被幂等误伤。
    # 事件 dict 额外携带解析器透传的可选字段：time_hint（「昨天/上午」等时间词，
    # 仅展示用）、expense_category（expense 事件的费用归口）。
    event: dict | None = None
    events: list[dict] = Field(default_factory=list)
    warning: str | None = None


class VoiceTodayCountData(BaseModel):
    today_count: int


class VoiceLogItem(BaseModel):
    id: str
    merchant_id: str
    audio_url: str | None = None
    asr_text: str | None = None
    parsed_event: dict | None = None
    status: str
    # 便捷金额（自 parsed_event 提取），供「最近说过」列表直接展示
    total_amount: float | None = None
    created_at: str | None = None


class VoiceCorrectData(BaseModel):
    voice_log_id: str
    parsed: dict | None = None


class VoiceConfirmData(BaseModel):
    voice_log_id: str
    event_type: str
    product: str
    product_id: int | None = None
    quantity: DecimalNum
    unit: str
    total_amount: DecimalNum
    # FIFO 实际消耗数量（Decimal）；原 list 声明与路由返回值不符，
    # 销售确认一旦真正扣批次就会触发 ResponseValidationError。
    consumed_from_batches: DecimalNum | None = None
    idempotent: bool = False


class VoiceVoidData(BaseModel):
    voice_log_id: str
    record_id: str | None = None
    # rollback_batch_on_void 返回的是 summary dict（BatchRollbackSummary），
    # 原声明 list 与实际返回不符，撤销必触发 ResponseValidationError。
    batch_summary: dict | None = None


class VoiceEditData(BaseModel):
    voice_log_id: str
    old_record_id: str
    new_record_id: str
    product: str
    quantity: DecimalNum
    unit: str
    consumed_from_batches: DecimalNum | None = None


# ── 响应信封类型别名 ──────────────────────────────────────

VoiceUploadResponse = ApiResponse[VoiceUploadData]
VoiceParseTextResponse = ApiResponse[VoiceParseTextData]
VoiceTodayCountResponse = ApiResponse[VoiceTodayCountData]
VoiceLogsResponse = PaginatedResponse[VoiceLogItem]
VoiceCorrectResponse = ApiResponse[VoiceCorrectData]
VoiceConfirmResponse = ApiResponse[VoiceConfirmData]
VoiceVoidResponse = ApiResponse[VoiceVoidData]
VoiceEditResponse = ApiResponse[VoiceEditData]
