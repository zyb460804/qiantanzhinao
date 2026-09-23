"""商品/SKU/别名/规格/单位 管理 API — 完整 CRUD。

Models 已存在于 catalog.py，本路由提供摊主日常管理所需的全套接口。
"""

from __future__ import annotations

import uuid
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
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
from app.models.staff import StaffMember
from app.routers.staff import PermissionContext, require_permission
from app.schemas.common import AnyResponse, PaginatedResponse
from app.services.supplier_scoring import calculate_supplier_score


# 摊主可读名称里的危险字符（存储型 XSS 的根因：名称会回流到 Web 管理端/
# 导出/追溯二维码等消费端）。创建/更新时统一剥离，而非期望每个消费端转义。
_DISPLAY_NAME_BAD_CHARS = str.maketrans("", "", "<>\"'`")

# RA-13：零宽/双向控制字符 —— 肉眼不可见，可夹带伪装与混淆（「白\u200b菜」
# 与「白菜」显示全同但字节不同）。净化时先剥，再做 NFKC 归一化。
#   \u200b\u200c\u200d  零宽空格/非连接符/连接符
#   \u2060              词连接符（同族零宽）
#   \ufeff              零宽不换行空格（BOM）
#   \u200e\u200f        LRM/RLM 方向标记
#   \u202a-\u202e       双向格式覆盖
#   \u2066-\u2069       双向隔离符（复查探针夹带的 \u2066 属此族）
_INVISIBLE_CHARS = str.maketrans(
    "",
    "",
    "\u200b\u200c\u200d\u2060\ufeff\u200e\u200f"
    "\u202a\u202b\u202c\u202d\u202e\u2066\u2067\u2068\u2069",
)


def _sanitize_display_name(raw) -> str:
    """剥离名称中的脚本/标签字符并压平空白；全空则返回空串由调用方拒绝。

    RA-13：净化链 = 剥零宽/双向控制字符 → NFKC 归一化 → 剥 <> " ' `。
    NFKC 把全角 ＜ ＞ ＂ ＇ 折叠为 ASCII（随即被坏字符表剥除），堵住
    「＜svg onload=…＞」借全角尖括号绕过净化的口子。注意 NFKC 也会把
    全角数字/字母折叠为半角（１２３→123、ＡＢＣ→ABC）——只影响新写入
    的展示文本（净化只在创建/更新入口调用），历史数据不做回写清洗。
    """
    import re as _re
    import unicodedata as _ud

    name = str(raw or "").translate(_INVISIBLE_CHARS)
    name = _ud.normalize("NFKC", name).translate(_DISPLAY_NAME_BAD_CHARS)
    return _re.sub(r"\s+", " ", name).strip()


# SKU 售价上限：与 expense.py 费用金额上限同口径（P1-5 修复沿用）。
_MAX_SALE_PRICE = Decimal("1000000")


def _parse_sale_price(raw):
    """QA-11：安全解析售价 —— None/'' → None；"abc"/"NaN" 等非数字串此前
    直抛 Decimal InvalidOperation 导致 500（与费用接口 400 防护口径不一致）。
    现参照 expense.py 对金额的防护模式：先包 try 解析 → 422「售价格式不正确」，
    再做有限性 + 区间校验（0 ~ 1000000），口径与费用金额一致。

    RA-14：校验顺序与 pos._resolve_unit_price 完全对齐 —— 先取请求值的
    浮点二进制真实值（Decimal(float(x))，JSON 数字/数字串经 float 解析
    得到同一 float），ROUND_HALF_UP 量化 2 位后再判 ≤1e6。此前按 str 精确
    十进制直判，1000000.004 在 catalog 422 而 POS 量化后恰为 1000000.00
    放行，两端口边界不一致。
    """
    if raw is None or raw == "":
        return None
    try:
        price = Decimal(float(raw))
    except (InvalidOperation, TypeError, ValueError, OverflowError):
        raise HTTPException(status_code=422, detail="售价格式不正确") from None
    # NaN/Infinity 解析不报错但不可比较/不可入账，与越界售价一并拒绝
    if not price.is_finite():
        raise HTTPException(status_code=422, detail="售价必须在 0 ~ 1000000 之间")
    try:
        price = price.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    except InvalidOperation:
        # 超出 Decimal 上下文精度的天文数字：量化即拒绝，不泄漏为 500
        raise HTTPException(status_code=422, detail="售价必须在 0 ~ 1000000 之间") from None
    if not (Decimal("0") <= price <= _MAX_SALE_PRICE):
        raise HTTPException(status_code=422, detail="售价必须在 0 ~ 1000000 之间")
    return price


router = APIRouter(prefix="/api/v1/catalog", tags=["catalog"])

# P2-9：新商户单位字典为空时的内置常用单位兜底（只读引导，unit_id 为空表示未建档）。
BUILT_IN_UNITS = (
    ("斤", "斤"),
    ("公斤", "公斤"),
    ("克", "克"),
    ("份", "份"),
    ("箱", "箱"),
    ("袋", "袋"),
    ("个", "个"),
    ("根", "根"),
    ("把", "把"),
)


def _d(v) -> Decimal:
    if v is None:
        return Decimal("0")
    if isinstance(v, Decimal):
        return v
    return Decimal(str(v))


# ═══════════════════════════════════════════════════════════
# SKU 管理
# ═══════════════════════════════════════════════════════════

# N8：SKU 列表排序白名单（限响应内可排序字段），非法值 422。
# 附 id 作稳定 tiebreaker，保证分页窗口不因同值行序漂移而重叠/丢行。
_SKU_ORDERABLE_FIELDS: dict[str, object] = {
    "name": ProductSKU.name,
    "category_group": ProductSKU.category_group,
    "default_sale_price": ProductSKU.default_sale_price,
    "shelf_life_hours": ProductSKU.shelf_life_hours,
    "created_at": ProductSKU.created_at,
}


@router.get("/skus", response_model=PaginatedResponse)
async def list_skus(
    page: int = Query(1, ge=1, description="页码，从 1 开始"),
    page_size: int = Query(20, ge=1, le=100, description="每页条数，上限 100"),
    order_by: str = Query("name", description="排序字段（白名单）"),
    merchant: Merchant = Depends(get_current_merchant),
    db: AsyncSession = Depends(get_db),
):
    """活跃 SKU 分页列表（N8 修复：此前 page/page_size/order_by 全被忽略、恒返回全量）。

    分页信封沿用项目惯例（PaginatedResponse）：data 仍为列表，
    meta = {page, limit, total}，与库存流水/AI 动作历史一致。
    """
    order_column = _SKU_ORDERABLE_FIELDS.get(order_by)
    if order_column is None:
        raise HTTPException(
            status_code=422,
            detail=f"order_by 仅支持：{'、'.join(_SKU_ORDERABLE_FIELDS)}",
        )

    filters = (
        ProductSKU.merchant_id == merchant.id,
        ProductSKU.is_active == True,  # noqa: E712
    )
    total = (
        await db.execute(select(func.count()).select_from(ProductSKU).where(*filters))
    ).scalar() or 0
    skus = (
        (
            await db.execute(
                select(ProductSKU)
                .where(*filters)
                .order_by(order_column, ProductSKU.id)
                .offset((page - 1) * page_size)
                .limit(page_size)
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
        "meta": {"page": page, "limit": page_size, "total": total},
    }


async def _conversion_hint(
    db: AsyncSession, merchant_id: uuid.UUID, sku: ProductSKU, body: dict
) -> dict | None:
    """主（采购）单位 ≠ 账本基准单位时的引导性提示 — 只读建议，不强制。

    优先取请求里的 primary_unit（采购主单位）；未提供时回退到商户单位
    字典中 is_base 的基础单位。两者与 canonical_unit 相同则不打扰用户。
    """
    from_unit = (body.get("primary_unit") or "").strip() or None
    if from_unit is None:
        from_unit = await db.scalar(
            select(Unit.code)
            .where(
                Unit.merchant_id == merchant_id,
                Unit.is_base == True,  # noqa: E712
            )
            .order_by(Unit.code)
            .limit(1)
        )
    if not from_unit or from_unit == sku.canonical_unit:
        return None
    return {
        "need_conversion": True,
        "from_unit": from_unit,
        "to_unit": sku.canonical_unit,
        "message": (
            f"采购主单位「{from_unit}」与账本基准单位「{sku.canonical_unit}」不同，"
            "建议设置换算（POST /api/v1/catalog/unit-conversions），"
            "否则整件入账无法自动折算为基准单位"
        ),
    }


@router.post("/skus", response_model=AnyResponse)
async def create_sku(
    body: dict,
    merchant: Merchant = Depends(get_current_merchant),
    db: AsyncSession = Depends(get_db),
):
    name = _sanitize_display_name(body.get("name"))
    if not name:
        raise HTTPException(status_code=400, detail="商品名称不能为空")
    # P2-1 修复：名称长度上限（此前 200 字名称可直接落库撑爆列表/卡片）。
    if len(name) > 50:
        raise HTTPException(status_code=422, detail="商品名称不能超过 50 个字")
    # QA-28：计量单位长度上限 ≤16 字（此前 300 字任意串可直接落库）。
    canonical_unit = str(body.get("canonical_unit") or "斤").strip()
    if len(canonical_unit) > 16:
        raise HTTPException(status_code=422, detail="计量单位不能超过 16 个字")
    # P1-5 修复：售价上下限（此前 -1 与 1e13 均落库成功）；QA-11：非数字串 → 422。
    sale_price = _parse_sale_price(body.get("default_sale_price"))
    sku = ProductSKU(
        merchant_id=merchant.id,
        name=name,
        category_group=body.get("category_group"),
        canonical_unit=canonical_unit,
        shelf_life_hours=body.get("shelf_life_hours", 72),
        default_sale_price=sale_price,
    )
    db.add(sku)
    try:
        await db.commit()
    except IntegrityError as e:
        # uq_active_sku_name_per_merchant：同商户活跃同名 SKU 重复
        # （并发创建越过应用层检查时由 DB 约束兜底）。
        await db.rollback()
        raise HTTPException(status_code=409, detail="商品已存在") from e
    await db.refresh(sku)
    data = {"sku_id": str(sku.id), "name": sku.name}
    hint = await _conversion_hint(db, merchant.id, sku, body)
    if hint:
        data["unit_conversion_hint"] = hint
    return {"code": 0, "data": data}


@router.put("/skus/{sku_id}", response_model=AnyResponse)
async def update_sku(
    sku_id: uuid.UUID,
    body: dict,
    merchant: Merchant = Depends(get_current_merchant),
    db: AsyncSession = Depends(get_db),
    # QA-02：改价属高风险操作，此前未挂权限装饰器，cashier 员工可把任意 SKU
    # 价格 3.5 改成 0.01。现与文件内其他端点同款挂 require_permission("change_price")
    # （owner/manager 有此权限，cashier 无 → 403）。
    _perm: PermissionContext = Depends(require_permission("change_price")),
):
    sku = await db.get(ProductSKU, sku_id)
    if not sku or sku.merchant_id != merchant.id:
        raise HTTPException(status_code=404, detail="SKU不存在")
    if "canonical_unit" in body and body["canonical_unit"] is not None:
        # QA-28：更新路径同样限制单位长度 ≤16 字。
        if len(str(body["canonical_unit"]).strip()) > 16:
            raise HTTPException(status_code=422, detail="计量单位不能超过 16 个字")
    for field in ("category_group", "canonical_unit"):
        if field in body:
            setattr(sku, field, body[field])
    if "name" in body:
        sku.name = _sanitize_display_name(body["name"])
    # P2-1：更新路径同样限制名称长度
    if sku.name and len(sku.name) > 50:
        raise HTTPException(status_code=422, detail="商品名称不能超过 50 个字")
    if "shelf_life_hours" in body:
        sku.shelf_life_hours = int(body["shelf_life_hours"])
    if "default_sale_price" in body:
        old_price = sku.default_sale_price
        # QA-11：更新路径同样包 try 解析，非数字串 → 422 而非 500
        new_price = _parse_sale_price(body["default_sale_price"])
        sku.default_sale_price = new_price
        if old_price and new_price is not None and old_price != new_price:
            # QA-33：changed_by 记录操作者身份（员工 PIN 登录后 token 携带
            # staff_id claim，经 require_permission 解析为 _perm.staff_id），
            # 此前恒写死 "merchant"，越权改价无法追责。取不到员工名时兜底"老板"。
            operator = "老板"
            if _perm.staff_id:
                staff = await db.get(StaffMember, _perm.staff_id)
                if staff:
                    operator = staff.name
            db.add(
                PriceHistory(
                    merchant_id=merchant.id,
                    sku_id=sku.id,
                    old_price=old_price,
                    new_price=new_price,
                    reason="manual",
                    changed_by=operator,
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
    # N9：主资源不存在（含跨商户越权探测）→ 404，与 add_alias/更新接口语义一致；
    # 此前返回 200 空数组，无法区分「没有别名」和「SKU 不存在」。
    sku = await db.get(ProductSKU, sku_id)
    if not sku or sku.merchant_id != merchant.id:
        raise HTTPException(status_code=404, detail="SKU不存在")
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
    # 与 aliases/price-history 同语义：主资源不存在（含跨商户越权探测）→ 404，
    # 区分「没有规格」和「SKU 不存在」。
    sku = await db.get(ProductSKU, sku_id)
    if not sku or sku.merchant_id != merchant.id:
        raise HTTPException(status_code=404, detail="SKU不存在")
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
    if not units:
        # P2-9 修复：空字典时返回内置常用单位，单位选择不再面对空列表。
        return {
            "code": 0,
            "data": [
                {
                    "unit_id": None,
                    "code": code,
                    "name": name,
                    "kind": "built_in",
                    "is_base": code == "斤",
                }
                for code, name in BUILT_IN_UNITS
            ],
        }
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
    # N9：同 aliases —— SKU 不存在 / 不属于本商户 → 404，而非 200 空数组。
    sku = await db.get(ProductSKU, sku_id)
    if not sku or sku.merchant_id != merchant.id:
        raise HTTPException(status_code=404, detail="SKU不存在")
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

    # QA2-13/RA-12 补口：limit≤0 → 空列表（与 pos-orders/voice-logs/inventory
    # -history 三域语义一致）。此前 min(limit, 200) 对负数无防护，SQLite 把
    # LIMIT -N 视作无上限返回全量。
    if limit <= 0:
        return {
            "code": 0,
            "data": {
                "items": [],
                "total": 0,
                "blacklisted_count": 0,
                "offset": offset,
                "limit": 0,
            },
        }

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
    name = _sanitize_display_name(body.get("name"))
    if not name:
        raise HTTPException(status_code=400, detail="供应商名称不能为空")
    if len(name) > 50:
        raise HTTPException(status_code=422, detail="供应商名称不能超过 50 个字")
    # QA-08：contact/address 属回显文本，此前未净化，`<script>`/`onerror`/
    # `' or '1'='1` 等 payload 原样落库（存储型 XSS 清洗不一致）。与名称类
    # 字段同款净化，保持既有清理强度不减弱。
    contact = _sanitize_display_name(body.get("contact")) or None
    address = _sanitize_display_name(body.get("address")) or None
    # QA-28：最小起订量不允许负数（此前 -5 可直接落库）。
    min_order_qty = None
    if body.get("min_order_qty") not in (None, ""):
        min_order_qty = Decimal(str(body["min_order_qty"]))
        if min_order_qty < 0:
            raise HTTPException(status_code=422, detail="最小起订量不能为负数")
    s = Supplier(
        merchant_id=merchant.id,
        name=name,
        contact=contact,
        address=address,
        business_category=body.get("business_category"),
        min_order_qty=min_order_qty,
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
    # QA-08：更新路径同样净化 contact/address（与 create 路径同款规则）。
    if "contact" in body:
        s.contact = _sanitize_display_name(body["contact"]) or None
    if "address" in body:
        s.address = _sanitize_display_name(body["address"]) or None
    if "business_category" in body:
        s.business_category = body["business_category"]
    if "name" in body:
        new_name = _sanitize_display_name(body["name"])
        if not new_name:
            raise HTTPException(status_code=422, detail="供应商名称不能为空")
        if len(new_name) > 50:
            raise HTTPException(status_code=422, detail="供应商名称不能超过 50 个字")
        s.name = new_name
    if "min_order_qty" in body:
        # QA-28：更新路径同样拒绝负数最小起订量。
        moq = (
            Decimal(str(body["min_order_qty"])) if body["min_order_qty"] not in (None, "") else None
        )
        if moq is not None and moq < 0:
            raise HTTPException(status_code=422, detail="最小起订量不能为负数")
        s.min_order_qty = moq
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
    # 归属校验：供应商和 SKU 必须属于当前商户，防止跨租户串写关联。
    supplier = await db.get(Supplier, supplier_id)
    if not supplier or supplier.merchant_id != merchant.id:
        raise HTTPException(status_code=404, detail="供应商不存在")
    try:
        sku_uuid = uuid.UUID(body["sku_id"])
    except (KeyError, ValueError, TypeError) as exc:
        raise HTTPException(status_code=400, detail="sku_id 缺失或格式错误") from exc
    sku = await db.get(ProductSKU, sku_uuid)
    if not sku or sku.merchant_id != merchant.id:
        raise HTTPException(status_code=404, detail="SKU不存在")

    sp = SupplierProduct(
        merchant_id=merchant.id,
        supplier_id=supplier_id,
        sku_id=sku_uuid,
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

    Scoring algorithm is implemented once in app.services.supplier_scoring.
    """
    supplier = await db.get(Supplier, supplier_id)
    if not supplier or supplier.merchant_id != merchant.id:
        raise HTTPException(status_code=404, detail="供应商不存在")

    score = await calculate_supplier_score(db, merchant.id, supplier_id)
    if score is None:
        return {
            "code": 0,
            "message": "该供应商暂无采购记录，无法评分",
            "data": {"supplier_id": str(supplier_id), "score": None},
        }

    # Update supplier record
    supplier.shortage_rate = score.shortage_rate.quantize(Decimal("0.01"))
    supplier.return_rate = score.return_rate.quantize(Decimal("0.01"))
    supplier.quality_issue_rate = score.quality_issue_rate.quantize(Decimal("0.01"))
    supplier.on_time_rate = score.on_time_rate.quantize(Decimal("0.01"))
    supplier.composite_score = score.composite_score.quantize(Decimal("0.01"))
    supplier.total_orders = score.total_orders

    from app.models.audit import AuditLog

    db.add(
        AuditLog(
            merchant_id=merchant.id,
            action="supplier_score",
            target_table="suppliers",
            target_id=str(supplier.id),
            after_data={
                "composite_score": float(score.composite_score),
                "shortage_rate": float(score.shortage_rate),
                "return_rate": float(score.return_rate),
                "quality_issue_rate": float(score.quality_issue_rate),
                "on_time_rate": float(score.on_time_rate),
                "total_orders": score.total_orders,
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
            "composite_score": float(score.composite_score),
            "shortage_rate": float(score.shortage_rate),
            "return_rate": float(score.return_rate),
            "quality_issue_rate": float(score.quality_issue_rate),
            "on_time_rate": float(score.on_time_rate),
            "total_orders": score.total_orders,
            "total_expected_qty": float(score.total_expected),
            "total_shortage_qty": float(score.total_shortage),
            "total_returned_qty": float(score.total_returned),
        },
    }
