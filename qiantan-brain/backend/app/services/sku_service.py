"""SKU 解析服务（P0-B：取消 category 孤儿化）。

背景：账本三表（inventory_records / purchase_items / batch_lifecycles）的
product_id 当前指向全局共享的 product_categories（整数），与每商户的
ProductSKU 脱钩——SKU 体系建成却落不了账本。本服务提供：

- resolve_sku_id: 由商品名（含别名）或 category id 解析出本商户的 SKU id。
- ensure_sku_for_category: 若不存在则按品类名为商户补建一个 SKU（供 backfill）。

解析不到时返回 None（账本 sku_id 可空，向后兼容）；写入路径应尽量填充，
否则 AI 建议 / 单位换算 / 溯源的准确度无法保证。
"""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.catalog import ProductAlias, ProductSKU
from app.models.product import ProductCategory


async def resolve_sku_id(
    db: AsyncSession,
    merchant_id: uuid.UUID,
    product_name: str | None = None,
    product_id: int | None = None,
) -> uuid.UUID | None:
    """解析本商户的 SKU id。

    优先级：① 按标准名精确匹配 → ② 按别名匹配 → ③ 由 category id 取品类名再匹配。
    均无则返回 None。
    """
    if product_name:
        name = product_name.strip()
        q = select(ProductSKU).where(
            ProductSKU.merchant_id == merchant_id,
            ProductSKU.name == name,
            ProductSKU.is_active == True,  # noqa: E712
        )
        sku = (await db.execute(q)).scalar_one_or_none()
        if sku:
            return sku.id
        # 别名匹配（如 西红柿 → 番茄）
        qa = (
            select(ProductSKU)
            .join(ProductAlias, ProductAlias.sku_id == ProductSKU.id)
            .where(
                ProductAlias.merchant_id == merchant_id,
                ProductAlias.alias == name,
            )
        )
        sku = (await db.execute(qa)).scalar_one_or_none()
        if sku:
            return sku.id

    if product_id is not None:
        cat = await db.get(ProductCategory, product_id)
        if cat and cat.name:
            return await resolve_sku_id(db, merchant_id, product_name=cat.name)

    return None


async def ensure_sku_for_category(
    db: AsyncSession,
    merchant_id: uuid.UUID,
    product_id: int,
) -> uuid.UUID | None:
    """确保某品类在本商户下存在对应 SKU，返回其 id（get-or-create）。

    用于 backfill 脚本：把历史 category 账本补上 sku_id。
    注意：ProductSKU 当前无 (merchant_id, name) 唯一约束，并发场景可能重复，
    但 backfill 为一次性串行脚本，可接受；后续可加唯一约束收口。
    """
    sku_id = await resolve_sku_id(db, merchant_id, product_id=product_id)
    if sku_id:
        return sku_id
    cat = await db.get(ProductCategory, product_id)
    if not cat or not cat.name:
        return None
    sku = ProductSKU(
        merchant_id=merchant_id,
        name=cat.name,
        category_group=cat.category_group,
        canonical_unit=cat.unit or "斤",
        shelf_life_hours=cat.shelf_life_hours or 72,
        default_sale_price=cat.default_price,
    )
    db.add(sku)
    await db.flush()
    return sku.id
