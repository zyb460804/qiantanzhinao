"""供应商评分服务 — 采购验收数据自动评分的单一实现。

从采购验收历史计算缺斤率、退货率、质量问题率、准时率和综合评分。
路由层只负责鉴权、持久化与审计；算法统一收敛在这里，避免多套实现漂移。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.purchase import PurchaseItem, PurchaseList


@dataclass
class SupplierScore:
    """供应商评分计算结果。"""

    shortage_rate: Decimal
    return_rate: Decimal
    quality_issue_rate: Decimal
    on_time_rate: Decimal
    composite_score: Decimal
    total_orders: int
    total_expected: Decimal
    total_shortage: Decimal
    total_returned: Decimal


async def calculate_supplier_score(
    db: AsyncSession,
    merchant_id: UUID,
    supplier_id: UUID,
) -> SupplierScore | None:
    """根据采购验收历史计算供应商质量指标。

    无采购记录时返回 None，由路由层按“暂无采购记录，无法评分”处理。
    """
    items = (
        (
            await db.execute(
                select(PurchaseItem).where(
                    PurchaseItem.merchant_id == merchant_id,
                    PurchaseItem.supplier_id == supplier_id,
                )
            )
        )
        .scalars()
        .all()
    )

    if not items:
        return None

    # Count distinct purchase lists
    list_ids = set(item.list_id for item in items if item.list_id)

    # 批量预加载相关采购单的预计到货时间，用于判定是否迟到。
    expected_by_list: dict[UUID, datetime | None] = {}
    if list_ids:
        list_result = await db.execute(select(PurchaseList).where(PurchaseList.id.in_(list_ids)))
        for pl in list_result.scalars().all():
            expected_by_list[pl.id] = pl.expected_arrival_date

    total_expected = Decimal("0")
    total_shortage = Decimal("0")
    total_returned = Decimal("0")
    total_damaged = Decimal("0")
    accepted_count = 0
    quality_ok_count = 0
    late_count = 0

    for item in items:
        qty = item.actual_qty or Decimal("0")
        if qty > 0:
            total_expected += qty
        shortage = item.shortage_qty or Decimal("0")
        if shortage > 0:
            total_shortage += shortage
        returned = item.returned_qty or Decimal("0")
        if returned > 0:
            total_returned += returned
        damaged = item.damaged_qty or Decimal("0")
        if damaged > 0:
            total_damaged += damaged
        if item.accepted_at is not None:
            accepted_count += 1
            if item.quality_ok:
                quality_ok_count += 1
            # 以 item.accepted_at vs plist.expected_arrival_date 判定迟到。
            # 仅当采购单设置了预计到货时间且实际验收晚于该时间才计入迟到；
            # 未设预计到货时间的单据不参与迟到率计算（不谎报 100%）。
            expected_arrival = expected_by_list.get(item.list_id) if item.list_id else None
            if expected_arrival is not None and item.accepted_at > expected_arrival:
                late_count += 1

    # Calculate rates (0-100)
    shortage_rate = (total_shortage / total_expected * 100) if total_expected > 0 else Decimal("0")
    return_rate = (total_returned / total_expected * 100) if total_expected > 0 else Decimal("0")
    quality_issue_rate = (
        Decimal("100") - (Decimal(str(quality_ok_count)) / Decimal(str(accepted_count)) * 100)
        if accepted_count > 0
        else Decimal("0")
    )
    on_time_rate = Decimal("100")  # Default 100%, adjusted if late deliveries detected
    if accepted_count > 0 and late_count > 0:
        on_time_rate = Decimal("100") - (
            Decimal(str(late_count)) / Decimal(str(accepted_count)) * Decimal("100")
        )

    # Composite score (0-100, higher = better)
    composite = Decimal("100")
    composite -= shortage_rate * Decimal("0.25")
    composite -= return_rate * Decimal("0.25")
    composite -= quality_issue_rate * Decimal("0.35")
    composite -= (Decimal("100") - on_time_rate) * Decimal("0.15")
    composite = max(Decimal("0"), min(Decimal("100"), composite))

    return SupplierScore(
        shortage_rate=shortage_rate,
        return_rate=return_rate,
        quality_issue_rate=quality_issue_rate,
        on_time_rate=on_time_rate,
        composite_score=composite,
        total_orders=len(list_ids),
        total_expected=total_expected,
        total_shortage=total_shortage,
        total_returned=total_returned,
    )
