"""Voice accounting API router — core Phase 1 module."""

import json
import logging
import uuid
from decimal import Decimal
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.security import get_current_merchant, get_merchant_id
from app.core.timezone import utc_now, utc_today_start
from app.database import get_db
from app.models.audit import AuditLog
from app.models.inventory import InventoryRecord
from app.models.merchant import Merchant
from app.models.product import ProductCategory
from app.models.voice import VoiceLog
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
from app.services.voice_parser import parse_voice_text


logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/voice", tags=["voice"])

_RULES_DIR = Path(__file__).parent.parent / "rules"


def _load_product_names() -> list[str]:
    config_path = _RULES_DIR / "product_categories.json"
    if config_path.exists():
        with open(config_path, encoding="utf-8") as f:
            data = json.load(f)
        return data.get("product_names", [])
    return ["白菜", "土豆", "豆腐", "猪肉"]


async def _lookup_product(db: AsyncSession, name: str) -> int | None:
    """Look up product_id by name. Returns None if not found."""
    if not name:
        return None
    query = select(ProductCategory.id).where(ProductCategory.name == name)
    result = await db.execute(query)
    pid = result.scalar_one_or_none()
    return pid


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
    ext = Path(audio.filename or "").suffix.lower() or ".wav"
    saved_name = f"{uuid.uuid4()}{ext}"
    saved_path = audio_dir / saved_name
    saved_path.write_bytes(audio_bytes)
    audio_url = f"/uploads/audio/{saved_name}"

    asr_text = ""
    parsed = None

    # Only attempt transcription when full credentials are present.
    if settings.asr_app_id and settings.asr_api_key and settings.asr_api_secret:
        try:
            asr_text = await asr_iflytek.transcribe_audio(str(saved_path), dialect=dialect)
        except Exception as e:
            logger.error("ASR transcription failed: %s", e, exc_info=True)
            asr_text = ""

        if asr_text:
            product_names = _load_product_names()
            parsed = parse_voice_text(asr_text, product_names)
            product_name = parsed.get("product")
            product_id = await _lookup_product(db, product_name) if product_name else None
            parsed["product_id"] = product_id
    else:
        logger.warning("ASR credentials not configured; upload saved without transcription")

    voice_log = VoiceLog(
        merchant_id=merchant.id,
        audio_url=audio_url,
        asr_text=asr_text,
        parsed_event=parsed,
        status="parsed" if parsed else "pending",
        client_id=client_id,
    )
    db.add(voice_log)
    await db.commit()
    await db.refresh(voice_log)

    if parsed:
        parsed["voice_log_id"] = str(voice_log.id)

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
    product_names = _load_product_names()

    parsed = parse_voice_text(asr_text, product_names)

    # If product name was parsed, look up its real database ID
    product_name = parsed.get("product")
    product_id = await _lookup_product(db, product_name) if product_name else None
    parsed["product_id"] = product_id

    voice_log = VoiceLog(
        merchant_id=merchant.id,
        asr_text=asr_text,
        parsed_event=parsed,
        status="parsed",
        client_id=body.client_id,
    )
    db.add(voice_log)
    await db.commit()
    await db.refresh(voice_log)

    # Embed voice_log_id so frontend can use it for confirm/correct
    parsed["voice_log_id"] = str(voice_log.id)

    return {
        "code": 0,
        "data": {
            "voice_log_id": str(voice_log.id),
            "asr_text": asr_text,
            "parsed": parsed,
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
    query = select(VoiceLog).where(VoiceLog.id == body.voice_log_id)
    result = await db.execute(query)
    log = result.scalar_one_or_none()

    if not log:
        raise HTTPException(status_code=404, detail="Voice log not found")
    if log.merchant_id != merchant.id:
        raise HTTPException(status_code=404, detail="Voice log not found")

    if log.parsed_event:
        log.parsed_event.update(body.corrections)
        log.parsed_event["missing_fields"] = []
        log.parsed_event["confidence"] = 1.0

        # Re-lookup product_id if product name was corrected
        if "product" in body.corrections:
            pid = await _lookup_product(db, body.corrections["product"])
            log.parsed_event["product_id"] = pid

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
    query = select(VoiceLog).where(VoiceLog.id == body.voice_log_id)
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
                "product": parsed.get("product") or "未知商品",
                "product_id": parsed.get("product_id"),
                "quantity": abs(parsed.get("quantity") or 0),
                "unit": parsed.get("unit", "斤"),
                "total_amount": parsed.get("total_amount") or 0,
                "consumed_from_batches": None,
                "idempotent": True,
            },
        }
    event_type = parsed.get("event_type", "purchase")
    product_name = parsed.get("product") or "未知商品"

    # Resolve product_id: use cached value from parsing, or look up fresh
    product_id = parsed.get("product_id")
    if product_id is None and product_name:
        product_id = await _lookup_product(db, product_name)

    if product_id is None:
        raise HTTPException(
            status_code=400,
            detail=f"商品 '{product_name}' 未在品类表中找到，请先添加品类",
        )

    # P0-B: 解析本商户 SKU（按名/别名），让账本挂到 SKU 上，category 仅兼容。
    sku_id = await resolve_sku_id(db, merchant.id, product_name=product_name)

    qty = parsed.get("quantity") or 0
    if event_type in ("sale", "waste"):
        record_qty = -abs(qty)
    elif event_type == "purchase":
        record_qty = abs(qty)
    else:
        record_qty = qty

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
    record = InventoryRecord(
        merchant_id=log.merchant_id,
        product_id=product_id,
        sku_id=sku_id,
        quantity=Decimal(str(record_qty)),
        unit=parsed.get("unit", "斤"),
        unit_cost=unit_cost if event_type == "purchase" else None,
        unit_price=unit_price if event_type == "sale" else None,
        total_amount=total_amount,
        event_type=event_type,
        event_time=utc_now(),
        source="voice",
        voice_log_id=log.id,
        batch_label=batch_label if event_type == "purchase" else None,
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
            quantity=Decimal(str(abs(qty))),
            purchase_time=record.event_time,
            sku_id=sku_id,
            unit_cost=unit_cost,
        )
    elif event_type in ("sale", "waste"):
        consumed_from_batches = await consume_batches_fifo(
            db, log.merchant_id, product_id, Decimal(str(abs(qty)))
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
            "quantity": abs(qty),
            "unit": parsed.get("unit", "斤"),
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
):
    """Void a confirmed voice record — rolls back inventory and batches.

    Soft-delete: marks voided, never physically deletes. Creates audit log.
    """
    query = select(VoiceLog).where(VoiceLog.id == voice_log_id)
    result = await db.execute(query)
    log = result.scalar_one_or_none()
    if not log:
        raise HTTPException(status_code=404, detail="语音记录不存在")
    if log.merchant_id != merchant.id:
        raise HTTPException(status_code=404, detail="语音记录不存在")
    if log.status == "voided":
        raise HTTPException(status_code=400, detail="该记录已撤销，无需重复操作")
    if log.status != "confirmed":
        raise HTTPException(status_code=400, detail="只能撤销已确认的记录")

    record_query = select(InventoryRecord).where(
        InventoryRecord.voice_log_id == log.id,
        InventoryRecord.is_voided.is_(False),
    )
    record_result = await db.execute(record_query)
    record = record_result.scalar_one_or_none()

    if not record:
        log.status = "voided"
        await db.commit()
        return {
            "code": 0,
            "message": "记录已撤销（无关联库存记录）",
            "data": {"voice_log_id": str(log.id)},
        }

    before_data = {
        "quantity": float(record.quantity),
        "event_type": record.event_type,
        "product_id": record.product_id,
        "total_amount": float(record.total_amount) if record.total_amount else None,
    }

    batch_summary = await rollback_batch_on_void(db, log.merchant_id, record.product_id, record)

    record.is_voided = True
    record.voided_at = utc_now()
    record.void_reason = body.reason
    record.voided_by = "voice"

    log.status = "voided"

    audit = AuditLog(
        merchant_id=log.merchant_id,
        action="void",
        target_table="inventory_records",
        target_id=str(record.id),
        before_data=before_data,
        after_data={"is_voided": True, "batch_summary": batch_summary},
        reason=body.reason,
        operator="merchant",
    )
    db.add(audit)
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
):
    """Edit a confirmed record: void old + create corrected record (full audit trail).

    Body: { "product"?, "quantity"?, "unit"?, "unit_cost"?, "unit_price"?, "reason"? }
    """
    query = select(VoiceLog).where(VoiceLog.id == voice_log_id)
    result = await db.execute(query)
    log = result.scalar_one_or_none()
    if not log:
        raise HTTPException(status_code=404, detail="语音记录不存在")
    if log.merchant_id != merchant.id:
        raise HTTPException(status_code=404, detail="语音记录不存在")
    if log.status != "confirmed":
        raise HTTPException(status_code=400, detail="只能修改已确认的记录")

    record_query = select(InventoryRecord).where(
        InventoryRecord.voice_log_id == log.id,
        InventoryRecord.is_voided.is_(False),
    )
    record_result = await db.execute(record_query)
    old_record = record_result.scalar_one_or_none()
    if not old_record:
        raise HTTPException(status_code=400, detail="未找到关联的库存记录")

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

    new_product_id = await _lookup_product(db, new_product_name)
    if new_product_id is None:
        raise HTTPException(
            status_code=400,
            detail=f"商品 '{new_product_name}' 未在品类表中找到",
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
        consumed = await consume_batches_fifo(
            db, log.merchant_id, new_product_id, Decimal(str(abs(new_qty)))
        )

    parsed.update(
        {
            "product": new_product_name,
            "product_id": new_product_id,
            "quantity": abs(new_qty),
            "unit": new_unit,
        }
    )
    if new_unit_cost is not None:
        parsed["unit_cost"] = new_unit_cost
    if new_unit_price is not None:
        parsed["unit_price"] = new_unit_price
    if new_total is not None:
        parsed["total_amount"] = new_total
    log.parsed_event = parsed
    log.correction_count = (log.correction_count or 0) + 1

    audit = AuditLog(
        merchant_id=log.merchant_id,
        action="edit",
        target_table="inventory_records",
        target_id=str(old_record.id),
        before_data=old_before,
        after_data={
            "new_record": {
                "product_id": new_product_id,
                "quantity": record_qty,
                "unit_cost": new_unit_cost,
                "unit_price": new_unit_price,
            },
            "batch_summary": batch_summary,
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
