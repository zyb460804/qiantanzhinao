"""支付渠道账单导入与逐笔对账 API (section 4.7)."""

import uuid
from datetime import date
from decimal import Decimal
from typing import Literal

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import get_current_merchant
from app.database import get_db
from app.models.audit import AuditLog
from app.models.merchant import Merchant
from app.models.payment import (
    ChannelBillImport,
    PaymentChannel,
    ReconciliationDifference,
    ReconciliationTask,
)
from app.schemas.common import AnyResponse
from app.schemas.reconciliation import ResolveDifferenceRequest
from app.services.channel_bill_download import (
    ChannelBillDownloadError,
    download_channel_bill,
)
from app.services.reconciliation import (
    BillParseError,
    get_or_create_task,
    import_channel_bill_file,
    reconcile_task,
)


router = APIRouter(prefix="/api/v1/reconciliation", tags=["reconciliation"])
MAX_BILL_FILE_SIZE = 10 * 1024 * 1024
ChannelName = Literal["wechat", "alipay"]


async def _channel_fee_rate(
    db: AsyncSession,
    merchant_id: uuid.UUID,
    channel: str,
) -> Decimal:
    configured = await db.scalar(
        select(PaymentChannel.fee_rate).where(
            PaymentChannel.merchant_id == merchant_id,
            PaymentChannel.channel == channel,
            PaymentChannel.is_active.is_(True),
        )
    )
    return configured if configured is not None else Decimal("0.006")


@router.get("/channels", response_model=AnyResponse)
async def list_channels(
    merchant: Merchant = Depends(get_current_merchant), db: AsyncSession = Depends(get_db)
):
    rows = (
        (await db.execute(select(PaymentChannel).where(PaymentChannel.merchant_id == merchant.id)))
        .scalars()
        .all()
    )
    return {
        "code": 0,
        "data": [
            {
                "id": str(c.id),
                "channel": c.channel,
                "sub_mch_id": c.sub_mch_id,
                "fee_rate": float(c.fee_rate),
                "is_active": c.is_active,
            }
            for c in rows
        ],
    }


@router.post("/channels", response_model=AnyResponse)
async def create_channel(
    body: dict,
    merchant: Merchant = Depends(get_current_merchant),
    db: AsyncSession = Depends(get_db),
):
    c = PaymentChannel(
        merchant_id=merchant.id,
        channel=body["channel"],
        sub_mch_id=body.get("sub_mch_id"),
        fee_rate=Decimal(str(body.get("fee_rate", 0.006))),
    )
    db.add(c)
    await db.commit()
    return {"code": 0, "data": {"id": str(c.id), "channel": c.channel}}


@router.post("/run/{recon_date}", response_model=AnyResponse)
async def run_reconciliation(
    recon_date: date,
    channel: ChannelName = "wechat",
    merchant: Merchant = Depends(get_current_merchant),
    db: AsyncSession = Depends(get_db),
):
    """Re-run matching against all imported entries for a channel/day."""
    task = await get_or_create_task(db, merchant.id, channel, recon_date)
    result = await reconcile_task(
        db,
        task,
        fee_rate=await _channel_fee_rate(db, merchant.id, channel),
    )
    await db.commit()
    return {
        "code": 0,
        "data": {
            "task_id": str(task.id),
            **{
                key: float(value) if isinstance(value, Decimal) else value
                for key, value in result.items()
            },
            "fee": float(result["fee_amount"]),
            "note": task.note,
        },
    }


@router.post("/import/{recon_date}", response_model=AnyResponse)
async def import_channel_bill(
    recon_date: date,
    channel: ChannelName = "wechat",
    file: UploadFile = File(...),
    merchant: Merchant = Depends(get_current_merchant),
    db: AsyncSession = Depends(get_db),
):
    """Import a normalized or common WeChat/Alipay CSV statement and reconcile it."""
    content = await file.read(MAX_BILL_FILE_SIZE + 1)
    if not content:
        raise HTTPException(status_code=400, detail="账单文件不能为空")
    if len(content) > MAX_BILL_FILE_SIZE:
        raise HTTPException(status_code=413, detail="账单文件不能超过10MB")
    try:
        bill_import, duplicate, result = await import_channel_bill_file(
            db,
            merchant_id=merchant.id,
            channel=channel,
            bill_date=recon_date,
            file_name=file.filename or "channel-bill.csv",
            content=content,
            fee_rate=await _channel_fee_rate(db, merchant.id, channel),
        )
    except BillParseError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    await db.commit()
    return {
        "code": 0,
        "data": {
            "import_id": str(bill_import.id),
            "task_id": str(bill_import.task_id),
            "duplicate": duplicate,
            "file_name": bill_import.file_name,
            "row_count": bill_import.row_count,
            "inserted_count": bill_import.inserted_count,
            "duplicate_count": bill_import.duplicate_count,
            **{
                key: float(value) if isinstance(value, Decimal) else value
                for key, value in result.items()
            },
        },
    }


@router.post("/download/{recon_date}", response_model=AnyResponse)
async def download_and_reconcile_channel_bill(
    recon_date: date,
    channel: ChannelName = "wechat",
    merchant: Merchant = Depends(get_current_merchant),
    db: AsyncSession = Depends(get_db),
):
    """Download a statement from the configured provider and reconcile it."""
    try:
        content = await download_channel_bill(channel, recon_date)
        bill_import, duplicate, result = await import_channel_bill_file(
            db,
            merchant_id=merchant.id,
            channel=channel,
            bill_date=recon_date,
            file_name=f"{channel}-{recon_date.isoformat()}.csv",
            content=content,
            fee_rate=await _channel_fee_rate(db, merchant.id, channel),
        )
    except (ChannelBillDownloadError, BillParseError) as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    await db.commit()
    return {
        "code": 0,
        "data": {
            "import_id": str(bill_import.id),
            "task_id": str(bill_import.task_id),
            "duplicate": duplicate,
            **{
                key: float(value) if isinstance(value, Decimal) else value
                for key, value in result.items()
            },
        },
    }


@router.get("/imports", response_model=AnyResponse)
async def list_bill_imports(
    channel: ChannelName | None = None,
    bill_date: date | None = None,
    limit: int = 30,
    merchant: Merchant = Depends(get_current_merchant),
    db: AsyncSession = Depends(get_db),
):
    filters = [ChannelBillImport.merchant_id == merchant.id]
    if channel is not None:
        filters.append(ChannelBillImport.channel == channel)
    if bill_date is not None:
        filters.append(ChannelBillImport.bill_date == bill_date)
    imports = (
        (
            await db.execute(
                select(ChannelBillImport)
                .where(*filters)
                .order_by(ChannelBillImport.created_at.desc())
                .limit(min(max(limit, 1), 100))
            )
        )
        .scalars()
        .all()
    )
    return {
        "code": 0,
        "data": [
            {
                "id": str(item.id),
                "task_id": str(item.task_id),
                "channel": item.channel,
                "bill_date": item.bill_date.isoformat(),
                "file_name": item.file_name,
                "row_count": item.row_count,
                "inserted_count": item.inserted_count,
                "duplicate_count": item.duplicate_count,
                "created_at": item.created_at.isoformat() if item.created_at else None,
            }
            for item in imports
        ],
    }


@router.get("/tasks/{task_id}/differences", response_model=AnyResponse)
async def list_differences(
    task_id: uuid.UUID,
    status: Literal["open", "resolved", "ignored"] | None = None,
    merchant: Merchant = Depends(get_current_merchant),
    db: AsyncSession = Depends(get_db),
):
    task = await db.scalar(
        select(ReconciliationTask).where(
            ReconciliationTask.id == task_id,
            ReconciliationTask.merchant_id == merchant.id,
        )
    )
    if task is None:
        raise HTTPException(status_code=404, detail="对账任务不存在")
    filters = [ReconciliationDifference.task_id == task.id]
    if status is not None:
        filters.append(ReconciliationDifference.status == status)
    differences = (
        (
            await db.execute(
                select(ReconciliationDifference)
                .where(*filters)
                .order_by(ReconciliationDifference.created_at, ReconciliationDifference.id)
            )
        )
        .scalars()
        .all()
    )
    return {
        "code": 0,
        "data": [
            {
                "id": str(item.id),
                "diff_type": item.diff_type,
                "system_ref": item.system_ref,
                "channel_ref": item.channel_ref,
                "system_amount": float(item.system_amount)
                if item.system_amount is not None
                else None,
                "channel_amount": float(item.channel_amount)
                if item.channel_amount is not None
                else None,
                "status": item.status,
                "resolution": item.resolution,
            }
            for item in differences
        ],
    }


@router.post("/differences/{difference_id}/resolve", response_model=AnyResponse)
async def resolve_difference(
    difference_id: uuid.UUID,
    body: ResolveDifferenceRequest,
    merchant: Merchant = Depends(get_current_merchant),
    db: AsyncSession = Depends(get_db),
):
    difference = await db.scalar(
        select(ReconciliationDifference).where(
            ReconciliationDifference.id == difference_id,
            ReconciliationDifference.merchant_id == merchant.id,
        )
    )
    if difference is None:
        raise HTTPException(status_code=404, detail="对账差异不存在")
    difference.status = body.status
    difference.resolution = body.resolution.strip()
    await db.flush()

    remaining = await db.scalar(
        select(func.count(ReconciliationDifference.id)).where(
            ReconciliationDifference.task_id == difference.task_id,
            ReconciliationDifference.status == "open",
        )
    )
    task = await db.get(ReconciliationTask, difference.task_id)
    if task is not None and not remaining:
        task.status = "balanced" if abs(task.diff_amount) <= Decimal("0.01") else "resolved"

    db.add(
        AuditLog(
            merchant_id=merchant.id,
            action="reconciliation_difference_resolve",
            target_table="reconciliation_differences",
            target_id=str(difference.id),
            after_data={"status": body.status, "resolution": difference.resolution},
            reason=difference.resolution,
            operator="merchant",
        )
    )
    await db.commit()
    return {
        "code": 0,
        "data": {
            "difference_id": str(difference.id),
            "status": difference.status,
            "task_status": task.status if task is not None else None,
        },
    }


@router.get("/tasks", response_model=AnyResponse)
async def list_tasks(
    limit: int = 30,
    merchant: Merchant = Depends(get_current_merchant),
    db: AsyncSession = Depends(get_db),
):
    tasks = (
        (
            await db.execute(
                select(ReconciliationTask)
                .where(ReconciliationTask.merchant_id == merchant.id)
                .order_by(ReconciliationTask.date.desc())
                .limit(limit)
            )
        )
        .scalars()
        .all()
    )
    return {
        "code": 0,
        "data": [
            {
                "id": str(t.id),
                "channel": t.channel,
                "date": t.date.isoformat(),
                "status": t.status,
                "system_total": float(t.system_total),
                "channel_total": float(t.channel_total),
                "diff_amount": float(t.diff_amount),
                "fee_amount": float(t.fee_amount),
                "matched_count": t.matched_count,
                "unmatched_system": t.unmatched_system,
                "unmatched_channel": t.unmatched_channel,
                "note": t.note,
            }
            for t in tasks
        ],
    }
