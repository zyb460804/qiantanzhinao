"""商品/SKU/别名/规格/单位 管理 API — 完整 CRUD。

Models 已存在于 catalog.py，本路由提供摊主日常管理所需的全套接口。
"""

from __future__ import annotations

import uuid
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import get_current_merchant
from app.database import get_db
from app.models.catalog import (
    PriceHistory,
    ProductAlias,
    ProductSKU,
    ProductSpecification,
    Supplier,
    SupplierProduct,
    Unit,
    UnitConversion,
)
from app.models.merchant import Merchant
from app.schemas.common import AnyResponse


router = APIRouter(prefix="/api/v1/catalog", tags=["catalog"])


def _d(v) -> Decimal:
    if v is None:
        return Decimal("0")
    if isinstance(v, Decimal):
        return v
    return Decimal(str(v))


# ═══════════════════════════════════════════════════════════
# SKU 管理
# ═══════════════════════════════════════════════════════════


@router.get("/skus", response_model=AnyResponse)
async def list_skus(
    merchant: Merchant = Depends(get_current_merchant),
    db: AsyncSession = Depends(get_db),
):
    skus = (
        (
            await db.execute(
                select(ProductSKU)
                .where(
                    ProductSKU.merchant_id == merchant.id,
                    ProductSKU.is_active == True,  # noqa: E712
                )
                .order_by(ProductSKU.name)
            )
        )
        .scalars()
        .all()
    )
    return {
        "code": 0,
        "data": [
            {
                "sku_id": str(s.id),
                "name": s.name,
                "category_group": s.category_group,
                "canonical_unit": s.canonical_unit,
                "shelf_life_hours": s.shelf_life_hours,
                "default_sale_price": float(s.default_sale_price) if s.default_sale_price else None,
            }
            for s in skus
        ],
    }


@router.post("/skus", response_model=AnyResponse)
async def create_sku(
    body: dict,
    merchant: Merchant = Depends(get_current_merchant),
    db: AsyncSession = Depends(get_db),
):
    name = (body.get("name") or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="商品名称不能为空")
    sku = ProductSKU(
        merchant_id=merchant.id,
        name=name,
        category_group=body.get("category_group"),
        canonical_unit=body.get("canonical_unit", "斤"),
        shelf_life_hours=body.get("shelf_life_hours", 72),
        default_sale_price=Decimal(str(body["default_sale_price"]))
        if body.get("default_sale_price")
        else None,
    )
    db.add(sku)
    await db.commit()
    await db.refresh(sku)
    return {"code": 0, "data": {"sku_id": str(sku.id), "name": sku.name}}


@router.put("/skus/{sku_id}", response_model=AnyResponse)
async def update_sku(
    sku_id: uuid.UUID,
    body: dict,
    merchant: Merchant = Depends(get_current_merchant),
    db: AsyncSession = Depends(get_db),
):
    sku = await db.get(ProductSKU, sku_id)
    if not sku or sku.merchant_id != merchant.id:
        raise HTTPException(status_code=404, detail="SKU不存在")
    for field in ("name", "category_group", "canonical_unit"):
        if field in body:
            setattr(sku, field, body[field])
    if "shelf_life_hours" in body:
        sku.shelf_life_hours = int(body["shelf_life_hours"])
    if "default_sale_price" in body:
        old_price = sku.default_sale_price
        new_price = Decimal(str(body["default_sale_price"]))
        sku.default_sale_price = new_price
        if old_price and old_price != new_price:
            db.add(
                PriceHistory(
                    merchant_id=merchant.id,
                    sku_id=sku.id,
                    old_price=old_price,
                    new_price=new_price,
                    reason="manual",
                    changed_by="merchant",
                )
            )
    if "is_active" in body:
        sku.is_active = bool(body["is_active"])
    await db.commit()
    return {"code": 0, "data": {"sku_id": str(sku.id), "name": sku.name}}


@router.delete("/skus/{sku_id}", response_model=AnyResponse)
async def deactivate_sku(
    sku_id: uuid.UUID,
    merchant: Merchant = Depends(get_current_merchant),
    db: AsyncSession = Depends(get_db),
):
    sku = await db.get(ProductSKU, sku_id)
    if not sku or sku.merchant_id != merchant.id:
        raise HTTPException(status_code=404, detail="SKU不存在")
    sku.is_active = False
    await db.commit()
    return {"code": 0, "message": f"已停用 {sku.name}"}


# ═══════════════════════════════════════════════════════════
# 别名管理
# ═══════════════════════════════════════════════════════════


@router.get("/skus/{sku_id}/aliases", response_model=AnyResponse)
async def list_aliases(
    sku_id: uuid.UUID,
    merchant: Merchant = Depends(get_current_merchant),
    db: AsyncSession = Depends(get_db),
):
    aliases = (
        (
            await db.execute(
                select(ProductAlias).where(
                    ProductAlias.sku_id == sku_id, ProductAlias.merchant_id == merchant.id
                )
            )
        )
        .scalars()
        .all()
    )
    return {
        "code": 0,
        "data": [
            {"alias_id": str(a.id), "alias": a.alias, "is_system": a.is_system} for a in aliases
        ],
    }


@router.post("/skus/{sku_id}/aliases", response_model=AnyResponse)
async def add_alias(
    sku_id: uuid.UUID,
    body: dict,
    merchant: Merchant = Depends(get_current_merchant),
    db: AsyncSession = Depends(get_db),
):
    from sqlalchemy.exc import IntegrityError

    alias = (body.get("alias") or "").strip()
    if not alias:
        raise HTTPException(status_code=400, detail="别名不能为空")
    sku = await db.get(ProductSKU, sku_id)
    if not sku or sku.merchant_id != merchant.id:
        raise HTTPException(status_code=404, detail="SKU不存在")
    a = ProductAlias(merchant_id=merchant.id, sku_id=sku_id, alias=alias)
    db.add(a)
    try:
        await db.commit()
    except IntegrityError as e:
        await db.rollback()
        raise HTTPException(status_code=409, detail=f"别名 '{alias}' 已存在") from e
    return {"code": 0, "data": {"alias_id": str(a.id), "alias": alias}}


@router.delete("/aliases/{alias_id}", response_model=AnyResponse)
async def remove_alias(
    alias_id: uuid.UUID,
    merchant: Merchant = Depends(get_current_merchant),
    db: AsyncSession = Depends(get_db),
):
    a = await db.get(ProductAlias, alias_id)
    if not a or a.merchant_id != merchant.id:
        raise HTTPException(status_code=404, detail="别名不存在")
    await db.delete(a)
    await db.commit()
    return {"code": 0, "message": "别名已删除"}


# ═══════════════════════════════════════════════════════════
# 规格管理
# ═══════════════════════════════════════════════════════════


@router.get("/skus/{sku_id}/specs", response_model=AnyResponse)
async def list_specs(
    sku_id: uuid.UUID,
    merchant: Merchant = Depends(get_current_merchant),
    db: AsyncSession = Depends(get_db),
):
    specs = (
        (
            await db.execute(
                select(ProductSpecification).where(
                    ProductSpecification.sku_id == sku_id,
                    ProductSpecification.merchant_id == merchant.id,
                )
            )
        )
        .scalars()
        .all()
    )
    return {
        "code": 0,
        "data": [
            {
                "spec_id": str(s.id),
                "name": s.name,
                "price_delta": float(s.price_delta),
                "is_active": s.is_active,
            }
            for s in specs
        ],
    }


@router.post("/skus/{sku_id}/specs", response_model=AnyResponse)
async def add_spec(
    sku_id: uuid.UUID,
    body: dict,
    merchant: Merchant = Depends(get_current_merchant),
    db: AsyncSession = Depends(get_db),
):
    name = (body.get("name") or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="规格名不能为空")
    sku = await db.get(ProductSKU, sku_id)
    if not sku or sku.merchant_id != merchant.id:
        raise HTTPException(status_code=404, detail="SKU不存在")
    price_delta = Decimal(str(body.get("price_delta", 0)))
    if sku.default_sale_price is None and price_delta < 0:
        raise HTTPException(status_code=400, detail="未设置基础售价时，规格加价不能为负数")
    if (
        sku.default_sale_price is not None
        and Decimal(str(sku.default_sale_price)) + price_delta < 0
    ):
        raise HTTPException(status_code=400, detail="规格最终售价不能为负数")
    s = ProductSpecification(
        merchant_id=merchant.id, sku_id=sku_id, name=name, price_delta=price_delta
    )
    db.add(s)
    await db.commit()
    return {"code": 0, "data": {"spec_id": str(s.id), "name": name}}


@router.delete("/specs/{spec_id}", response_model=AnyResponse)
async def remove_spec(
    spec_id: uuid.UUID,
    merchant: Merchant = Depends(get_current_merchant),
    db: AsyncSession = Depends(get_db),
):
    s = await db.get(ProductSpecification, spec_id)
    if not s or s.merchant_id != merchant.id:
        raise HTTPException(status_code=404, detail="规格不存在")
    s.is_active = False
    await db.commit()
    return {"code": 0, "message": "规格已停用"}


# ═══════════════════════════════════════════════════════════
# 单位管理
# ═══════════════════════════════════════════════════════════


@router.get("/units", response_model=AnyResponse)
async def list_units(
    merchant: Merchant = Depends(get_current_merchant), db: AsyncSession = Depends(get_db)
):
    units = (
        (await db.execute(select(Unit).where(Unit.merchant_id == merchant.id).order_by(Unit.code)))
        .scalars()
        .all()
    )
    return {
        "code": 0,
        "data": [
            {
                "unit_id": str(u.id),
                "code": u.code,
                "name": u.name,
                "kind": u.kind,
                "is_base": u.is_base,
            }
            for u in units
        ],
    }


@router.post("/units", response_model=AnyResponse)
async def create_unit(
    body: dict,
    merchant: Merchant = Depends(get_current_merchant),
    db: AsyncSession = Depends(get_db),
):
    code = (body.get("code") or "").strip()
    if not code:
        raise HTTPException(status_code=400, detail="单位代码不能为空")
    u = Unit(
        merchant_id=merchant.id,
        code=code,
        name=body.get("name", code),
        kind=body.get("kind", "weight"),
        is_base=body.get("is_base", False),
    )
    db.add(u)
    await db.commit()
    return {"code": 0, "data": {"unit_id": str(u.id), "code": code}}


@router.get("/unit-conversions", response_model=AnyResponse)
async def list_conversions(
    merchant: Merchant = Depends(get_current_merchant), db: AsyncSession = Depends(get_db)
):
    convs = (
        (await db.execute(select(UnitConversion).where(UnitConversion.merchant_id == merchant.id)))
        .scalars()
        .all()
    )
    return {
        "code": 0,
        "data": [
            {
                "id": str(c.id),
                "from_unit": c.from_unit,
                "to_unit": c.to_unit,
                "factor": float(c.factor),
                "sku_id": str(c.sku_id) if c.sku_id else None,
            }
            for c in convs
        ],
    }


@router.post("/unit-conversions", response_model=AnyResponse)
async def create_conversion(
    body: dict,
    merchant: Merchant = Depends(get_current_merchant),
    db: AsyncSession = Depends(get_db),
):
    from_unit = (body.get("from_unit") or "").strip()
    to_unit = (body.get("to_unit") or "").strip()
    factor = Decimal(str(body.get("factor", 0)))
    if not from_unit or not to_unit:
        raise HTTPException(status_code=400, detail="换算单位不能为空")
    if from_unit == to_unit:
        raise HTTPException(status_code=400, detail="来源单位和目标单位不能相同")
    if factor <= 0:
        raise HTTPException(status_code=400, detail="换算系数必须大于0")
    sku_id = uuid.UUID(body["sku_id"]) if body.get("sku_id") else None
    if sku_id:
        sku = await db.get(ProductSKU, sku_id)
        if not sku or sku.merchant_id != merchant.id:
            raise HTTPException(status_code=404, detail="SKU不存在")

    # 换算关系必须在同一商户、同一 SKU 作用域内保持一致。把已有关系视为
    # 双向图，新增边若与已有路径给出的比例冲突，则拒绝，否则会出现
    # “1箱=10斤、1斤=0.2箱”这类不可逆的闭环。
    existing = (
        (
            await db.execute(
                select(UnitConversion).where(
                    UnitConversion.merchant_id == merchant.id,
                    UnitConversion.sku_id == sku_id,
                )
            )
        )
        .scalars()
        .all()
    )
    for item in existing:
        if item.from_unit == from_unit and item.to_unit == to_unit:
            raise HTTPException(status_code=409, detail="该单位换算关系已存在")

    graph: dict[str, list[tuple[str, Decimal]]] = {}
    for item in existing:
        edge_factor = Decimal(str(item.factor))
        graph.setdefault(item.from_unit, []).append((item.to_unit, edge_factor))
        graph.setdefault(item.to_unit, []).append((item.from_unit, Decimal("1") / edge_factor))

    # BFS 保留从来源单位到目标单位的累计比例。
    paths: list[tuple[str, Decimal]] = [(from_unit, Decimal("1"))]
    visited = {from_unit}
    known_factor = None
    while paths:
        current, current_factor = paths.pop(0)
        if current == to_unit:
            known_factor = current_factor
            break
        for nxt, edge_factor in graph.get(current, []):
            if nxt not in visited:
                visited.add(nxt)
                paths.append((nxt, current_factor * edge_factor))
    if known_factor is not None:
        tolerance = max(Decimal("0.000001"), abs(factor) * Decimal("0.000001"))
        if abs(known_factor - factor) > tolerance:
            raise HTTPException(status_code=409, detail="单位换算关系与已有换算链冲突")
        raise HTTPException(status_code=409, detail="该单位换算关系已由现有换算链确定")

    c = UnitConversion(
        merchant_id=merchant.id,
        from_unit=from_unit,
        to_unit=to_unit,
        factor=factor,
        sku_id=sku_id,
    )
    db.add(c)
    await db.commit()
    return {"code": 0, "data": {"id": str(c.id), "factor": float(c.factor)}}


@router.delete("/unit-conversions/{conv_id}", response_model=AnyResponse)
async def delete_conversion(
    conv_id: uuid.UUID,
    merchant: Merchant = Depends(get_current_merchant),
    db: AsyncSession = Depends(get_db),
):
    c = await db.get(UnitConversion, conv_id)
    if not c or c.merchant_id != merchant.id:
        raise HTTPException(status_code=404, detail="换算不存在")
    await db.delete(c)
    await db.commit()
    return {"code": 0, "message": "换算已删除"}


# ═══════════════════════════════════════════════════════════
# 价格历史
# ═══════════════════════════════════════════════════════════


@router.get("/skus/{sku_id}/price-history", response_model=AnyResponse)
async def sku_price_history(
    sku_id: uuid.UUID,
    merchant: Merchant = Depends(get_current_merchant),
    db: AsyncSession = Depends(get_db),
):
    rows = (
        (
            await db.execute(
                select(PriceHistory)
                .where(PriceHistory.sku_id == sku_id, PriceHistory.merchant_id == merchant.id)
                .order_by(PriceHistory.created_at.desc())
                .limit(50)
            )
        )
        .scalars()
        .all()
    )
    return {
        "code": 0,
        "data": [
            {
                "old_price": float(r.old_price),
                "new_price": float(r.new_price),
                "reason": r.reason,
                "changed_by": r.changed_by,
                "created_at": r.created_at.isoformat() if r.created_at else None,
            }
            for r in rows
        ],
    }


# ═══════════════════════════════════════════════════════════
# 供应商管理 CRUD (section 4.2)
# ═══════════════════════════════════════════════════════════


@router.get("/suppliers", response_model=AnyResponse)
async def list_suppliers(
    offset: int = 0,
    limit: int = 50,
    keyword: str | None = None,
    merchant: Merchant = Depends(get_current_merchant),
    db: AsyncSession = Depends(get_db),
):
    from app.services.accounts_service import get_supplier_balance

    base = select(Supplier).where(
        Supplier.merchant_id == merchant.id,
        Supplier.is_active == True,  # noqa: E712
    )
    count_q = (
        select(func.count())
        .select_from(Supplier)
        .where(
            Supplier.merchant_id == merchant.id,
            Supplier.is_active == True,  # noqa: E712
        )
    )

    if keyword:
        kw = f"%{keyword.strip()}%"
        base = base.where(
            Supplier.name.ilike(kw)
            | Supplier.contact.ilike(kw)
            | Supplier.business_category.ilike(kw)
            | Supplier.address.ilike(kw)
        )
        count_q = count_q.where(
            Supplier.name.ilike(kw)
            | Supplier.contact.ilike(kw)
            | Supplier.business_category.ilike(kw)
            | Supplier.address.ilike(kw)
        )

    total = (await db.execute(count_q)).scalar() or 0
    # 全量黑名单数（不受 keyword/分页影响），供前端统计卡精确展示
    blacklisted_count = (
        await db.execute(
            select(func.count())
            .select_from(Supplier)
            .where(
                Supplier.merchant_id == merchant.id,
                Supplier.is_active == True,  # noqa: E712
                Supplier.is_blacklisted == True,  # noqa: E712
            )
        )
    ).scalar() or 0

    suppliers = (
        (await db.execute(base.order_by(Supplier.name).offset(offset).limit(min(limit, 200))))
        .scalars()
        .all()
    )

    result = []
    for s in suppliers:
        bal = await get_supplier_balance(db, merchant.id, s.id)
        result.append(
            {
                "supplier_id": str(s.id),
                "name": s.name,
                "contact": s.contact,
                "address": s.address,
                "business_category": s.business_category,
                "min_order_qty": float(s.min_order_qty) if s.min_order_qty else None,
                "lead_time_hours": s.lead_time_hours,
                "default_credit_days": s.default_credit_days,
                "is_blacklisted": s.is_blacklisted,
                "composite_score": float(s.composite_score) if s.composite_score else None,
                "shortage_rate": float(s.shortage_rate) if s.shortage_rate else None,
                "return_rate": float(s.return_rate) if s.return_rate else None,
                "quality_issue_rate": float(s.quality_issue_rate) if s.quality_issue_rate else None,
                "on_time_rate": float(s.on_time_rate) if s.on_time_rate else None,
                "total_orders": s.total_orders or 0,
                "created_at": s.created_at.isoformat() if s.created_at else None,
                "current_balance": float(bal),
            }
        )
    return {
        "code": 0,
        "data": {
            "items": result,
            "total": total,
            "blacklisted_count": blacklisted_count,
            "offset": offset,
            "limit": limit,
        },
    }


# ═══════════════════════════════════════════════════════════
# 供应商比价 + 最优推荐 (§4.2 扩展)
# ═══════════════════════════════════════════════════════════


@router.get("/suppliers/compare", response_model=AnyResponse)
async def compare_suppliers(
    sku_id: uuid.UUID,
    sort_by: str = "value",
    merchant: Merchant = Depends(get_current_merchant),
    db: AsyncSession = Depends(get_db),
):
    """Compare all suppliers for a given SKU by price, quality, and value.

    sort_by options:
    - "value" (default): value-for-money score = composite_score / price (higher=better)
    - "price": cheapest first
    - "score": highest composite_score first
    - "lead_time": shortest lead time first

    Returns each supplier's price, quality metrics, and a computed value_score.
    """
    sku = await db.get(ProductSKU, sku_id)
    if not sku or sku.merchant_id != merchant.id:
        raise HTTPException(status_code=404, detail="SKU不存在")

    sps = (
        await db.execute(
            select(SupplierProduct, Supplier)
            .join(Supplier, Supplier.id == SupplierProduct.supplier_id)
            .where(
                SupplierProduct.sku_id == sku_id,
                Supplier.merchant_id == merchant.id,
                Supplier.is_active.is_(True),
                Supplier.is_blacklisted.is_(False),
            )
        )
    ).all()

    if not sps:
        return {
            "code": 0,
            "data": {
                "sku_id": str(sku_id),
                "sku_name": sku.name,
                "canonical_unit": sku.canonical_unit,
                "suppliers": [],
                "total": 0,
                "message": "该商品暂无关联的活跃供应商",
            },
        }

    from app.services.accounts_service import get_supplier_balance

    results = []
    for sp, supplier in sps:
        price = float(sp.last_price) if sp.last_price else None
        score = float(supplier.composite_score) if supplier.composite_score else None
        value_score = None
        if price and price > 0 and score is not None:
            value_score = round(score / price, 2)
        balance = await get_supplier_balance(db, merchant.id, supplier.id)
        results.append(
            {
                "supplier_id": str(supplier.id),
                "supplier_name": supplier.name,
                "contact": supplier.contact,
                "last_price": price,
                "min_order_qty": float(sp.min_order_qty) if sp.min_order_qty else None,
                "composite_score": score,
                "shortage_rate": float(supplier.shortage_rate) if supplier.shortage_rate else None,
                "return_rate": float(supplier.return_rate) if supplier.return_rate else None,
                "quality_issue_rate": float(supplier.quality_issue_rate)
                if supplier.quality_issue_rate
                else None,
                "on_time_rate": float(supplier.on_time_rate) if supplier.on_time_rate else None,
                "lead_time_hours": supplier.lead_time_hours,
                "default_credit_days": supplier.default_credit_days,
                "total_orders": supplier.total_orders or 0,
                "current_balance": float(balance),
                "value_score": value_score,
            }
        )

    if sort_by == "price":
        results.sort(key=lambda r: r["last_price"] if r["last_price"] is not None else float("inf"))
    elif sort_by == "score":
        results.sort(
            key=lambda r: r["composite_score"] if r["composite_score"] is not None else -1,
            reverse=True,
        )
    elif sort_by == "lead_time":
        results.sort(
            key=lambda r: r["lead_time_hours"] if r["lead_time_hours"] is not None else 99999
        )
    else:
        results.sort(
            key=lambda r: r["value_score"] if r["value_score"] is not None else -1, reverse=True
        )

    best = results[0] if results else None
    if best:
        best["is_best"] = True

    return {
        "code": 0,
        "data": {
            "sku_id": str(sku_id),
            "sku_name": sku.name,
            "canonical_unit": sku.canonical_unit,
            "suppliers": results,
            "total": len(results),
        },
    }


@router.post("/suppliers/recommend", response_model=AnyResponse)
async def recommend_best_supplier(
    sku_id: uuid.UUID,
    urgency: str = "normal",
    max_price: float | None = None,
    merchant: Merchant = Depends(get_current_merchant),
    db: AsyncSession = Depends(get_db),
):
    """AI-style recommendation: pick the best supplier for a SKU.

    Parameters:
    - urgency: "urgent" prefers fastest delivery, "cost" prefers cheapest,
      "normal" (default) balances all factors
    - max_price: optional price ceiling

    Returns the top-ranked supplier with reasoning and alternatives.
    """
    sku = await db.get(ProductSKU, sku_id)
    if not sku or sku.merchant_id != merchant.id:
        raise HTTPException(status_code=404, detail="SKU不存在")

    sps = (
        await db.execute(
            select(SupplierProduct, Supplier)
            .join(Supplier, Supplier.id == SupplierProduct.supplier_id)
            .where(
                SupplierProduct.sku_id == sku_id,
                Supplier.merchant_id == merchant.id,
                Supplier.is_active.is_(True),
                Supplier.is_blacklisted.is_(False),
            )
        )
    ).all()

    if not sps:
        return {
            "code": 0,
            "data": {
                "recommendation": None,
                "message": "该商品暂无关联供应商，请先在商品详情中关联供应商",
            },
        }

    candidates = []
    for sp, supplier in sps:
        price = float(sp.last_price) if sp.last_price else None
        if max_price and price and price > max_price:
            continue
        score = float(supplier.composite_score) if supplier.composite_score else 0
        lead = supplier.lead_time_hours or 72
        credit = supplier.default_credit_days or 0
        rate = float(supplier.on_time_rate) if supplier.on_time_rate else 100
        quality = 100 - float(supplier.quality_issue_rate) if supplier.quality_issue_rate else 100
        total_orders = supplier.total_orders or 0

        if urgency == "urgent":
            rec_score = (rate * 0.5) + (100 - lead * 0.5) * 0.3 + (score * 1.0) * 0.2
            reasoning = "紧急模式：优先时效性和准时率"
        elif urgency == "cost":
            rec_score = (100 - (price or 0) * 10) * 0.5 + (score * 0.5)
            reasoning = "成本优先模式：优先最低价格"
        else:
            price_penalty = max(0, 100 - (price or 0) * 5) if price else 50
            rec_score = (
                (score * 0.4)
                + (price_penalty * 0.2)
                + (rate * 0.2)
                + (quality * 0.1)
                + (credit * 1.5) * 0.1
            )
            reasoning = "综合平衡模式：综合质量、价格、时效和信用期"

        candidates.append(
            {
                "supplier_id": str(supplier.id),
                "supplier_name": supplier.name,
                "last_price": price,
                "composite_score": round(score, 1),
                "on_time_rate": round(rate, 1),
                "lead_time_hours": lead,
                "default_credit_days": credit,
                "total_orders": total_orders,
                "recommendation_score": round(rec_score, 1),
                "reasoning": reasoning,
            }
        )

    if not candidates:
        return {
            "code": 0,
            "data": {"recommendation": None, "message": f"没有满足价格上限 ¥{max_price} 的供应商"},
        }

    candidates.sort(key=lambda c: c["recommendation_score"], reverse=True)
    best = candidates[0]

    factors = []
    if best["last_price"]:
        avg = sum((c["last_price"] or 0) for c in candidates) / len(candidates)
        if best["last_price"] <= avg:
            factors.append(f"价格低于平均水平 ¥{avg:.2f}")
    if best["composite_score"] and best["composite_score"] >= 80:
        factors.append("综合质量评分良好")
    if best["on_time_rate"] and best["on_time_rate"] >= 90:
        factors.append("历史准时率高")
    if best["total_orders"] >= 10:
        factors.append("合作次数多，供应稳定")

    return {
        "code": 0,
        "data": {
            "sku_id": str(sku_id),
            "sku_name": sku.name,
            "urgency": urgency,
            "recommendation": {
                "supplier_id": best["supplier_id"],
                "supplier_name": best["supplier_name"],
                "last_price": best["last_price"],
                "recommendation_score": best["recommendation_score"],
                "reasoning": best["reasoning"],
                "decision_factors": factors,
            },
            "alternatives": candidates[1:4],
            "total_candidates": len(candidates),
        },
    }


@router.get("/suppliers/{supplier_id}", response_model=AnyResponse)
async def get_supplier(
    supplier_id: uuid.UUID,
    merchant: Merchant = Depends(get_current_merchant),
    db: AsyncSession = Depends(get_db),
):
    """Get full supplier details including quality metrics and balance."""
    from app.services.accounts_service import get_supplier_balance

    s = await db.get(Supplier, supplier_id)
    if not s or s.merchant_id != merchant.id:
        raise HTTPException(status_code=404, detail="供应商不存在")
    bal = await get_supplier_balance(db, merchant.id, s.id)
    return {
        "code": 0,
        "data": {
            "supplier_id": str(s.id),
            "name": s.name,
            "contact": s.contact,
            "address": s.address,
            "business_category": s.business_category,
            "min_order_qty": float(s.min_order_qty) if s.min_order_qty else None,
            "lead_time_hours": s.lead_time_hours,
            "default_credit_days": s.default_credit_days,
            "is_blacklisted": s.is_blacklisted,
            "composite_score": float(s.composite_score) if s.composite_score else None,
            "shortage_rate": float(s.shortage_rate) if s.shortage_rate else None,
            "return_rate": float(s.return_rate) if s.return_rate else None,
            "quality_issue_rate": float(s.quality_issue_rate) if s.quality_issue_rate else None,
            "on_time_rate": float(s.on_time_rate) if s.on_time_rate else None,
            "total_orders": s.total_orders or 0,
            "certificates": s.certificates,
            "notes": getattr(s, "notes", None),
            "created_at": s.created_at.isoformat() if s.created_at else None,
            "current_balance": float(bal),
        },
    }


@router.post("/suppliers", response_model=AnyResponse)
async def create_supplier(
    body: dict,
    merchant: Merchant = Depends(get_current_merchant),
    db: AsyncSession = Depends(get_db),
):
    name = (body.get("name") or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="供应商名称不能为空")
    s = Supplier(
        merchant_id=merchant.id,
        name=name,
        contact=body.get("contact"),
        address=body.get("address"),
        business_category=body.get("business_category"),
        min_order_qty=Decimal(str(body["min_order_qty"])) if body.get("min_order_qty") else None,
        lead_time_hours=body.get("lead_time_hours"),
        default_credit_days=body.get("default_credit_days"),
    )
    db.add(s)
    await db.commit()
    await db.refresh(s)
    return {"code": 0, "data": {"supplier_id": str(s.id), "name": s.name}}


@router.put("/suppliers/{supplier_id}", response_model=AnyResponse)
async def update_supplier(
    supplier_id: uuid.UUID,
    body: dict,
    merchant: Merchant = Depends(get_current_merchant),
    db: AsyncSession = Depends(get_db),
):
    s = await db.get(Supplier, supplier_id)
    if not s or s.merchant_id != merchant.id:
        raise HTTPException(status_code=404, detail="供应商不存在")
    for f in ("name", "contact", "address", "business_category"):
        if f in body:
            setattr(s, f, body[f])
    if "min_order_qty" in body:
        s.min_order_qty = Decimal(str(body["min_order_qty"]))
    if "lead_time_hours" in body:
        s.lead_time_hours = int(body["lead_time_hours"])
    if "default_credit_days" in body:
        s.default_credit_days = int(body["default_credit_days"])
    if "is_active" in body:
        s.is_active = bool(body["is_active"])
    if "is_blacklisted" in body:
        s.is_blacklisted = bool(body["is_blacklisted"])
    if "certificates" in body:
        s.certificates = body["certificates"]
    if "notes" in body:
        setattr(s, "notes", body["notes"]) if hasattr(s, "notes") else None
    await db.commit()
    return {"code": 0, "data": {"supplier_id": str(s.id), "name": s.name}}


@router.delete("/suppliers/{supplier_id}", response_model=AnyResponse)
async def deactivate_supplier(
    supplier_id: uuid.UUID,
    merchant: Merchant = Depends(get_current_merchant),
    db: AsyncSession = Depends(get_db),
):
    s = await db.get(Supplier, supplier_id)
    if not s or s.merchant_id != merchant.id:
        raise HTTPException(status_code=404, detail="供应商不存在")
    s.is_active = False
    await db.commit()
    return {"code": 0, "message": f"已停用 {s.name}"}


@router.get("/suppliers/{supplier_id}/products", response_model=AnyResponse)
async def list_supplier_products(
    supplier_id: uuid.UUID,
    merchant: Merchant = Depends(get_current_merchant),
    db: AsyncSession = Depends(get_db),
):
    sps = (
        (
            await db.execute(
                select(SupplierProduct).where(
                    SupplierProduct.supplier_id == supplier_id,
                    SupplierProduct.merchant_id == merchant.id,
                )
            )
        )
        .scalars()
        .all()
    )
    # batch lookup SKU names
    sku_ids = [sp.sku_id for sp in sps]
    sku_map = {}
    if sku_ids:
        skus = (
            (await db.execute(select(ProductSKU).where(ProductSKU.id.in_(sku_ids)))).scalars().all()
        )
        sku_map = {s.id: s.name for s in skus}
    return {
        "code": 0,
        "data": [
            {
                "id": str(sp.id),
                "sku_id": str(sp.sku_id),
                "sku_name": sku_map.get(sp.sku_id, ""),
                "last_price": float(sp.last_price) if sp.last_price else None,
                "min_order_qty": float(sp.min_order_qty) if sp.min_order_qty else None,
            }
            for sp in sps
        ],
    }


@router.post("/suppliers/{supplier_id}/products", response_model=AnyResponse)
async def add_supplier_product(
    supplier_id: uuid.UUID,
    body: dict,
    merchant: Merchant = Depends(get_current_merchant),
    db: AsyncSession = Depends(get_db),
):
    sp = SupplierProduct(
        merchant_id=merchant.id,
        supplier_id=supplier_id,
        sku_id=uuid.UUID(body["sku_id"]),
        last_price=Decimal(str(body["last_price"])) if body.get("last_price") else None,
        min_order_qty=Decimal(str(body["min_order_qty"])) if body.get("min_order_qty") else None,
    )
    db.add(sp)
    await db.commit()
    return {"code": 0, "data": {"id": str(sp.id)}}


@router.delete("/supplier-products/{sp_id}", response_model=AnyResponse)
async def remove_supplier_product(
    sp_id: uuid.UUID,
    merchant: Merchant = Depends(get_current_merchant),
    db: AsyncSession = Depends(get_db),
):
    sp = await db.get(SupplierProduct, sp_id)
    if not sp or sp.merchant_id != merchant.id:
        raise HTTPException(status_code=404, detail="关联不存在")
    await db.delete(sp)
    await db.commit()
    return {"code": 0, "message": "已解除关联"}


# ═══════════════════════════════════════════════════════════
# SKU → 供应商 反向查询（按 SKU 查供应商及报价）
# ═══════════════════════════════════════════════════════════


@router.get("/skus/{sku_id}/suppliers", response_model=AnyResponse)
async def list_sku_suppliers(
    sku_id: uuid.UUID,
    merchant: Merchant = Depends(get_current_merchant),
    db: AsyncSession = Depends(get_db),
):
    """返回该 SKU 关联的所有供应商及报价，支撑商品详情面板。"""

    sku = await db.get(ProductSKU, sku_id)
    if not sku or sku.merchant_id != merchant.id:
        raise HTTPException(status_code=404, detail="SKU不存在")

    sps = (
        (
            await db.execute(
                select(SupplierProduct).where(
                    SupplierProduct.sku_id == sku_id,
                    SupplierProduct.merchant_id == merchant.id,
                )
            )
        )
        .scalars()
        .all()
    )

    # 批量取 supplier 名称，避免 N+1
    supplier_ids = [sp.supplier_id for sp in sps]
    supplier_map = {}
    if supplier_ids:
        suppliers = (
            (await db.execute(select(Supplier).where(Supplier.id.in_(supplier_ids))))
            .scalars()
            .all()
        )
        supplier_map = {s.id: s.name for s in suppliers}

    return {
        "code": 0,
        "data": [
            {
                "id": str(sp.id),
                "supplier_id": str(sp.supplier_id),
                "supplier_name": supplier_map.get(sp.supplier_id, ""),
                "last_price": float(sp.last_price) if sp.last_price else None,
                "min_order_qty": float(sp.min_order_qty) if sp.min_order_qty else None,
            }
            for sp in sps
        ],
    }


# ═══════════════════════════════════════════════════════════
# 供应商自动评分 (§4.2)
# ═══════════════════════════════════════════════════════════


@router.post("/suppliers/{supplier_id}/recalculate-score", response_model=AnyResponse)
async def recalculate_supplier_score(
    supplier_id: uuid.UUID,
    merchant: Merchant = Depends(get_current_merchant),
    db: AsyncSession = Depends(get_db),
):
    """Auto-calculate supplier quality metrics from purchase acceptance history.

    Analyzes all purchase items for this supplier to compute:
    - shortage_rate: (sum shortage_qty / sum expected_qty) × 100
    - return_rate: (sum returned_qty / sum expected_qty) × 100
    - quality_issue_rate: (count quality_ok=false / total accepted) × 100
    - on_time_rate: estimated from delivery timeliness (simplified)
    - composite_score: weighted average
      (100 - shortage×0.25 - return×0.25 - quality×0.35 - late×0.15)
    - total_orders: count of distinct purchase lists
    """
    from app.models.purchase import PurchaseItem

    supplier = await db.get(Supplier, supplier_id)
    if not supplier or supplier.merchant_id != merchant.id:
        raise HTTPException(status_code=404, detail="供应商不存在")

    # Get all purchase items for this supplier
    items = (
        (
            await db.execute(
                select(PurchaseItem).where(
                    PurchaseItem.merchant_id == merchant.id,
                    PurchaseItem.supplier_id == supplier_id,
                )
            )
        )
        .scalars()
        .all()
    )

    if not items:
        return {
            "code": 0,
            "message": "该供应商暂无采购记录，无法评分",
            "data": {"supplier_id": str(supplier_id), "score": None},
        }

    # Count distinct purchase lists
    list_ids = set(item.list_id for item in items if item.list_id)

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
        # Late delivery: simplified heuristic (arrival > expected lead time)
        if item.accepted_at and item.status == "accepted":
            # This is simplified; real implementation would compare
            # PurchaseList.expected_arrival vs actual acceptance date
            pass

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

    # Update supplier record
    supplier.shortage_rate = shortage_rate.quantize(Decimal("0.01"))
    supplier.return_rate = return_rate.quantize(Decimal("0.01"))
    supplier.quality_issue_rate = quality_issue_rate.quantize(Decimal("0.01"))
    supplier.on_time_rate = on_time_rate.quantize(Decimal("0.01"))
    supplier.composite_score = composite.quantize(Decimal("0.01"))
    supplier.total_orders = len(list_ids)

    from app.models.audit import AuditLog

    db.add(
        AuditLog(
            merchant_id=merchant.id,
            action="supplier_score",
            target_table="suppliers",
            target_id=str(supplier.id),
            after_data={
                "composite_score": float(composite),
                "shortage_rate": float(shortage_rate),
                "return_rate": float(return_rate),
                "quality_issue_rate": float(quality_issue_rate),
                "on_time_rate": float(on_time_rate),
                "total_orders": len(list_ids),
            },
            operator="merchant",
        )
    )
    await db.commit()

    return {
        "code": 0,
        "message": f"供应商 {supplier.name} 评分已更新",
        "data": {
            "supplier_id": str(supplier_id),
            "supplier_name": supplier.name,
            "composite_score": float(composite),
            "shortage_rate": float(shortage_rate),
            "return_rate": float(return_rate),
            "quality_issue_rate": float(quality_issue_rate),
            "on_time_rate": float(on_time_rate),
            "total_orders": len(list_ids),
            "total_expected_qty": float(total_expected),
            "total_shortage_qty": float(total_shortage),
            "total_returned_qty": float(total_returned),
        },
    }
