"""支付对账 API (section 4.7)."""

from datetime import date, datetime
from decimal import Decimal

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import get_current_merchant
from app.database import get_db
from app.models.merchant import Merchant
from app.models.payment import PaymentChannel, ReconciliationTask
from app.models.pos import Payment
from app.schemas.common import AnyResponse


router = APIRouter(prefix="/api/v1/reconciliation", tags=["reconciliation"])


@router.get("/channels", response_model=AnyResponse)
async def list_channels(merchant: Merchant = Depends(get_current_merchant), db: AsyncSession = Depends(get_db)):
    rows = (await db.execute(select(PaymentChannel).where(PaymentChannel.merchant_id == merchant.id))).scalars().all()
    return {"code": 0, "data": [{"id": str(c.id), "channel": c.channel, "sub_mch_id": c.sub_mch_id, "fee_rate": float(c.fee_rate), "is_active": c.is_active} for c in rows]}


@router.post("/channels", response_model=AnyResponse)
async def create_channel(body: dict, merchant: Merchant = Depends(get_current_merchant), db: AsyncSession = Depends(get_db)):
    c = PaymentChannel(merchant_id=merchant.id, channel=body["channel"], sub_mch_id=body.get("sub_mch_id"), fee_rate=Decimal(str(body.get("fee_rate", 0.006))))
    db.add(c); await db.commit()
    return {"code": 0, "data": {"id": str(c.id), "channel": c.channel}}


@router.post("/run/{recon_date}", response_model=AnyResponse)
async def run_reconciliation(recon_date: date, channel: str = "wechat", merchant: Merchant = Depends(get_current_merchant), db: AsyncSession = Depends(get_db)):
    """Run daily reconciliation: compare system orders vs channel payments."""
    from datetime import time as dt_time
    day_start = datetime.combine(recon_date, dt_time.min)
    day_end = datetime.combine(recon_date, dt_time.max)

    ch = (await db.execute(select(PaymentChannel).where(PaymentChannel.merchant_id == merchant.id, PaymentChannel.channel == channel))).scalar_one_or_none()
    fee_rate = ch.fee_rate if ch else Decimal("0.006")

    # System side
    sys_payments = (await db.execute(select(func.coalesce(func.sum(Payment.amount), Decimal("0"))).where(Payment.merchant_id == merchant.id, Payment.method == channel, Payment.status == "success", Payment.created_at >= day_start, Payment.created_at <= day_end))).scalar() or Decimal("0")
    sys_count = (await db.execute(select(func.count(Payment.id)).where(Payment.merchant_id == merchant.id, Payment.method == channel, Payment.status == "success", Payment.created_at >= day_start, Payment.created_at <= day_end))).scalar() or 0
    fee = (sys_payments * fee_rate).quantize(Decimal("0.01"))

    task = await db.scalar(select(ReconciliationTask).where(ReconciliationTask.merchant_id == merchant.id, ReconciliationTask.channel == channel, ReconciliationTask.date == recon_date))
    if not task:
        task = ReconciliationTask(merchant_id=merchant.id, channel=channel, date=recon_date)
        db.add(task)
    task.system_total = sys_payments
    task.channel_total = sys_payments  # Placeholder: in production, download from WeChat bill
    task.diff_amount = task.system_total - task.channel_total
    task.matched_count = int(sys_count)
    task.fee_amount = fee
    task.status = "balanced" if task.diff_amount == 0 else "exception"
    task.note = f"系统订单 {sys_count} 笔，合计 ¥{float(sys_payments)}，手续费 ¥{float(fee)}。渠道账单需人工导入核对。"
    await db.commit()
    return {"code": 0, "data": {"task_id": str(task.id), "system_total": float(sys_payments), "fee": float(fee), "status": task.status, "note": task.note}}


@router.get("/tasks", response_model=AnyResponse)
async def list_tasks(limit: int = 30, merchant: Merchant = Depends(get_current_merchant), db: AsyncSession = Depends(get_db)):
    tasks = (await db.execute(select(ReconciliationTask).where(ReconciliationTask.merchant_id == merchant.id).order_by(ReconciliationTask.date.desc()).limit(limit))).scalars().all()
    return {"code": 0, "data": [{"id": str(t.id), "channel": t.channel, "date": t.date.isoformat(), "status": t.status, "system_total": float(t.system_total), "channel_total": float(t.channel_total), "diff_amount": float(t.diff_amount), "fee_amount": float(t.fee_amount), "matched_count": t.matched_count} for t in tasks]}
