"""种子分片：商户 + 商品目录 + SKU + 别名 + 单位 + 供应商档案。

这是所有其它分片的基座：先建目录，后续库存/订单/批次才能引用。
幂等：按固定主键 / 业务唯一键判重。
"""

from __future__ import annotations

import json
from decimal import Decimal

from sqlalchemy import select

from app.models.catalog import (
    ProductAlias,
    ProductSKU,
    ProductSpecification,
    Supplier,
    SupplierProduct,
    Unit,
    UnitConversion,
)
from app.models.merchant import Merchant
from app.models.product import ProductCategory
from scripts.seed_data.common import (
    ALL_MERCHANT_IDS,
    DEMO_TENANT_ID,
    MERCHANTS,
    PRODUCTS,
    SUPPLIERS,
    money,
    products_for,
    sku_uuid,
    supplier_id_for,
)


async def _get_or_create(db, model, pk, **fields):
    """按主键判重。"""
    obj = await db.get(model, pk)
    if obj is not None:
        return obj, False
    obj = model(id=pk, **fields)
    db.add(obj)
    await db.flush()
    return obj, True


async def seed_merchants(db) -> None:
    """三摊主 + 绑定演示租户。"""
    for profile in MERCHANTS:
        merchant, created = await _get_or_create(
            db,
            Merchant,
            profile.merchant_id,
            name=profile.name,
            business_type=profile.business_type,
            location=profile.location,
        )
        if getattr(merchant, "tenant_id", None) is None:
            merchant.tenant_id = DEMO_TENANT_ID
        print(f"  {'[+]' if created else '[=]'} 商户: {profile.name}")


async def seed_product_categories(db) -> None:
    """全局商品目录（name 唯一）。旧 10 个保留原 id，向后兼容。"""
    for p in PRODUCTS:
        if await db.get(ProductCategory, p.id) is not None:
            continue
        db.add(
            ProductCategory(
                id=p.id,
                name=p.name,
                unit=p.unit,
                default_price=p.default_price,
                shelf_life_hours=p.shelf_life_hours,
                category_group=p.group,
                is_active=True,
            )
        )
    await db.flush()
    print(f"  [+] 商品目录: {len(PRODUCTS)} 个品类")


async def seed_skus_and_aliases(db) -> None:
    """每商户 × 其经营商品 → SKU；带别名（方言 ASR 演示）+ 规格（分级定价）。"""
    n_sku = n_alias = n_spec = 0
    for merchant_id in ALL_MERCHANT_IDS:
        for p in products_for(merchant_id):
            sid = sku_uuid(merchant_id, p.id)
            if await db.get(ProductSKU, sid) is None:
                db.add(
                    ProductSKU(
                        id=sid,
                        merchant_id=merchant_id,
                        name=p.name,
                        category_group=p.group,
                        canonical_unit=p.unit,
                        shelf_life_hours=p.shelf_life_hours,
                        default_sale_price=p.default_price,
                        is_active=True,
                    )
                )
                n_sku += 1

            for alias in p.aliases:
                exists = await db.execute(
                    select(ProductAlias.id)
                    .where(ProductAlias.merchant_id == merchant_id)
                    .where(ProductAlias.alias == alias)
                )
                if exists.first() is None:
                    db.add(
                        ProductAlias(
                            merchant_id=merchant_id,
                            sku_id=sid,
                            alias=alias,
                            is_system=True,
                        )
                    )
                    n_alias += 1

    # 规格分级定价：番茄大果/小果、草莓精品
    spec_defs = [
        (ALL_MERCHANT_IDS[0], 6, "大果", money("5.00")),
        (ALL_MERCHANT_IDS[0], 6, "小果", money("-1.00")),
        (ALL_MERCHANT_IDS[1], 14, "精品", money("8.00")),
    ]
    for merchant_id, pid, name, delta in spec_defs:
        sid = sku_uuid(merchant_id, pid)
        exists = await db.execute(
            select(ProductSpecification.id)
            .where(ProductSpecification.sku_id == sid)
            .where(ProductSpecification.name == name)
        )
        if exists.first() is None:
            db.add(
                ProductSpecification(
                    merchant_id=merchant_id,
                    sku_id=sid,
                    name=name,
                    price_delta=delta,
                )
            )
            n_spec += 1

    await db.flush()
    print(f"  [+] SKU: {n_sku}, 别名: {n_alias}, 规格: {n_spec}")


async def seed_units(db) -> None:
    """单位字典 + 常用换算（采购按筐、销售按斤）。"""
    unit_defs = [
        ("斤", "斤", "weight", True),
        ("公斤", "公斤", "weight", False),
        ("筐", "筐", "package", False),
        ("件", "件", "package", False),
        ("个", "个", "count", False),
        ("盒", "盒", "count", False),
    ]
    conv_defs = [("公斤", "斤", Decimal("2")), ("筐", "斤", Decimal("45"))]

    n_unit = 0
    for merchant_id in ALL_MERCHANT_IDS:
        for code, name, kind, is_base in unit_defs:
            exists = await db.execute(
                select(Unit.id).where(Unit.merchant_id == merchant_id).where(Unit.code == code)
            )
            if exists.first() is None:
                db.add(
                    Unit(
                        merchant_id=merchant_id,
                        code=code,
                        name=name,
                        kind=kind,
                        is_base=is_base,
                    )
                )
                n_unit += 1
        for from_u, to_u, factor in conv_defs:
            exists = await db.execute(
                select(UnitConversion.id)
                .where(UnitConversion.merchant_id == merchant_id)
                .where(UnitConversion.from_unit == from_u)
                .where(UnitConversion.to_unit == to_u)
            )
            if exists.first() is None:
                db.add(
                    UnitConversion(
                        merchant_id=merchant_id,
                        from_unit=from_u,
                        to_unit=to_u,
                        factor=factor,
                    )
                )
    await db.flush()
    print(f"  [+] 单位字典: {n_unit} 条（含换算）")


async def seed_suppliers(db) -> None:
    """6 个供应商（含 1 个黑名单），三摊各一份档案。"""
    n = 0
    for merchant_id in ALL_MERCHANT_IDS:
        for s in SUPPLIERS:
            compound_id = supplier_id_for(merchant_id, s.idx)
            if await db.get(Supplier, compound_id) is not None:
                continue
            db.add(
                Supplier(
                    id=compound_id,
                    merchant_id=merchant_id,
                    name=s.name,
                    contact=s.contact,
                    address="上海浦东新区农产品批发中心",
                    business_category=s.business_category,
                    min_order_qty=money("20.00"),
                    lead_time_hours=12,
                    default_credit_days=s.default_credit_days,
                    certificates=json.dumps(
                        {"license": f"BL2024{s.idx:04d}", "food_safety": "FS-2024-A"},
                        ensure_ascii=False,
                    ),
                    shortage_rate=s.shortage_rate,
                    return_rate=s.return_rate,
                    quality_issue_rate=s.quality_issue_rate,
                    on_time_rate=s.on_time_rate,
                    composite_score=s.composite_score,
                    total_orders=0,
                    is_active=not s.is_blacklisted,
                    is_blacklisted=s.is_blacklisted,
                )
            )
            n += 1
    await db.flush()
    print(f"  [+] 供应商档案: {n} 条")


async def seed_supplier_products(db) -> None:
    """供应商 × SKU 近期报价（演示比价）。"""
    supplier_category_map = {
        1: {"叶菜类", "根茎类", "瓜果类"},
        2: {"水果类"},
        3: {"肉类"},
        4: {"豆制品", "蛋类"},
        5: {"肉类"},
    }
    n = 0
    for merchant_id in ALL_MERCHANT_IDS:
        for p in products_for(merchant_id):
            for s_idx, groups in supplier_category_map.items():
                if p.group not in groups:
                    continue
                sid = supplier_id_for(merchant_id, s_idx)
                sku = sku_uuid(merchant_id, p.id)
                exists = await db.execute(
                    select(SupplierProduct.id)
                    .where(SupplierProduct.supplier_id == sid)
                    .where(SupplierProduct.sku_id == sku)
                )
                if exists.first() is not None:
                    continue
                db.add(
                    SupplierProduct(
                        merchant_id=merchant_id,
                        supplier_id=sid,
                        sku_id=sku,
                        last_price=p.default_price * Decimal("0.65"),
                        min_order_qty=money("20.00"),
                    )
                )
                n += 1
    await db.flush()
    print(f"  [+] 供应商报价: {n} 条")


async def seed_catalog(db) -> dict:
    """目录层总入口。"""
    print("[1/7] 目录层（商户/商品/SKU/单位/供应商）")
    await seed_merchants(db)
    await seed_product_categories(db)
    await seed_skus_and_aliases(db)
    await seed_units(db)
    await seed_suppliers(db)
    await seed_supplier_products(db)
    return {}
