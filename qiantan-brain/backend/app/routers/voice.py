"""Voice accounting API router — core Phase 1 module."""

import json
import logging
import uuid
from decimal import Decimal
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import flag_modified

from app.config import settings
from app.core.security import get_current_merchant, get_merchant_id
from app.core.timezone import utc_now, utc_today_start
from app.database import get_db
from app.models.audit import AuditLog
from app.models.batch import BatchLifecycle
from app.models.catalog import ProductAlias, ProductSKU
from app.models.inventory import InventoryRecord
from app.models.merchant import Merchant
from app.models.product import ProductCategory
from app.models.voice import VoiceLog
from app.routers.staff import require_permission
from app.schemas.voice import (
    VoiceConfirmRequest,
    VoiceConfirmResponse,
    VoiceCorrectRequest,
    VoiceCorrectResponse,
    VoiceEditRequest,
    VoiceEditResponse,
    VoiceLogsResponse,
    VoiceParseTextRequest,
    VoiceParseTextResponse,
    VoiceTodayCountResponse,
    VoiceUploadResponse,
    VoiceVoidRequest,
    VoiceVoidResponse,
)
from app.services import asr_iflytek
from app.services.accounts_service import record_customer_receivable
from app.services.batch import consume_batches_fifo, create_batch, rollback_batch_on_void
from app.services.sku_service import resolve_sku_id
from app.services.unit_conversion import convert_to_base_unit
from app.services.voice_ledger import sync_voice_receivables, void_voice_confirmed_record
from app.services.voice_parser import parse_voice_events


logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/voice", tags=["voice"])

_RULES_DIR = Path(__file__).parent.parent / "rules"

# 修复 C-9：音频上传大小/类型上限（参照 vision.py 的 _MAX_IMAGE_SIZE 模式）。
_MAX_AUDIO_SIZE = 25 * 1024 * 1024
_AUDIO_EXT_WHITELIST = {".wav", ".mp3", ".m4a", ".amr", ".aac", ".ogg", ".opus"}


def _load_product_names() -> list[str]:
    config_path = _RULES_DIR / "product_categories.json"
    if config_path.exists():
        with open(config_path, encoding="utf-8") as f:
            data = json.load(f)
        return data.get("product_names", [])
    return ["白菜", "土豆", "豆腐", "猪肉"]


def _json_safe(value):
    """JSON 列不能存 Decimal：parsed_event 里的数值统一 float 化。

    旧代码往 parsed_event 塞 Decimal 却因 JSON 列原地变更不被追踪而从未
    真正落库；flag_modified 修复后 flush 会真实序列化，必须先转 float。
    """
    if isinstance(value, Decimal):
        return float(value)
    return value


async def _lookup_product(db: AsyncSession, name: str) -> int | None:
    """Look up product_id by name. Returns None if not found."""
    if not name:
        return None
    query = select(ProductCategory.id).where(ProductCategory.name == name)
    result = await db.execute(query)
    pid = result.scalar_one_or_none()
    return pid


async def _load_sku_terms(db: AsyncSession, merchant_id: uuid.UUID) -> list[str]:
    """本商户 SKU 名称 + 别名，并入解析器词表，让商户自有商品可被识别。"""
    sku_names = (
        (
            await db.execute(
                select(ProductSKU.name).where(
                    ProductSKU.merchant_id == merchant_id,
                    ProductSKU.is_active == True,  # noqa: E712
                )
            )
        )
        .scalars()
        .all()
    )
    aliases = (
        (
            await db.execute(
                select(ProductAlias.alias).where(ProductAlias.merchant_id == merchant_id)
            )
        )
        .scalars()
        .all()
    )
    return [t for t in [*sku_names, *aliases] if t]


async def _match_merchant_sku(
    db: AsyncSession, merchant_id: uuid.UUID, word: str | None
) -> ProductSKU | None:
    """按用户原词匹配商户 SKU：标准名精确 → 别名精确 → 模糊包含。"""
    if not word:
        return None
    w = word.strip()
    if not w:
        return None
    q_name = select(ProductSKU).where(
        ProductSKU.merchant_id == merchant_id,
        ProductSKU.name == w,
        ProductSKU.is_active == True,  # noqa: E712
    )
    sku = (await db.execute(q_name)).scalar_one_or_none()
    if sku is None:
        q_alias = (
            select(ProductSKU)
            .join(ProductAlias, ProductAlias.sku_id == ProductSKU.id)
            .where(ProductAlias.merchant_id == merchant_id, ProductAlias.alias == w)
        )
        sku = (await db.execute(q_alias)).scalar_one_or_none()
    if sku is None and len(w) >= 2:
        like = f"%{w}%"
        q_alias_like = (
            select(ProductSKU)
            .join(ProductAlias, ProductAlias.sku_id == ProductSKU.id)
            .where(ProductAlias.merchant_id == merchant_id, ProductAlias.alias.like(like))
            .limit(1)
        )
        sku = (await db.execute(q_alias_like)).scalars().first()
    if sku is None and len(w) >= 2:
        q_name_like = (
            select(ProductSKU)
            .where(
                ProductSKU.merchant_id == merchant_id,
                ProductSKU.is_active == True,  # noqa: E712
                ProductSKU.name.like(f"%{w}%"),
            )
            .limit(1)
        )
        sku = (await db.execute(q_name_like)).scalars().first()
    return sku


async def _ensure_category_id(db: AsyncSession, sku: ProductSKU) -> int:
    """SKU 命中但全局品类缺失时按 SKU 名补建品类（product_id 非空约束兜底）。"""
    pid = await _lookup_product(db, sku.name)
    if pid is not None:
        return pid
    category = ProductCategory(
        name=sku.name,
        unit=sku.canonical_unit or "斤",
        shelf_life_hours=sku.shelf_life_hours or 72,
        category_group=sku.category_group,
        default_price=sku.default_sale_price,
    )
    db.add(category)
    await db.flush()
    return category.id


async def _enrich_event_with_sku(db: AsyncSession, merchant_id: uuid.UUID, event: dict) -> dict:
    """SKU 优先回填：命中 → product=SKU 标准名 + sku_id；未命中 → 回退品类表。"""
    word = event.get("product") or event.get("product_word")
    sku = await _match_merchant_sku(db, merchant_id, word)
    if sku is not None:
        event["product_word"] = word
        event["product"] = sku.name
        event["sku_id"] = str(sku.id)
        event["product_id"] = await _lookup_product(db, sku.name)
    else:
        event["sku_id"] = None
        product = event.get("product")
        event["product_id"] = await _lookup_product(db, product) if product else None
        if product and word and word != product:
            event["note"] = f"未找到商品“{word}”，已按品类记录"
    return event


async def _parse_events_with_context(
    db: AsyncSession, merchant_id: uuid.UUID, asr_text: str
) -> list[dict]:
    """带商户上下文解析：SKU 词表并入品类词表，逐事件做 SKU 优先回填。"""
    sku_terms = await _load_sku_terms(db, merchant_id)
    product_names = list(dict.fromkeys([*sku_terms, *_load_product_names()]))
    events = parse_voice_events(asr_text, product_names)
    for event in events:
        await _enrich_event_with_sku(db, merchant_id, event)
    return events


def _multi_event_warning(events: list[dict]) -> str | None:
    """多意图提示文案（与小程序端约定字段一字不差）。"""
    if len(events) > 1:
        return f"检测到{len(events)}笔，仅返回第1笔"
    return None


async def _persist_voice_logs(
    db: AsyncSession,
    *,
    merchant_id: uuid.UUID,
    asr_text: str,
    events: list[dict],
    client_id: str | None,
    audio_url: str | None = None,
) -> list[VoiceLog]:
    """一次话语的事件各建一条独立 VoiceLog（多意图前后端契约修复）。

    旧实现整句只建 1 条 log，events[1:] 是「展示卡不可确认」：前端逐卡
    「确认入账」时对缺 voice_log_id 的事件回退顶层 id，第二笔起与第一笔
    共用同一 log id，被 confirm 幂等键 voice:{log.id} 误去重（只入第一
    笔的账）。

    现约定：len(events)>1 时每笔事件独立成单（同 asr_text、各自
    parsed_event=该事件、各自待确认状态），confirm/void/edit 按 log 行
    天然隔离；len(events)<=1 时恰好一条，行为与旧版逐字段一致（无事件
    时 parsed_event=None、status=pending）。提交后把各自 voice_log_id
    回填进 events[i]——仅响应携带，落库值与旧版一致不含该字段。
    """
    logs = [
        VoiceLog(
            merchant_id=merchant_id,
            audio_url=audio_url,
            asr_text=asr_text,
            parsed_event=events[i] if events else None,
            status="parsed" if events else "pending",
            client_id=client_id,
        )
        for i in range(max(len(events), 1))
    ]
    db.add_all(logs)
    await db.commit()
    for log in logs:
        await db.refresh(log)
    for event, log in zip(events, logs, strict=False):
        event["voice_log_id"] = str(log.id)
    return logs


# 单位换算（A1 契约）：convert_to_base_unit(session, sku_id, quantity, from_unit)
# 返回 (换算数量, 基准单位) 或 None（SKU 未配置该换算）。
async def _convert_to_base(db: AsyncSession, sku_id: uuid.UUID, quantity: Decimal, from_unit: str):
    """调用单位换算服务，把任意单位数量换到 SKU 基准单位。"""
    return await convert_to_base_unit(db, sku_id, quantity, from_unit)


async def _sellable_qty(db: AsyncSession, merchant_id: uuid.UUID, product_id: int) -> float:
    """可售批次余量（基准单位），用于单位换算缺失时的 409 文案。"""
    q = select(func.coalesce(func.sum(BatchLifecycle.remaining_qty), 0)).where(
        BatchLifecycle.merchant_id == merchant_id,
        BatchLifecycle.product_id == product_id,
        BatchLifecycle.status.in_(("sellable", "near_expiry")),
    )
    return float((await db.execute(q)).scalar() or 0)


@router.post("/upload", response_model=VoiceUploadResponse)
async def upload_voice(
    merchant: Merchant = Depends(get_current_merchant),
    dialect: str = Form("mandarin"),
    client_id: str | None = Form(None),
    audio: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
):
    """Upload audio for ASR transcription and automatic semantic parsing.

    If iFlytek ASR credentials are configured, transcribes the audio and runs
    the parser. Otherwise returns an empty ``asr_text`` so the client can
    prompt the user to switch to text input.
    """
    audio_dir = Path(settings.audio_dir)
    audio_dir.mkdir(parents=True, exist_ok=True)

    audio_bytes = await audio.read()
    # 修复 C-9：校验大小 / 扩展名 / content-type，参照 vision.py 的防护模式。
    if len(audio_bytes) > _MAX_AUDIO_SIZE:
        raise HTTPException(413, "音频文件不能超过25MB")
    ext = Path(audio.filename or "").suffix.lower() or ".wav"
    if ext not in _AUDIO_EXT_WHITELIST:
        raise HTTPException(400, f"不支持的音频格式: {ext}")
    if audio.content_type and not audio.content_type.startswith("audio/"):
        raise HTTPException(400, "仅支持音频文件")
    saved_name = f"{uuid.uuid4()}{ext}"
    saved_path = audio_dir / saved_name
    saved_path.write_bytes(audio_bytes)
    audio_url = f"/uploads/audio/{saved_name}"

    asr_text = ""
    events: list[dict] = []

    # Only attempt transcription when full credentials are present.
    if settings.asr_app_id and settings.asr_api_key and settings.asr_api_secret:
        try:
            asr_text = await asr_iflytek.transcribe_audio(str(saved_path), dialect=dialect)
        except Exception as e:
            logger.error("ASR transcription failed: %s", e, exc_info=True)
            asr_text = ""

        if asr_text:
            events = await _parse_events_with_context(db, merchant.id, asr_text)
    else:
        logger.warning("ASR credentials not configured; upload saved without transcription")

    # 多意图契约：每笔事件各建一条 VoiceLog（events[i] 内嵌各自
    # voice_log_id）；识别失败/单意图仍恰好一条，行为与旧版一致。
    logs = await _persist_voice_logs(
        db,
        merchant_id=merchant.id,
        asr_text=asr_text,
        events=events,
        client_id=client_id,
        audio_url=audio_url,
    )
    voice_log = logs[0]
    parsed = events[0] if events else None

    message = (
        "ASR transcription and parsing completed" if asr_text else "语音识别未成功，请使用文字输入"
    )
    return {
        "code": 0,
        "message": message,
        "data": {
            "voice_log_id": str(voice_log.id),
            "asr_text": asr_text,
            "parsed": parsed,
            "event": parsed,
            "events": events,
            "warning": _multi_event_warning(events),
        },
    }


@router.post("/parse-text", response_model=VoiceParseTextResponse)
async def parse_text(
    body: VoiceParseTextRequest,
    merchant: Merchant = Depends(get_current_merchant),
    db: AsyncSession = Depends(get_db),
):
    """Submit text for semantic parsing. Creates voice log with parsed event.

    身份来自 token（get_current_merchant），不再信任客户端 merchant_id。
    """
    asr_text = body.text
    # 空内容（空串/纯空格）直接 422，不再产出垃圾语音记录。
    if not asr_text.strip():
        raise HTTPException(status_code=422, detail="请说出或输入要记的内容")

    # SKU 优先：商户 SKU 名称/别名并入词表，命中带 sku_id 并以 SKU 名为 product。
    events = await _parse_events_with_context(db, merchant.id, asr_text)

    # 多意图契约：每笔事件各建一条 VoiceLog，events[i] 内嵌各自
    # voice_log_id 供前端逐卡 confirm；单意图路径行为与旧版完全一致
    # （voice_log_id 即 events[0] 的，兼容旧客户端）。
    logs = await _persist_voice_logs(
        db, merchant_id=merchant.id, asr_text=asr_text, events=events, client_id=body.client_id
    )
    parsed = events[0]
    voice_log = logs[0]

    return {
        "code": 0,
        "data": {
            "voice_log_id": str(voice_log.id),
            "asr_text": asr_text,
            "parsed": parsed,
            "event": parsed,
            "events": events,
            "warning": _multi_event_warning(events),
        },
    }


@router.get("/today-count", response_model=VoiceTodayCountResponse)
async def get_today_voice_count(
    merchant_id: uuid.UUID = Depends(get_merchant_id),
    db: AsyncSession = Depends(get_db),
):
    """Return the exact number of voice records created today."""
    # created_at 由 server_default now() 存储为 UTC，边界须用 UTC 零点（见 purchase.py 同类修复）。
    today_start = utc_today_start()
    query = select(func.count(VoiceLog.id)).where(
        VoiceLog.merchant_id == merchant_id,
        VoiceLog.created_at >= today_start,
    )
    result = await db.execute(query)
    return {"code": 0, "data": {"today_count": int(result.scalar() or 0)}}


@router.get("/logs", response_model=VoiceLogsResponse)
async def get_voice_logs(
    merchant_id: uuid.UUID = Depends(get_merchant_id),
    page: int = 1,
    limit: int = 20,
    db: AsyncSession = Depends(get_db),
):
    """Query voice log history for a merchant."""
    offset = (page - 1) * limit
    query = (
        select(VoiceLog)
        .where(VoiceLog.merchant_id == merchant_id)
        .order_by(VoiceLog.created_at.desc())
        .offset(offset)
        .limit(limit)
    )
    result = await db.execute(query)
    logs = result.scalars().all()

    return {
        "code": 0,
        "data": [
            {
                "id": str(log.id),
                "merchant_id": str(log.merchant_id),
                "audio_url": log.audio_url,
                "asr_text": log.asr_text,
                "parsed_event": log.parsed_event,
                "status": log.status,
                "created_at": log.created_at.isoformat() if log.created_at else None,
            }
            for log in logs
        ],
        "meta": {"page": page, "limit": limit},
    }


@router.post("/correct", response_model=VoiceCorrectResponse)
async def correct_voice(
    body: VoiceCorrectRequest,
    merchant: Merchant = Depends(get_current_merchant),
    db: AsyncSession = Depends(get_db),
):
    """Correct parsed fields — user edits misrecognized values before confirming."""
    # 锚点行锁 + 状态守卫（V2-M1）：correct 曾无锁且无状态检查，对已 confirmed
    # 的单调用会把状态打回 parsed → 二次 confirm 撞 voice:{log.id} 幂等唯一键
    # → 500。已确认的单只能走 void/edit 链路（那里才有批次/往来账回滚）。
    query = select(VoiceLog).where(VoiceLog.id == body.voice_log_id).with_for_update()
    result = await db.execute(query)
    log = result.scalar_one_or_none()

    if not log:
        raise HTTPException(status_code=404, detail="Voice log not found")
    if log.merchant_id != merchant.id:
        raise HTTPException(status_code=404, detail="Voice log not found")
    if log.status not in ("pending", "parsed"):
        raise HTTPException(
            status_code=409,
            detail=f"只能修正未确认的记录（当前状态: {log.status}），已确认请使用撤销/修改",
        )

    if log.parsed_event:
        # 修复 C-1：corrections 已是强类型 VoiceCorrection，白名单字段 + 范围约束。
        # 只取非 None 字段合并进 parsed_event，杜绝任意键注入。
        updates = {
            k: _json_safe(v) for k, v in body.corrections.model_dump(exclude_none=True).items()
        }

        # 商品修正走 SKU 优先解析：命中则以 SKU 标准名入账并带 sku_id。
        if "product" in updates:
            corrected_name = updates["product"]
            sku = await _match_merchant_sku(db, merchant.id, corrected_name)
            if sku is not None:
                updates["product"] = sku.name
                updates["product_word"] = corrected_name
                updates["sku_id"] = str(sku.id)
                updates["product_id"] = await _lookup_product(db, sku.name)
            else:
                updates["sku_id"] = None
                updates["product_id"] = await _lookup_product(db, corrected_name)

        # P0：parsed_event 是 JSON 列，原地 .update() 不会被 SQLAlchemy 变更追踪，
        # commit 不生成 UPDATE → 用户修正静默丢失。必须赋新 dict + flag_modified。
        merged = {
            **dict(log.parsed_event),
            **updates,
            "missing_fields": [],
            "confidence": 1.0,
        }
        log.parsed_event = merged
        flag_modified(log, "parsed_event")

    log.status = "parsed"
    log.correction_count = (log.correction_count or 0) + 1
    await db.commit()

    return {
        "code": 0,
        "data": {
            "voice_log_id": str(log.id),
            "parsed": log.parsed_event,
        },
    }


@router.post("/confirm", response_model=VoiceConfirmResponse)
async def confirm_voice(
    body: VoiceConfirmRequest,
    merchant: Merchant = Depends(get_current_merchant),
    db: AsyncSession = Depends(get_db),
):
    """Confirm parsed result and persist as an inventory record."""
    # 锚点行锁：把整个多步记账（库存流水/批次/往来账/状态翻转）按 log 行
    # 串行化，弱网双击/重试的第二个请求会在锁上等待并看到已确认状态，
    # 与 pos.py pay_sale_order 的既有做法一致（SQLite 静默忽略 FOR UPDATE，
    # PG16 生效；SQLite 下由下方幂等键唯一约束兜底）。
    query = select(VoiceLog).where(VoiceLog.id == body.voice_log_id).with_for_update()
    result = await db.execute(query)
    log = result.scalar_one_or_none()

    if not log:
        raise HTTPException(status_code=404, detail="Voice log not found")
    if log.merchant_id != merchant.id:
        raise HTTPException(status_code=404, detail="Voice log not found")
    if not log.parsed_event:
        raise HTTPException(status_code=400, detail="No parsed event to confirm")

    parsed = log.parsed_event
    if log.status == "confirmed":
        return {
            "code": 0,
            "message": "该记录已记账，无需重复确认",
            "data": {
                "voice_log_id": str(log.id),
                "event_type": parsed.get("event_type", "purchase"),
                "product": parsed.get("product") or parsed.get("product_word") or "未知商品",
                "product_id": parsed.get("product_id"),
                "quantity": abs(parsed.get("quantity") or 0),
                "unit": parsed.get("unit", "斤"),
                "total_amount": parsed.get("total_amount") or 0,
                "consumed_from_batches": None,
                "idempotent": True,
            },
        }
    event_type = parsed.get("event_type", "purchase")
    # 修复 C-1：白名单校验 event_type。非 sale/waste/purchase 直接 400，
    # 防止注入任意类型时 else 分支（record_qty=qty 不取 abs）落负库存。
    ALLOWED_EVENT_TYPES = ("purchase", "sale", "waste")
    if event_type not in ALLOWED_EVENT_TYPES:
        raise HTTPException(400, f"不支持的事件类型: {event_type}")
    # 保留用户原词：商品未识别时错误提示不再丢失原词（“火龙果”≠“未知商品”）。
    product_word = parsed.get("product_word") or parsed.get("product")
    product_name = parsed.get("product") or product_word or "未知商品"

    # SKU 优先校验（与 parse-text 同一套）：缓存 sku_id / 原词匹配商户 SKU
    # （名称+别名，模糊包含），命中则挂 SKU 账本并以 SKU 名为 product；
    # 未命中回退全局品类表。
    sku = None
    cached_sku_id = parsed.get("sku_id")
    if cached_sku_id:
        try:
            candidate = await db.get(ProductSKU, uuid.UUID(str(cached_sku_id)))
        except (ValueError, TypeError):
            candidate = None
        # 跨商户 sku_id 注入防护：只认本商户的 SKU。
        if candidate is not None and candidate.merchant_id == log.merchant_id:
            sku = candidate
    if sku is None and product_word:
        sku = await _match_merchant_sku(db, log.merchant_id, product_word)

    if sku is not None:
        sku_id = sku.id
        product_name = sku.name
        product_id = await _ensure_category_id(db, sku)
    else:
        # P0-B: 解析本商户 SKU（按名/别名），让账本挂到 SKU 上，category 仅兼容。
        sku_id = await resolve_sku_id(db, log.merchant_id, product_name=product_name)
        product_id = parsed.get("product_id")
        if product_id is None and product_name:
            product_id = await _lookup_product(db, product_name)
        if product_id is None:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"未找到商品“{product_word or '未知商品'}”，请先在商品目录添加该商品或品类"
                ),
            )

    qty = parsed.get("quantity") or 0
    # 单位换算：账本/批次统一以 SKU 基准单位入账，换算只在语音边界完成
    # （catalog 模型设计约定）。服务返回 None 且单位非基准 → 409 引导补
    # 换算规则，而不是把「2箱」当「2斤」直接卖出。
    user_unit = parsed.get("unit") or "斤"
    book_qty = Decimal(str(abs(qty)))
    book_unit = user_unit
    converted = False
    if sku is not None and book_qty > 0:
        conv = await _convert_to_base(db, sku.id, book_qty, user_unit)
        if conv is None:
            if user_unit != sku.canonical_unit:
                available = await _sellable_qty(db, log.merchant_id, product_id)
                raise HTTPException(
                    status_code=409,
                    detail=(
                        f"库存不足：可用{available}{sku.canonical_unit}；"
                        f"按“{user_unit}”卖出需先在商品目录设置单位换算"
                    ),
                )
        else:
            new_qty, base_unit = Decimal(str(conv[0])), str(conv[1])
            converted = new_qty != book_qty or base_unit != user_unit
            book_qty, book_unit = abs(new_qty), base_unit

    if event_type in ("sale", "waste"):
        record_qty = -book_qty
    else:
        record_qty = book_qty

    batch_label = f"{product_name}-{utc_now().strftime('%m%d%H%M')}"

    total_amount = (
        parsed.get("total_amount") or parsed.get("total_cost") or parsed.get("total_revenue")
    )
    if total_amount is not None:
        total_amount = Decimal(str(total_amount))
    unit_cost = parsed.get("unit_cost")
    if unit_cost is not None:
        unit_cost = Decimal(str(unit_cost))
    unit_price = parsed.get("unit_price")
    if unit_price is not None:
        unit_price = Decimal(str(unit_price))
    # 换算后数量变了：单价/成本按基准单位重摊（80元/2箱 → 每基准单位重算）。
    if converted and total_amount is not None and book_qty > 0:
        if unit_cost is not None:
            unit_cost = (total_amount / book_qty).quantize(Decimal("0.01"))
        if unit_price is not None:
            unit_price = (total_amount / book_qty).quantize(Decimal("0.01"))
    record = InventoryRecord(
        merchant_id=log.merchant_id,
        product_id=product_id,
        sku_id=sku_id,
        quantity=record_qty,
        unit=book_unit,
        unit_cost=unit_cost if event_type == "purchase" else None,
        unit_price=unit_price if event_type == "sale" else None,
        total_amount=total_amount,
        event_type=event_type,
        event_time=utc_now(),
        source="voice",
        voice_log_id=log.id,
        batch_label=batch_label if event_type == "purchase" else None,
        # 跨端幂等兜底：uq_inventory_idempotency_per_merchant 唯一约束保证
        # 同一语音单最多入账一条库存流水（状态检查 + 锚点锁是第一道防线）。
        idempotency_key=f"voice:{log.id}",
    )
    db.add(record)

    # Track batch lifecycle: create on purchase, consume FIFO on sale/waste.
    consumed_from_batches = None
    if event_type == "purchase":
        await create_batch(
            db,
            merchant_id=log.merchant_id,
            product_id=product_id,
            product_name=product_name,
            batch_label=batch_label,
            quantity=book_qty,
            purchase_time=record.event_time,
            sku_id=sku_id,
            unit_cost=unit_cost,
        )
    elif event_type in ("sale", "waste"):
        consumed_from_batches = await consume_batches_fifo(
            db, log.merchant_id, product_id, book_qty, sku_id=sku_id
        )
        # F3: reject instead of silently under-consuming — otherwise the
        # InventoryRecord (already db.add-ed above) would book a sale/waste
        # that FIFO could not fulfil, driving stock negative.
        if consumed_from_batches < book_qty:
            raise HTTPException(
                status_code=409,
                detail=(
                    f"库存不足，需要{float(book_qty)}{book_unit}，"
                    f"可用{float(consumed_from_batches)}"
                ),
            )

    # P1-D: 语音记账触发的往来账流水。
    # 解析到交易对手 & 赊账/回款关键词时，分别落客户应收或供应商付款。
    party_name = parsed.get("party_name")
    is_credit = parsed.get("is_credit", False)
    is_repay = parsed.get("is_repay", False)
    total_amount_for_debt = (
        parsed.get("total_amount") or parsed.get("total_revenue") or parsed.get("total_cost")
    )
    if total_amount_for_debt and party_name:
        debt_amount = Decimal(str(total_amount_for_debt)).quantize(Decimal("0.01"))
        if event_type == "sale" and is_credit:
            await record_customer_receivable(
                db,
                merchant_id=log.merchant_id,
                customer_name=party_name,
                amount=debt_amount,
                direction="charge",
                note=f"语音销售赊账 {product_name} x{abs(qty)}",
                idempotency_key=f"voice:{log.id}:charge",
            )
        elif is_repay:
            # 先按客户回款处理；若后续需要区分供应商付款，可在 party 处加 supplier 标记。
            await record_customer_receivable(
                db,
                merchant_id=log.merchant_id,
                customer_name=party_name,
                amount=debt_amount,
                direction="repay",
                note=f"语音回款/结算 {party_name}",
                idempotency_key=f"voice:{log.id}:repay",
            )

    log.status = "confirmed"
    await db.commit()

    return {
        "code": 0,
        "message": "记账成功",
        "data": {
            "voice_log_id": str(log.id),
            "event_type": event_type,
            "product": product_name,
            "product_id": product_id,
            "quantity": float(book_qty),
            "unit": book_unit,
            "total_amount": parsed.get("total_amount") or 0,
            "consumed_from_batches": consumed_from_batches,
        },
    }


@router.post("/{voice_log_id}/void", response_model=VoiceVoidResponse)
async def void_voice_record(
    voice_log_id: uuid.UUID,
    body: VoiceVoidRequest,
    merchant: Merchant = Depends(get_current_merchant),
    db: AsyncSession = Depends(get_db),
    _perm=Depends(require_permission("void_record")),
):
    """Void a confirmed voice record — rolls back inventory and batches.

    Soft-delete: marks voided, never physically deletes. Creates audit log.
    """
    # 锚点行锁：撤销与确认/修改按 log 行串行化，防止并发撤销+确认交错记账
    # （SQLite 忽略 FOR UPDATE，由状态检查幂等语义兜底）。
    # 加锁次序 VoiceLog → InventoryRecord → 批次，与 inventory.py 的语音
    # 分支共用 void_voice_confirmed_record 核心消除跨路径双撤销（V2-H1）。
    query = select(VoiceLog).where(VoiceLog.id == voice_log_id).with_for_update()
    result = await db.execute(query)
    log = result.scalar_one_or_none()
    if not log:
        raise HTTPException(status_code=404, detail="语音记录不存在")
    if log.merchant_id != merchant.id:
        raise HTTPException(status_code=404, detail="语音记录不存在")

    record, batch_summary = await void_voice_confirmed_record(
        db, log, body.reason, voided_by="voice"
    )

    if record is None:
        await db.commit()
        return {
            "code": 0,
            "message": "记录已撤销（无关联库存记录）",
            "data": {"voice_log_id": str(log.id)},
        }

    await db.commit()

    return {
        "code": 0,
        "message": "记录已撤销，库存和批次已回滚",
        "data": {
            "voice_log_id": str(log.id),
            "record_id": str(record.id),
            "batch_summary": batch_summary,
        },
    }


@router.put("/{voice_log_id}/edit", response_model=VoiceEditResponse)
async def edit_confirmed_record(
    voice_log_id: uuid.UUID,
    body: VoiceEditRequest,
    merchant: Merchant = Depends(get_current_merchant),
    db: AsyncSession = Depends(get_db),
    _perm=Depends(require_permission("void_record")),
):
    """Edit a confirmed record: void old + create corrected record (full audit trail).

    Body: { "product"?, "quantity"?, "unit"?, "unit_cost"?, "unit_price"?, "reason"? }
    """
    # 锚点行锁：与 confirm/void 按 log 行串行化，防止并发修改交错冲正
    # （SQLite 忽略 FOR UPDATE，由冲正记录幂等键唯一约束兜底）。
    query = select(VoiceLog).where(VoiceLog.id == voice_log_id).with_for_update()
    result = await db.execute(query)
    log = result.scalar_one_or_none()
    if not log:
        raise HTTPException(status_code=404, detail="语音记录不存在")
    if log.merchant_id != merchant.id:
        raise HTTPException(status_code=404, detail="语音记录不存在")
    if log.status != "confirmed":
        raise HTTPException(status_code=409, detail="只能修改已确认的记录")

    # 流水行锁（V2-H1）：撤销/修改与 inventory.py 的 record-void 会在同一条
    # source="voice" 流水上交汇，必须在已持有 log 锁的前提下再锁流水行，
    # 维持 VoiceLog → InventoryRecord → 批次 的统一加锁次序。
    record_query = (
        select(InventoryRecord)
        .where(
            InventoryRecord.voice_log_id == log.id,
            InventoryRecord.is_voided.is_(False),
        )
        .with_for_update()
    )
    record_result = await db.execute(record_query)
    old_record = record_result.scalar_one_or_none()
    if not old_record:
        raise HTTPException(status_code=409, detail="未找到关联的库存记录")
    if old_record.source == "pos":
        # 订单体系另有完整退款链路（pos.py refund），语音修改不得绕过其核销逻辑。
        raise HTTPException(
            status_code=409,
            detail="POS订单流水不支持语音修改，请通过订单退款链路处理",
        )

    # 编辑序号：correction_count 在 correct/edit 中严格递增，保证每次编辑的
    # 冲正记录/往来账差额记录幂等键唯一 —— 重复编辑不撞唯一约束、不双重冲销。
    edit_seq = (log.correction_count or 0) + 1

    parsed = log.parsed_event or {}
    event_type = old_record.event_type
    new_product_name = body.product or parsed.get("product", "未知商品")
    new_qty = Decimal(str(body.quantity if body.quantity is not None else abs(old_record.quantity)))
    new_unit = body.unit or old_record.unit
    new_unit_cost = body.unit_cost if body.unit_cost is not None else old_record.unit_cost
    new_unit_price = body.unit_price if body.unit_price is not None else old_record.unit_price
    new_total = body.total_amount if body.total_amount is not None else old_record.total_amount

    old_before = {
        "quantity": float(old_record.quantity),
        "event_type": old_record.event_type,
        "product_id": old_record.product_id,
    }
    batch_summary = await rollback_batch_on_void(
        db, log.merchant_id, old_record.product_id, old_record
    )
    old_record.is_voided = True
    old_record.voided_at = utc_now()
    old_record.void_reason = body.reason or "修改后冲正"
    old_record.voided_by = "edit"

    # SKU 优先（与 confirm 同一套）：命中则以 SKU 名入账并补建品类兜底。
    edit_sku = await _match_merchant_sku(db, merchant.id, new_product_name)
    if edit_sku is not None:
        new_sku_id = edit_sku.id
        new_product_name = edit_sku.name
        new_product_id = await _ensure_category_id(db, edit_sku)
    else:
        new_product_id = await _lookup_product(db, new_product_name)
        if new_product_id is None:
            raise HTTPException(
                status_code=400,
                detail=f"未找到商品“{new_product_name}”，请先在商品目录添加该商品或品类",
            )
        # P0-B: 解析本商户 SKU（按名/别名），让冲正后的账本也挂到 SKU 上。
        new_sku_id = await resolve_sku_id(db, merchant.id, product_name=new_product_name)

    if event_type in ("sale", "waste"):
        record_qty = -abs(new_qty)
    else:
        record_qty = abs(new_qty)

    batch_label = f"{new_product_name}-{utc_now().strftime('%m%d%H%M')}"
    unit_cost = None
    if new_unit_cost is not None:
        unit_cost = Decimal(str(new_unit_cost)) if event_type == "purchase" else None
    unit_price = None
    if new_unit_price is not None:
        unit_price = Decimal(str(new_unit_price)) if event_type == "sale" else None
    total_amount = None
    if new_total is not None:
        total_amount = Decimal(str(new_total))
    corrected_record = InventoryRecord(
        merchant_id=log.merchant_id,
        product_id=new_product_id,
        sku_id=new_sku_id,
        quantity=Decimal(str(record_qty)),
        unit=new_unit,
        unit_cost=unit_cost,
        unit_price=unit_price,
        total_amount=total_amount,
        event_type=event_type,
        event_time=utc_now(),
        source="voice",
        voice_log_id=log.id,
        batch_label=batch_label if event_type == "purchase" else None,
        is_correction=True,
        original_record_id=old_record.id,
        # 跨端幂等兜底：每次编辑的冲正记录用独立序号键，与原记录的
        # voice:{log.id} 键共存且互不冲突。
        idempotency_key=f"voice:{log.id}:edit{edit_seq}",
    )
    db.add(corrected_record)

    consumed = None
    if event_type == "purchase":
        await create_batch(
            db,
            merchant_id=log.merchant_id,
            product_id=new_product_id,
            product_name=new_product_name,
            batch_label=batch_label,
            quantity=Decimal(str(abs(new_qty))),
            purchase_time=corrected_record.event_time,
            sku_id=new_sku_id,
            unit_cost=unit_cost,
        )
    elif event_type in ("sale", "waste"):
        requested_qty = Decimal(str(abs(new_qty)))
        consumed = await consume_batches_fifo(db, log.merchant_id, new_product_id, requested_qty)
        # F3 对齐 confirm_voice：FIFO 消耗不足即 409（本次事务内的回滚/作废
        # 随请求异常一并回退），防止冲正记录把库存改负。
        if consumed < requested_qty:
            raise HTTPException(
                status_code=409,
                detail=(f"库存不足，需要{float(requested_qty)}{new_unit}，可用{float(consumed)}"),
            )

    parsed = {
        **parsed,
        "product": new_product_name,
        "product_id": new_product_id,
        "quantity": float(abs(new_qty)),
        "unit": new_unit,
    }
    if new_unit_cost is not None:
        parsed["unit_cost"] = _json_safe(new_unit_cost)
    if new_unit_price is not None:
        parsed["unit_price"] = _json_safe(new_unit_price)
    if new_total is not None:
        parsed["total_amount"] = _json_safe(new_total)
    # JSON 列不能原地赋同一对象：新 dict + flag_modified 才会生成 UPDATE。
    log.parsed_event = dict(parsed)
    flag_modified(log, "parsed_event")
    log.correction_count = (log.correction_count or 0) + 1

    # 往来账对齐：把该语音单名下应收净额冲平后按修正后金额重新入账
    # （非赊账/无对手方时目标为 0，即纯冲销）。金额口径与 confirm 的
    # P1-D 分支一致：total_amount → total_revenue → total_cost。
    party_name = parsed.get("party_name")
    total_for_debt = (
        parsed.get("total_amount") or parsed.get("total_revenue") or parsed.get("total_cost")
    )
    target_party = None
    target_net = None
    if party_name and total_for_debt is not None:
        target_amount = Decimal(str(total_for_debt)).quantize(Decimal("0.01"))
        if target_amount > 0:
            if event_type == "sale" and parsed.get("is_credit", False):
                target_party, target_net = party_name, target_amount
            elif parsed.get("is_repay", False):
                target_party, target_net = party_name, -target_amount
    await sync_voice_receivables(
        db,
        log,
        adjustment_key=f"voice:{log.id}:edit{edit_seq}",
        target_party=target_party,
        target_net=target_net,
    )

    audit = AuditLog(
        merchant_id=log.merchant_id,
        action="edit",
        target_table="inventory_records",
        target_id=str(old_record.id),
        before_data=old_before,
        after_data={
            # 审计 JSON 列不能存 Decimal，金额/数量统一 float 化。
            "new_record": {
                "product_id": new_product_id,
                "quantity": float(record_qty),
                "unit_cost": float(new_unit_cost) if new_unit_cost is not None else None,
                "unit_price": float(new_unit_price) if new_unit_price is not None else None,
            },
            "batch_summary": {
                **batch_summary,
                "qty_adjusted": float(batch_summary["qty_adjusted"]),
            },
        },
        reason=body.reason,
        operator="merchant",
    )
    db.add(audit)
    await db.commit()

    return {
        "code": 0,
        "message": "记录已修改，库存和批次已更新",
        "data": {
            "voice_log_id": str(log.id),
            "old_record_id": str(old_record.id),
            "new_record_id": str(corrected_record.id),
            "product": new_product_name,
            "quantity": abs(new_qty),
            "unit": new_unit,
            "consumed_from_batches": consumed,
        },
    }
