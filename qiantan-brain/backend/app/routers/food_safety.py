"""食品安全与批次追溯 API (sections 4.13, 4.14).

- 批次锁定/解锁/召回/销毁
- 批次追溯二维码生成
- 每日自查任务
- 不合格下架流程
"""

from __future__ import annotations

import json
import uuid

from fastapi import APIRouter, Body, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import get_current_merchant
from app.core.timezone import utc_now
from app.database import get_db
from app.models.audit import AuditLog
from app.models.batch import BatchLifecycle
from app.models.inventory import InventoryRecord
from app.models.merchant import Merchant
from app.schemas.common import AnyResponse
from app.services.batch import (
    destroy_batch,
    get_batch_trace_data,
    lock_batch,
    recall_batch,
    unlock_batch,
)


router = APIRouter(prefix="/api/v1/food-safety", tags=["food-safety"])


# ═══════════════════════════════════════════════════════════
# 批次管理
# ═══════════════════════════════════════════════════════════

@router.get("/batches", response_model=AnyResponse)
async def list_batches(
    status: str | None = None,
    limit: int = 50,
    merchant: Merchant = Depends(get_current_merchant),
    db: AsyncSession = Depends(get_db),
):
    """List batches, optionally filtered by status."""
    filters = [BatchLifecycle.merchant_id == merchant.id]
    if status: filters.append(BatchLifecycle.status == status)
    rows = (await db.execute(
        select(BatchLifecycle).where(*filters).order_by(BatchLifecycle.purchase_date.desc()).limit(min(limit, 200))
    )).scalars().all()
    return {"code": 0, "data": [
        {"batch_id": str(b.id), "batch_label": b.batch_label, "product_id": b.product_id,
         "sku_id": str(b.sku_id) if b.sku_id else None, "supplier_name": b.supplier_name,
         "origin": b.origin, "purchase_qty": float(b.purchase_qty), "remaining_qty": float(b.remaining_qty),
         "unit_cost": float(b.unit_cost) if b.unit_cost else None,
         "status": b.status, "expiry_date": b.expiry_date.isoformat() if b.expiry_date else None,
         "inspection_result": b.inspection_result, "locked_reason": b.locked_reason,
         "purchase_date": b.purchase_date.isoformat() if b.purchase_date else None}
        for b in rows
    ]}


@router.get("/batches/{batch_id}/trace", response_model=AnyResponse)
async def batch_trace(
    batch_id: uuid.UUID,
    merchant: Merchant = Depends(get_current_merchant),
    db: AsyncSession = Depends(get_db),
):
    """Generate full traceability data for QR code (section 4.13)."""
    data = await get_batch_trace_data(db, batch_id, merchant.id)
    if not data:
        raise HTTPException(status_code=404, detail="批次不存在")
    return {"code": 0, "data": data}


@router.post("/batches/{batch_id}/generate-qr", response_model=AnyResponse)
async def generate_batch_qr(
    batch_id: uuid.UUID,
    merchant: Merchant = Depends(get_current_merchant),
    db: AsyncSession = Depends(get_db),
):
    """Generate and persist QR code data for a batch (section 4.13).

    The QR code encodes a trace_code UUID that links to the full trace data.
    Consumers can scan the QR to verify origin, inspection, and recall status
    without exposing cost/profit data.
    """
    batch = await db.get(BatchLifecycle, batch_id)
    if not batch or batch.merchant_id != merchant.id:
        raise HTTPException(status_code=404, detail="批次不存在")

    # Generate trace data first
    trace_data = await get_batch_trace_data(db, batch_id, merchant.id)
    if not trace_data:
        raise HTTPException(status_code=500, detail="无法生成追溯数据")

    # Build QR payload — public-safe subset (no cost data)
    trace_code = str(uuid.uuid4())
    qr_payload = {
        "trace_code": trace_code,
        "batch_label": batch.batch_label,
        "product_name": trace_data.get("product_name"),
        "supplier_name": trace_data.get("supplier_name"),
        "origin": trace_data.get("origin"),
        "purchase_date": trace_data.get("purchase_date"),
        "inspection_result": trace_data.get("inspection_result"),
        "certificates": trace_data.get("certificates"),
        "status": batch.status,
        "expiry_date": batch.expiry_date.isoformat() if batch.expiry_date else None,
        "generated_at": utc_now().isoformat(),
        # URL path for the trace lookup page (mini-program or web)
        "trace_url": f"/trace/{trace_code}",
    }

    # Persist QR data on the batch
    batch.qr_data = json.dumps(qr_payload, ensure_ascii=False)

    db.add(AuditLog(
        merchant_id=merchant.id, action="batch_generate_qr",
        target_table="batch_lifecycles", target_id=str(batch.id),
        after_data={"trace_code": trace_code}, operator="merchant",
    ))
    await db.commit()

    return {
        "code": 0,
        "message": f"批次 {batch.batch_label} 二维码已生成",
        "data": {
            "batch_id": str(batch.id),
            "trace_code": trace_code,
            "qr_payload": qr_payload,
        },
    }


@router.get("/trace/{trace_code}", response_model=AnyResponse)
async def lookup_trace(
    trace_code: str,
    db: AsyncSession = Depends(get_db),
):
    """Public trace lookup by QR trace_code — no auth required (consumer-facing).

    Only exposes public-safe fields: product name, supplier, origin,
    inspection result, certificates, and recall status. Never exposes cost/profit.
    """
    # Search all batches for this trace_code in qr_data JSON
    query = select(BatchLifecycle).where(
        BatchLifecycle.qr_data.contains(trace_code)
    ).limit(1)
    result = await db.execute(query)
    batch = result.scalar_one_or_none()

    if not batch:
        raise HTTPException(status_code=404, detail="追溯码无效或已过期")

    # Parse QR data
    qr_data = json.loads(batch.qr_data) if batch.qr_data else {}

    # Return public subset only
    return {
        "code": 0,
        "data": {
            "trace_code": trace_code,
            "batch_label": batch.batch_label,
            "product_name": qr_data.get("product_name"),
            "supplier_name": qr_data.get("supplier_name"),
            "origin": qr_data.get("origin"),
            "purchase_date": qr_data.get("purchase_date"),
            "expiry_date": batch.expiry_date.isoformat() if batch.expiry_date else None,
            "inspection_result": qr_data.get("inspection_result"),
            "certificates": qr_data.get("certificates"),
            "status": batch.status,
            "is_recalled": batch.status in ("recalled", "destroyed"),
            "generated_at": qr_data.get("generated_at"),
        },
    }


@router.post("/batches/{batch_id}/inspect", response_model=AnyResponse)
async def record_inspection(
    batch_id: uuid.UUID, body: dict,
    merchant: Merchant = Depends(get_current_merchant),
    db: AsyncSession = Depends(get_db),
):
    """Record a quick-test inspection result for a batch."""
    batch = await db.get(BatchLifecycle, batch_id)
    if not batch or batch.merchant_id != merchant.id:
        raise HTTPException(status_code=404, detail="批次不存在")
    result = body.get("result", "pass")
    batch.inspection_result = result

    db.add(AuditLog(merchant_id=merchant.id, action="batch_inspect",
        target_table="batch_lifecycles", target_id=str(batch.id),
        after_data={"result": result}, operator="merchant"))
    await db.commit()
    return {"code": 0, "message": f"快检结果已记录: {result}"}


# ═══════════════════════════════════════════════════════════
# 锁定/解锁/召回/销毁 (section 4.14 不合格流程)
# ═══════════════════════════════════════════════════════════

@router.post("/batches/{batch_id}/lock", response_model=AnyResponse)
async def lock(
    batch_id: uuid.UUID, body: dict = Body(default={}),
    merchant: Merchant = Depends(get_current_merchant),
    db: AsyncSession = Depends(get_db),
):
    """Lock a batch — POS will immediately stop selling from this batch."""
    try:
        batch = await lock_batch(db, batch_id, merchant.id,
            reason=body.get("reason", "快检不合格"), locked_by="merchant")
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e)) from e

    db.add(AuditLog(merchant_id=merchant.id, action="batch_lock",
        target_table="batch_lifecycles", target_id=str(batch.id),
        after_data={"status": "locked", "reason": batch.locked_reason}, operator="merchant"))
    await db.commit()

    # Calculate remaining stock and affected orders
    remaining = float(batch.remaining_qty)
    return {"code": 0, "message": f"批次已锁定，剩余 {remaining} 禁止销售",
            "data": {"batch_id": str(batch.id), "remaining_qty": remaining, "status": "locked"}}


@router.post("/batches/{batch_id}/unlock", response_model=AnyResponse)
async def unlock(
    batch_id: uuid.UUID,
    merchant: Merchant = Depends(get_current_merchant),
    db: AsyncSession = Depends(get_db),
):
    """Unlock a batch (re-inspection passed)."""
    try:
        batch = await unlock_batch(db, batch_id, merchant.id)
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e)) from e
    db.add(AuditLog(merchant_id=merchant.id, action="batch_unlock",
        target_table="batch_lifecycles", target_id=str(batch.id),
        after_data={"status": "sellable"}, operator="merchant"))
    await db.commit()
    return {"code": 0, "message": "批次已解锁，恢复正常销售"}


@router.post("/batches/{batch_id}/recall", response_model=AnyResponse)
async def recall(
    batch_id: uuid.UUID, body: dict = Body(default={}),
    merchant: Merchant = Depends(get_current_merchant),
    db: AsyncSession = Depends(get_db),
):
    """Recall a locked batch."""
    try:
        batch = await recall_batch(db, batch_id, merchant.id, reason=body.get("reason", "食品安全召回"))
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e)) from e
    db.add(AuditLog(merchant_id=merchant.id, action="batch_recall",
        target_table="batch_lifecycles", target_id=str(batch.id),
        after_data={"status": "recalled"}, reason=body.get("reason"), operator="merchant"))
    await db.commit()
    return {"code": 0, "message": "批次已召回"}


@router.post("/batches/{batch_id}/destroy", response_model=AnyResponse)
async def destroy(
    batch_id: uuid.UUID, body: dict = Body(default={}),
    merchant: Merchant = Depends(get_current_merchant),
    db: AsyncSession = Depends(get_db),
):
    """Destroy a recalled batch — final disposal with waste record."""
    try:
        batch = await destroy_batch(db, batch_id, merchant.id,
            reason=body.get("reason", "不合格销毁"))
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e)) from e
    db.add(AuditLog(merchant_id=merchant.id, action="batch_destroy",
        target_table="batch_lifecycles", target_id=str(batch.id),
        after_data={"status": "destroyed", "remaining_qty": float(batch.remaining_qty)},
        reason=body.get("reason"), operator="merchant"))
    await db.commit()
    return {"code": 0, "message": f"批次已销毁，剩余 {float(batch.remaining_qty)} 已记录报损"}


# ═══════════════════════════════════════════════════════════
# 每日自查
# ═══════════════════════════════════════════════════════════

@router.get("/daily-checklist", response_model=AnyResponse)
async def daily_checklist(
    merchant: Merchant = Depends(get_current_merchant),
    db: AsyncSession = Depends(get_db),
):
    """Return daily food safety checklist with current status."""
    # Expired batches
    expired = (await db.execute(
        select(func.count(BatchLifecycle.id)).where(
            BatchLifecycle.merchant_id == merchant.id, BatchLifecycle.remaining_qty > 0,
            BatchLifecycle.expiry_date < utc_now(),
        )
    )).scalar() or 0

    # Locked batches
    locked = (await db.execute(
        select(func.count(BatchLifecycle.id)).where(
            BatchLifecycle.merchant_id == merchant.id, BatchLifecycle.status == "locked"
        )
    )).scalar() or 0

    # Batches without inspection
    uninspected = (await db.execute(
        select(func.count(BatchLifecycle.id)).where(
            BatchLifecycle.merchant_id == merchant.id, BatchLifecycle.remaining_qty > 0,
            BatchLifecycle.inspection_result.is_(None),
        )
    )).scalar() or 0

    # Failed inspection batches
    failed = (await db.execute(
        select(BatchLifecycle).where(
            BatchLifecycle.merchant_id == merchant.id, BatchLifecycle.inspection_result == "fail",
            BatchLifecycle.status != "destroyed",
        )
    )).scalars().all()

    return {"code": 0, "data": {
        "expired_batches": int(expired),
        "locked_batches": int(locked),
        "uninspected_batches": int(uninspected),
        "failed_inspections": [
            {"batch_id": str(b.id), "batch_label": b.batch_label, "status": b.status}
            for b in failed
        ],
        "checklist": [
            {"item": "检查过期批次", "status": "danger" if expired > 0 else "ok"},
            {"item": "检查快检未完成批次", "status": "warn" if uninspected > 0 else "ok"},
            {"item": "检查锁定批次", "status": "danger" if locked > 0 else "ok"},
            {"item": "检查不合格批次是否已处理", "status": "danger" if failed else "ok"},
            {"item": "冷柜温度检查", "status": "pending"},
            {"item": "环境清洁", "status": "pending"},
        ],
    }}


@router.get("/batches/{batch_id}/affected-orders", response_model=AnyResponse)
async def affected_orders(
    batch_id: uuid.UUID,
    merchant: Merchant = Depends(get_current_merchant),
    db: AsyncSession = Depends(get_db),
):
    """Find all sale orders that consumed from this batch (for recall notification)."""
    batch = await db.get(BatchLifecycle, batch_id)
    if not batch or batch.merchant_id != merchant.id:
        raise HTTPException(status_code=404, detail="批次不存在")

    # Find inventory records linked to this batch
    inv_records = (await db.execute(
        select(InventoryRecord).where(
            InventoryRecord.merchant_id == merchant.id,
            InventoryRecord.batch_label == batch.batch_label,
            InventoryRecord.event_type.in_(("sale", "refund")),
        )
    )).scalars().all()

    return {"code": 0, "data": {
        "batch_id": str(batch.id), "batch_label": batch.batch_label,
        "remaining_qty": float(batch.remaining_qty),
        "affected_records": [
            {"record_id": str(r.id), "qty": float(r.quantity), "notes": r.notes,
             "time": r.event_time.isoformat() if r.event_time else None}
            for r in inv_records
        ],
    }}
