"""发票发号与幂等落库 — 唯一性由数据库约束兜底（修复正确性 H6 / 并发 H6+M5）。

两个发票生成入口（worker ``generate_invoices`` 与 admin invoices 路由）共用：

- invoice_no 发号：``INV-YYYYMM-NNNN``，序号 = 当月前缀下数据库侧 ``MAX(seq)+1``。
  取代旧实现里 admin 的进程内计数器与 worker 的 ``ts % 10000`` —— 两者在
  进程重启 / 双副本 / 同批多票场景下必然撞 ``invoice_no`` 唯一约束。
  撞号竞态由唯一约束吸收（ON CONFLICT DO NOTHING）后重新 MAX+1 重试。
- 周期幂等：依赖 ``(subscription_id, period_start)`` 唯一约束
  （``uq_invoice_subscription_period``，见 app/models/saas.py），插入走
  方言感知 ``INSERT ... ON CONFLICT DO NOTHING``（参考 app/core/quota.py
  模式）。同订阅同周期并发/重复生成只出一票，后到方回查返回已有票。

两列均可空：手工开票（无订阅 / 无周期）时 NULL 不参与唯一性比较
（PG16 与 SQLite 均按 NULLS DISTINCT 处理），不受该约束影响。
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.saas import Invoice


# 发号竞态重试上限：每次重试重新 MAX+1，正常 1~2 次内收敛
INVOICE_NO_MAX_ATTEMPTS = 6


def invoice_no_prefix(now: datetime) -> str:
    """当月发号前缀，如 ``INV-202608-``。"""
    return f"INV-{now.strftime('%Y%m')}-"


async def next_invoice_no(db: AsyncSession, now: datetime | None = None) -> str:
    """数据库侧发号：当月前缀下已有最大序号 +1。

    不依赖进程状态，重启 / 双副本下发号连续不重复；并发窗口内的撞号由
    ``invoice_no`` 唯一约束兜底（:func:`insert_invoice` 捕获后重试递增）。
    """
    now = now or datetime.now(UTC)
    prefix = invoice_no_prefix(now)
    result = await db.execute(
        select(Invoice.invoice_no).where(Invoice.invoice_no.like(f"{prefix}%"))
    )
    max_seq = 0
    for (no,) in result.all():
        suffix = no[len(prefix) :]
        if suffix.isdigit():
            max_seq = max(max_seq, int(suffix))
    return f"{prefix}{max_seq + 1:04d}"


async def get_invoice_by_period(
    db: AsyncSession, subscription_id: uuid.UUID | None, period_start: datetime | None
) -> Invoice | None:
    """按幂等键 (subscription_id, period_start) 回查已有票（任一为 None 不查）。"""
    if subscription_id is None or period_start is None:
        return None
    return await db.scalar(
        select(Invoice).where(
            Invoice.subscription_id == subscription_id,
            Invoice.period_start == period_start,
        )
    )


async def insert_invoice(
    db: AsyncSession, values: dict[str, Any], *, now: datetime | None = None
) -> tuple[Invoice, bool]:
    """插入发票，返回 ``(实际生效的发票, 是否本次新建)``。

    - 同 (subscription_id, period_start) 已有票 → 不重复出票，返回 (已有票, False)；
    - invoice_no 撞号（并发发号竞态）→ 重新 MAX+1 重试，上限 6 次；
    - 仅用 ON CONFLICT DO NOTHING 在语句级吸收唯一性冲突，不做事务回滚，
      失败尝试不污染外层事务（同批其他插入不受影响）。

    ``values`` 为除 ``invoice_no`` 外的完整列值，缺省 ``id`` 自动生成。
    插入在调用方事务内完成，commit 由调用方负责。
    """
    insert_cls = pg_insert if db.get_bind().dialect.name == "postgresql" else sqlite_insert
    values = {**values}
    invoice_id = values.setdefault("id", uuid.uuid4())
    subscription_id = values.get("subscription_id")
    period_start = values.get("period_start")

    for attempt in range(1, INVOICE_NO_MAX_ATTEMPTS + 1):
        invoice_no = await next_invoice_no(db, now)
        # 不指定冲突目标：周期唯一与 invoice_no 唯一任一冲突都吸收为
        # “本条未插入”，由下方回查区分语义（周期已有票 → 幂等返回；否则按撞号重试）。
        await db.execute(
            insert_cls(Invoice).values(**values, invoice_no=invoice_no).on_conflict_do_nothing()
        )
        inserted = await db.get(Invoice, invoice_id)
        if inserted is not None:
            return inserted, True

        existing = await get_invoice_by_period(db, subscription_id, period_start)
        if existing is not None:
            return existing, False
        if attempt == INVOICE_NO_MAX_ATTEMPTS:
            raise RuntimeError(
                f"invoice_no 发号竞态重试 {INVOICE_NO_MAX_ATTEMPTS} 次仍冲突，已中止"
            )
    raise AssertionError("unreachable")
