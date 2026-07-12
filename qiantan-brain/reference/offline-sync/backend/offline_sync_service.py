"""
offline_sync_service.py — 离线记账幂等落库服务（参考实现 / 教学样例）
────────────────────────────────────────────────────────────────────────
对应 PRD §4.1.2 幂等键 / P0-4 后端幂等落库 / §4.1.4 冲突处理 / R3 审计留痕。

核心原则（资深评审要点）：
  1. 幂等由「(merchant_id, client_id) 唯一约束」兜底，而非仅前端去重。
  2. 并发安全：先查后插仍可能 race，所以插入时 catch IntegrityError 再读一次，
     保证「唯一约束」这道最后防线真正生效（教科书级幂等写法）。
  3. 多租户隔离：所有查询都带 merchant_id；跨商户的 client_id 在本商户查不到，
     自然返回 404（绝不返回他人数据）。
  4. 业务冲突（如目标批次已作废）不静默写入，抛 ConflictError 交由冲突中心(R6)。
  5. 每条成功同步写 AuditLog（R3）：含 client_id、来源、入队/同步时间、结果、kind。
"""

from __future__ import annotations

import uuid
from typing import Optional

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.audit import AuditLog
from app.models.inventory import InventoryRecord

# 纯函数决策抽到 idempotency.py（零依赖、可单测、前后端可共用同一份逻辑）
from idempotency import SyncResult, decide_upsert


class ConflictError(Exception):
    """业务冲突：目标已被其他端作废/删除（PRD §4.1.4）。"""

    def __init__(self, message: str, client_id: str):
        super().__init__(message)
        self.message = message
        self.client_id = client_id


# 注：SyncResult / decide_upsert 已抽到 idempotency.py（纯函数、零依赖）。
# 这样离线幂等决策既能单测，又能让小程序端复用同一份判定，避免双份逻辑漂移（PRD D2）。


# ── 业务冲突预检（示例接缝）──────────────────────────────────────────────
async def _check_business_conflict(
    db: AsyncSession, payload: dict, kind: str
) -> Optional[str]:
    """示例：若记账引用了某个已作废的批次，则业务冲突。

    真实实现应按 kind 去校验（如 purchase 引用的批次、sale 引用的库存），
    这里仅展示接缝，默认无冲突。
    """
    batch_id = payload.get("batch_id")
    if batch_id is None:
        return None
    # 真实代码：query Batch where id=batch_id and is_voided=true → 返回冲突原因
    return None


# ── 组装记录（演示：从 payload 映射到 InventoryRecord）────────────────────
def _build_record(
    *, merchant_id: uuid.UUID, client_id: str, kind: str, payload: dict, source: str
) -> InventoryRecord:
    # 注：真实代码需把 payload['product'] 名称解析为 product_categories.id；
    # 这里用占位，重点展示 client_id 与金额的落库。
    return InventoryRecord(
        merchant_id=merchant_id,
        client_id=client_id,
        product_id=int(payload.get("product_id", 0)),
        quantity=float(payload.get("quantity", 0)),
        unit=payload.get("unit", "斤"),
        total_amount=float(payload["total_amount"]) if payload.get("total_amount") is not None else None,
        event_type=payload.get("event_type", "sale"),
        event_time=__import__("datetime").datetime.now(),
        source=source,
    )


# ── 审计留痕（R3）────────────────────────────────────────────────────────
def _build_audit(
    *,
    merchant_id: uuid.UUID,
    client_id: str,
    record_id: uuid.UUID,
    kind: str,
    result: str,
    device_fp: str = "offline-sync",
) -> AuditLog:
    return AuditLog(
        merchant_id=merchant_id,
        action="offline_sync",
        target_table="inventory_records",
        target_id=str(record_id),
        before_data=None,
        after_data={
            "client_id": client_id,
            "kind": kind,
            "result": result,  # synced | conflict
            "source_device": device_fp,
        },
        reason=f"离线记账同步: {result}",
        operator="offline-engine",
    )


async def _find_by_client_id(
    db: AsyncSession, merchant_id: uuid.UUID, client_id: str
) -> Optional[InventoryRecord]:
    # 多租户隔离：只在本商户范围内查
    res = await db.execute(
        select(InventoryRecord).where(
            InventoryRecord.merchant_id == merchant_id,
            InventoryRecord.client_id == client_id,
        )
    )
    return res.scalar_one_or_none()


# ── 主流程：幂等落库 ───────────────────────────────────────────────────────
async def upsert_offline_record(
    db: AsyncSession,
    *,
    merchant_id: uuid.UUID,
    client_id: str,
    kind: str,
    payload: dict,
    source: str = "offline",
) -> SyncResult:
    # 1) 先查本商户下是否已存在该 client_id
    existing = await _find_by_client_id(db, merchant_id, client_id)
    if existing is not None:
        # 幂等：返回既存记录，HTTP 200 不重复处理（PRD §4.1.2）
        return SyncResult(
            client_id=client_id, status="duplicate", record_id=existing.id,
            message="已存在（幂等去重）",
        )

    # 2) 业务冲突预检（不静默写入）
    conflict = await _check_business_conflict(db, payload, kind)
    if conflict:
        raise ConflictError(conflict, client_id)

    # 3) 创建；用唯一约束做最后一道并发兜底
    try:
        record = _build_record(
            merchant_id=merchant_id, client_id=client_id, kind=kind, payload=payload, source=source
        )
        db.add(record)
        await db.flush()
        await db.commit()
        # 4) 审计留痕（R3）
        db.add(_build_audit(
            merchant_id=merchant_id, client_id=client_id,
            record_id=record.id, kind=kind, result="synced",
        ))
        await db.commit()
        return SyncResult(client_id=client_id, status="created", record_id=record.id)
    except IntegrityError:
        # 并发兜底：另一协程已插入同一 client_id → 视为 duplicate
        await db.rollback()
        existing2 = await _find_by_client_id(db, merchant_id, client_id)
        if existing2 is not None:
            return SyncResult(
                client_id=client_id, status="duplicate", record_id=existing2.id,
                message="并发去重（唯一约束兜底）",
            )
        raise
