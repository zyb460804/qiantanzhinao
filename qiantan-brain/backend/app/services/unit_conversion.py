"""跨模块单位换算服务 — 语音 / POS / 库存边界的统一入口。

为什么需要独立服务（而不是各路由各写一份）：
- 采购按「箱/筐/袋」整件入账、销售按「斤/个」零售是菜摊常态，
  换算因子挂在 SKU 上（一箱番茄 20 斤 ≠ 通用一筐 45 斤），
  读取规则（SKU 专属优先 → 商户通用兜底）必须全项目只有一份实现。
- 所有运算走 Decimal（红线 #7：数量/金额禁止 float 累加误差）。

跨代理契约（别路代理按此 import，签名不得改动）：
    convert_to_base_unit(session, sku_id, quantity, from_unit)
    返回 (换算后数量, 基准单位)；SKU 未配置该换算时返回 None。
"""

from __future__ import annotations

import uuid
from decimal import Decimal, InvalidOperation
from typing import Any

from sqlalchemy import select

from app.models.catalog import ProductSKU, UnitConversion


def _to_decimal(quantity: Any) -> Decimal:
    """int/float/str/Decimal → Decimal（str 中转，避免 float 二进制误差）。"""
    if isinstance(quantity, Decimal):
        return quantity
    if isinstance(quantity, bool) or quantity is None:
        raise ValueError(f"数量类型非法: {quantity!r}")
    try:
        return Decimal(str(quantity))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"数量格式非法: {quantity!r}") from exc


async def convert_to_base_unit(session, sku_id, quantity, from_unit: str):
    """返回 (换算后数量, 基准单位)；SKU 未配置该换算时返回 None。Decimal 运算。"""
    if not isinstance(sku_id, uuid.UUID):
        sku_id = uuid.UUID(str(sku_id))
    unit = (from_unit or "").strip()
    if not unit:
        return None

    sku = await session.get(ProductSKU, sku_id)
    if sku is None or not sku.is_active:
        return None

    base_unit = sku.canonical_unit
    amount = _to_decimal(quantity)

    # from_unit 即基准单位：恒等换算，无需任何配置。
    if unit == base_unit:
        return (amount, base_unit)

    conditions = (
        UnitConversion.merchant_id == sku.merchant_id,
        UnitConversion.from_unit == unit,
        # to_unit 必须落在该 SKU 的账本标准单位上：SKU 以公斤记账时，
        # 一条「筐→斤」换算对它是错方向的配置，不能拿来用。
        UnitConversion.to_unit == base_unit,
    )

    # SKU 专属因子优先（因子随商品不同），商户通用换算兜底（公斤→斤）。
    # uq_unit_conv 对 sku_id IS NULL 不去重（NULLS DISTINCT），通用换算
    # 历史上可能存在多条，取 created_at 最新一条保证结果确定。
    conv = await session.scalar(
        select(UnitConversion)
        .where(*conditions, UnitConversion.sku_id == sku.id)
        .order_by(UnitConversion.created_at.desc(), UnitConversion.id.desc())
        .limit(1)
    )
    if conv is None:
        conv = await session.scalar(
            select(UnitConversion)
            .where(*conditions, UnitConversion.sku_id.is_(None))
            .order_by(UnitConversion.created_at.desc(), UnitConversion.id.desc())
            .limit(1)
        )
    if conv is None:
        return None

    return (amount * Decimal(str(conv.factor)), base_unit)
