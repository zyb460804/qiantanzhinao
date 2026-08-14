"""语音账本共享服务 —— voice 路由与 inventory 撤销入口共用的冲正核心。

第五轮并发缝合（V2-H1）：voice.py 的 void/edit 与 inventory.py 的
record-void 两条路径都能撤销同一条 source="voice" 的库存流水。修复前
两条路径互不加对方的锚点锁（voice 只锁 VoiceLog、inventory 只锁
InventoryRecord），并发交错时会对同一条流水做两次批次回滚 +
两次往来账冲销（幻影库存 / 双倍应收）。

共享约定（两条入口都必须遵守）：
  加锁次序 = VoiceLog 行锁 → InventoryRecord 行锁 → 批次行锁（id 升序）。
- ``sync_voice_receivables``：把语音单名下往来账净额对齐到目标
  （撤销 → 归零；修改 → 修正后金额），从 voice.py 下沉至此。
- ``void_voice_confirmed_record``：调用方已持有 VoiceLog 行锁的前提下，
  完成批次回滚 + 流水作废 + 状态翻转 + 往来账冲销 + 审计。
"""

from __future__ import annotations

from decimal import Decimal

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.timezone import utc_now
from app.models.accounts import CustomerReceivable
from app.models.audit import AuditLog
from app.models.inventory import InventoryRecord
from app.models.voice import VoiceLog
from app.services.accounts_service import record_customer_receivable
from app.services.batch import BatchRollbackSummary, rollback_batch_on_void


async def sync_voice_receivables(
    db: AsyncSession,
    log: VoiceLog,
    *,
    adjustment_key: str,
    target_party: str | None = None,
    target_net: Decimal | None = None,
) -> None:
    """把该语音单名下的往来账净额对齐到目标（撤销 → 归零；修改 → 修正后金额）。

    confirm 按 ``voice:{log.id}:charge/repay`` 幂等键落 CustomerReceivable；
    撤销/修改若只回滚库存不反向冲账，应收会永久挂死。这里按同一前缀聚合
    每个客户的当前净额（charge 为正、repay 为负），用 accounts_service
    现有的 record_customer_receivable 写入差额记录，不新增模型字段。

    adjustment_key 携带操作序号（void 只发生一次；edit 按次数递增），
    因此重复编辑不会双重冲销，也不会撞幂等唯一约束。
    """
    rows = (
        (
            await db.execute(
                select(CustomerReceivable).where(
                    CustomerReceivable.merchant_id == log.merchant_id,
                    CustomerReceivable.idempotency_key.startswith(
                        f"voice:{log.id}:", autoescape=True
                    ),
                )
            )
        )
        .scalars()
        .all()
    )
    net_by_customer: dict[str, Decimal] = {}
    for row in rows:
        sign = Decimal("1") if row.direction == "charge" else Decimal("-1")
        net_by_customer[row.customer_name] = (
            net_by_customer.get(row.customer_name, Decimal("0")) + sign * row.amount
        )

    targets: dict[str, Decimal] = {target_party: target_net} if target_party and target_net else {}

    for name in set(net_by_customer) | set(targets):
        delta = targets.get(name, Decimal("0")) - net_by_customer.get(name, Decimal("0"))
        if delta > 0:
            await record_customer_receivable(
                db,
                merchant_id=log.merchant_id,
                customer_name=name,
                amount=delta,
                direction="charge",
                note=f"语音冲正 {log.id}",
                idempotency_key=f"{adjustment_key}:charge",
            )
        elif delta < 0:
            await record_customer_receivable(
                db,
                merchant_id=log.merchant_id,
                customer_name=name,
                amount=-delta,
                direction="repay",
                note=f"语音冲销 {log.id}",
                idempotency_key=f"{adjustment_key}:repay",
            )


async def void_voice_confirmed_record(
    db: AsyncSession,
    log: VoiceLog,
    reason: str,
    *,
    operator: str = "merchant",
    voided_by: str = "voice",
) -> tuple[InventoryRecord | None, BatchRollbackSummary | None]:
    """撤销一条已确认语音单的共享核心（voice 路由与 inventory 路由共用）。

    前置条件：调用方必须已对 ``log`` 行持有 FOR UPDATE 锁，保证
    VoiceLog → InventoryRecord → 批次 的统一加锁次序（跨入口无 ABBA）。

    Returns:
        (record, batch_summary)：正常撤销，含回滚摘要。
        (None, None)：该语音单没有未作废的库存流水（仅翻转状态）。

    Raises:
        HTTPException 409：状态冲突（已撤销 / 未确认 / POS 流水）。
    """
    if log.status == "voided":
        raise HTTPException(status_code=409, detail="该记录已撤销，无需重复操作")
    if log.status != "confirmed":
        raise HTTPException(status_code=409, detail="只能撤销已确认的记录")

    record = (
        (
            await db.execute(
                select(InventoryRecord)
                .where(
                    InventoryRecord.voice_log_id == log.id,
                    InventoryRecord.is_voided.is_(False),
                )
                .with_for_update()
            )
        )
        .scalars()
        .first()
    )

    if record and record.source == "pos":
        # 订单体系另有完整退款链路（pos.py refund），语音撤销不得绕过其核销逻辑。
        raise HTTPException(
            status_code=409,
            detail="POS订单流水不支持语音撤销，请通过订单退款链路处理",
        )

    if not record:
        log.status = "voided"
        return None, None

    before_data = {
        "quantity": float(record.quantity),
        "event_type": record.event_type,
        "product_id": record.product_id,
        "total_amount": float(record.total_amount) if record.total_amount else None,
    }

    batch_summary = await rollback_batch_on_void(db, log.merchant_id, record.product_id, record)
    # 审计 JSON 列不能存 Decimal，与 inventory.py 撤销路由同款处理。
    audit_summary = {**batch_summary, "qty_adjusted": float(batch_summary["qty_adjusted"])}

    record.is_voided = True
    record.voided_at = utc_now()
    record.void_reason = reason
    record.voided_by = voided_by

    log.status = "voided"

    # 往来账冲销：confirm 按 voice:{log.id}:charge/repay 落的赊账/回款流水，
    # 按同一前缀聚合净额后反向冲平，否则撤销后应收永久挂死。
    # adjustment_key 与入口无关（voice/inventory 两路收敛到同一组幂等键）。
    await sync_voice_receivables(db, log, adjustment_key=f"voice:{log.id}:void")

    db.add(
        AuditLog(
            merchant_id=log.merchant_id,
            action="void",
            target_table="inventory_records",
            target_id=str(record.id),
            before_data=before_data,
            after_data={"is_voided": True, "batch_summary": audit_summary},
            reason=reason,
            operator=operator,
        )
    )
    return record, batch_summary
