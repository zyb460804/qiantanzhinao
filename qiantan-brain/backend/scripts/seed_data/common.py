"""增强版种子数据 — 共享常量、商户档案、商品目录与辅助函数。

设计原则
--------
1. **确定性 UUID**：所有实体用固定 UUID，重复运行 `db.get()` 即可判重，
   每个分片幂等、可单独重跑。
2. **三类摊主故事化**：菜摊（全量演示）/ 水果摊（临期+经验云）/ 肉摊
   （食安批次+供应商评分），同一套种子逻辑按档案参数化。
3. **商品目录全局唯一**：product_categories.name 唯一，三摊共享一张目录表，
   各自只经营子集；ProductSKU 按商户隔离。
"""

from __future__ import annotations

import random
import uuid
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal


# ────────────────────────────────────────────────────────────────────
#  固定 UUID（幂等基石）
# ────────────────────────────────────────────────────────────────────

# 三摊主（老张菜摊保留原 seed_db 的 ID，向后兼容）
MERCHANT_VEGETABLE = uuid.UUID("a0000000-0000-0000-0000-000000000001")  # 老张菜摊
MERCHANT_FRUIT = uuid.UUID("a0000000-0000-0000-0000-000000000002")  # 王姐水果摊
MERCHANT_MEAT = uuid.UUID("a0000000-0000-0000-0000-000000000003")  # 刘哥肉摊

ALL_MERCHANT_IDS = [MERCHANT_VEGETABLE, MERCHANT_FRUIT, MERCHANT_MEAT]

# 租户（与 seed_saas 的演示租户对齐，便于鉴权联调）
DEMO_TENANT_ID = uuid.UUID("aaa00000-0000-0000-0000-000000000001")


def sku_uuid(merchant_id: uuid.UUID, product_id: int) -> uuid.UUID:
    """商户 × 商品 → 确定性 SKU UUID（基于 uuid5，跨运行稳定）。"""
    return uuid.uuid5(uuid.NAMESPACE_URL, f"qiantan-sku-{merchant_id}-{product_id}")


def supplier_uuid(idx: int) -> uuid.UUID:
    """第 idx 个供应商基准 UUID（用于跨商户引用标识）。"""
    return uuid.uuid5(uuid.NAMESPACE_URL, f"qiantan-supplier-base-{idx}")


def supplier_id_for(merchant_id: uuid.UUID, idx: int) -> uuid.UUID:
    """商户 × 供应商序号 → 该商户名下的供应商档案副本 UUID。

    suppliers 表 merchant_id 必填，同一供应商在三摊各有一份档案行；
    用 uuid5 保证幂等。
    """
    return uuid.uuid5(uuid.NAMESPACE_URL, f"qiantan-supplier-{merchant_id}-{idx}")


def staff_uuid(merchant_id: uuid.UUID, idx: int) -> uuid.UUID:
    return uuid.uuid5(uuid.NAMESPACE_URL, f"qiantan-staff-{merchant_id}-{idx}")


# ────────────────────────────────────────────────────────────────────
#  商品目录（全局唯一；三摊共享，各取子集）
# ────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class ProductDef:
    """商品目录定义。id 与 product_categories.id 对齐（手动指定保证稳定）。"""

    id: int
    name: str
    unit: str
    default_price: Decimal
    shelf_life_hours: int
    group: str
    # 该商品的经营摊主子集
    merchants: tuple[uuid.UUID, ...]
    # 进货成本区间（占售价比例）
    cost_ratio: tuple[float, float] = (0.50, 0.75)
    # 别名（方言 ASR / 同义词归一演示）
    aliases: tuple[str, ...] = ()


# 原有 10 个（id 1-10，保持与旧 seed 一致）+ 水果 + 肉类扩充
PRODUCTS: tuple[ProductDef, ...] = (
    # ── 蔬菜（老张主营）──
    ProductDef(
        1,
        "白菜",
        "斤",
        Decimal("1.50"),
        72,
        "叶菜类",
        (MERCHANT_VEGETABLE,),
        aliases=("大白菜", "娃娃菜"),
    ),
    ProductDef(
        2, "菠菜", "斤", Decimal("3.00"), 48, "叶菜类", (MERCHANT_VEGETABLE,), aliases=("菠菜草",)
    ),
    ProductDef(
        3,
        "土豆",
        "斤",
        Decimal("2.00"),
        168,
        "根茎类",
        (MERCHANT_VEGETABLE,),
        aliases=("马铃薯", "洋芋"),
    ),
    ProductDef(4, "豆腐", "斤", Decimal("2.50"), 24, "豆制品", (MERCHANT_VEGETABLE,)),
    ProductDef(
        5, "黄瓜", "斤", Decimal("3.50"), 96, "瓜果类", (MERCHANT_VEGETABLE, MERCHANT_FRUIT)
    ),
    ProductDef(
        6,
        "番茄",
        "斤",
        Decimal("4.00"),
        120,
        "瓜果类",
        (MERCHANT_VEGETABLE, MERCHANT_FRUIT),
        aliases=("西红柿", "洋柿子"),
    ),
    ProductDef(
        7, "西瓜", "斤", Decimal("2.00"), 120, "水果类", (MERCHANT_VEGETABLE, MERCHANT_FRUIT)
    ),
    ProductDef(
        8,
        "苹果",
        "斤",
        Decimal("6.00"),
        168,
        "水果类",
        (MERCHANT_VEGETABLE, MERCHANT_FRUIT),
        aliases=("红富士",),
    ),
    ProductDef(9, "猪肉", "斤", Decimal("15.00"), 48, "肉类", (MERCHANT_VEGETABLE, MERCHANT_MEAT)),
    ProductDef(
        10,
        "鸡蛋",
        "斤",
        Decimal("6.00"),
        720,
        "蛋类",
        (MERCHANT_VEGETABLE, MERCHANT_FRUIT, MERCHANT_MEAT),
    ),
    # ── 水果（王姐主营，id 11-17）──
    ProductDef(11, "香蕉", "斤", Decimal("3.50"), 120, "水果类", (MERCHANT_FRUIT,)),
    ProductDef(
        12,
        "橙子",
        "斤",
        Decimal("4.50"),
        240,
        "水果类",
        (MERCHANT_FRUIT,),
        aliases=("脐橙", "甜橙"),
    ),
    ProductDef(13, "葡萄", "斤", Decimal("8.00"), 96, "水果类", (MERCHANT_FRUIT,)),
    ProductDef(14, "草莓", "斤", Decimal("15.00"), 48, "水果类", (MERCHANT_FRUIT,)),
    ProductDef(15, "芒果", "斤", Decimal("9.00"), 120, "水果类", (MERCHANT_FRUIT,)),
    ProductDef(
        16, "梨", "斤", Decimal("3.00"), 168, "水果类", (MERCHANT_FRUIT,), aliases=("鸭梨", "雪梨")
    ),
    ProductDef(17, "桃子", "斤", Decimal("4.00"), 96, "水果类", (MERCHANT_FRUIT,)),
    # ── 肉类（刘哥主营，id 18-23）──
    ProductDef(18, "牛肉", "斤", Decimal("38.00"), 48, "肉类", (MERCHANT_MEAT,)),
    ProductDef(19, "羊肉", "斤", Decimal("42.00"), 48, "肉类", (MERCHANT_MEAT,)),
    ProductDef(20, "鸡腿", "斤", Decimal("11.00"), 72, "肉类", (MERCHANT_MEAT,)),
    ProductDef(21, "排骨", "斤", Decimal("28.00"), 48, "肉类", (MERCHANT_MEAT,)),
    ProductDef(22, "五花肉", "斤", Decimal("18.00"), 48, "肉类", (MERCHANT_MEAT,)),
    ProductDef(23, "鸭肉", "斤", Decimal("16.00"), 48, "肉类", (MERCHANT_MEAT,)),
)

PRODUCTS_BY_ID: dict[int, ProductDef] = {p.id: p for p in PRODUCTS}


def products_for(merchant_id: uuid.UUID) -> tuple[ProductDef, ...]:
    """该商户经营的商品子集。"""
    return tuple(p for p in PRODUCTS if merchant_id in p.merchants)


# ────────────────────────────────────────────────────────────────────
#  商户档案（故事化）
# ────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class MerchantProfile:
    merchant_id: uuid.UUID
    name: str
    business_type: str
    location: str
    story_hook: str  # 答辩一句话故事
    # 经营节奏：每日订单量基数
    daily_order_base: int
    # 进货频率（天/次）
    purchase_interval_days: int


MERCHANTS: tuple[MerchantProfile, ...] = (
    MerchantProfile(
        merchant_id=MERCHANT_VEGETABLE,
        name="老张菜摊",
        business_type="蔬菜水果",
        location="上海市浦东新区兰陵路菜市场 A-12",
        story_hook="用 FIFO + 临期预警把叶菜损耗从 18% 压到 6%",
        daily_order_base=5,
        purchase_interval_days=2,
    ),
    MerchantProfile(
        merchant_id=MERCHANT_FRUIT,
        name="王姐水果铺",
        business_type="水果",
        location="上海市浦东新区兰陵路菜市场 B-07",
        story_hook="靠差分隐私经验云学到隔壁摊的草莓定价，多赚了两成",
        daily_order_base=4,
        purchase_interval_days=3,
    ),
    MerchantProfile(
        merchant_id=MERCHANT_MEAT,
        name="刘哥鲜肉铺",
        business_type="肉类",
        location="上海市浦东新区兰陵路菜市场 C-03",
        story_hook="一批一码追溯 + 快检锁定，30 分钟召回问题排骨",
        daily_order_base=3,
        purchase_interval_days=2,
    ),
)


# ────────────────────────────────────────────────────────────────────
#  供应商档案（全局共享；三摊按品类引用）
# ────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class SupplierDef:
    idx: int
    name: str
    contact: str
    business_category: str
    # 质量评分（演示供应商评分模型）
    shortage_rate: Decimal
    return_rate: Decimal
    quality_issue_rate: Decimal
    on_time_rate: Decimal
    composite_score: Decimal
    is_blacklisted: bool = False
    default_credit_days: int = 15


SUPPLIERS: tuple[SupplierDef, ...] = (
    SupplierDef(
        1,
        "老王蔬菜批发",
        "13800000001",
        "蔬菜批发",
        Decimal("2.10"),
        Decimal("1.50"),
        Decimal("0.80"),
        Decimal("96.00"),
        Decimal("92.50"),
    ),
    SupplierDef(
        2,
        "张姐水果直供",
        "13800000002",
        "水果批发",
        Decimal("3.20"),
        Decimal("2.00"),
        Decimal("1.20"),
        Decimal("91.00"),
        Decimal("88.00"),
    ),
    SupplierDef(
        3,
        "李记肉联厂",
        "13800000003",
        "猪牛羊肉",
        Decimal("1.50"),
        Decimal("0.50"),
        Decimal("0.30"),
        Decimal("98.00"),
        Decimal("95.80"),
    ),
    SupplierDef(
        4,
        "惠民粮油",
        "13800000004",
        "豆制品蛋类",
        Decimal("1.00"),
        Decimal("0.80"),
        Decimal("0.50"),
        Decimal("94.00"),
        Decimal("91.20"),
    ),
    SupplierDef(
        5,
        "鲜达禽业",
        "13800000005",
        "鸡鸭禽类",
        Decimal("2.50"),
        Decimal("1.80"),
        Decimal("1.00"),
        Decimal("89.00"),
        Decimal("85.00"),
    ),
    SupplierDef(
        6,
        "宏发农贸（已拉黑）",
        "13800000006",
        "综合批发",
        Decimal("8.50"),
        Decimal("6.20"),
        Decimal("5.10"),
        Decimal("72.00"),
        Decimal("58.00"),
        is_blacklisted=True,
    ),
)

SUPPLIERS_BY_NAME: dict[str, SupplierDef] = {s.name: s for s in SUPPLIERS}


# ────────────────────────────────────────────────────────────────────
#  员工档案（每摊 3-4 人，覆盖不同角色演示权限）
# ────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class StaffDef:
    name: str
    role: str
    phone: str


STAFF_BY_MERCHANT: dict[uuid.UUID, tuple[StaffDef, ...]] = {
    # 摊主本人（张建国/王丽萍/刘大强）不设 owner 员工行（V3-H1）：owner 只能
    # 由商户本人（merchants）承载，员工体系的 owner 角色会被 staff_login
    # 直接 403、迁移 n6d7e8f9a0b1 也会把存量 owner 行降级——seed 不再复活
    # 该角色，摊主行改为权限次高的 manager 演示多角色权限矩阵。
    MERCHANT_VEGETABLE: (
        StaffDef("张建国", "manager", "13900000001"),
        StaffDef("王秀兰", "manager", "13900000002"),
        StaffDef("李小妹", "cashier", "13900000003"),
        StaffDef("赵师傅", "purchaser", "13900000004"),
    ),
    MERCHANT_FRUIT: (
        StaffDef("王丽萍", "manager", "13900000005"),
        StaffDef("陈阿姨", "cashier", "13900000006"),
        StaffDef("小周", "stocker", "13900000007"),
    ),
    MERCHANT_MEAT: (
        StaffDef("刘大强", "manager", "13900000008"),
        StaffDef("孙师傅", "manager", "13900000009"),
        StaffDef("马小哥", "cashier", "13900000010"),
        StaffDef("老陈", "purchaser", "13900000011"),
    ),
}


# ────────────────────────────────────────────────────────────────────
#  赊账客户（演示往来账 + 信用额度）
# ────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class CreditCustomerDef:
    name: str
    merchant_id: uuid.UUID
    credit_limit: Decimal
    default_credit_days: int
    is_blocked: bool
    block_reason: str | None


CREDIT_CUSTOMERS: tuple[CreditCustomerDef, ...] = (
    CreditCustomerDef("张记饭店", MERCHANT_VEGETABLE, Decimal("2000.00"), 15, False, None),
    CreditCustomerDef("李婶食堂", MERCHANT_VEGETABLE, Decimal("1500.00"), 10, False, None),
    CreditCustomerDef(
        "老赵排档", MERCHANT_VEGETABLE, Decimal("800.00"), 7, True, "逾期 45 天未结，暂停赊账"
    ),
    CreditCustomerDef("阳光幼儿园", MERCHANT_FRUIT, Decimal("3000.00"), 20, False, None),
    CreditCustomerDef("天天茶餐厅", MERCHANT_MEAT, Decimal("5000.00"), 15, False, None),
    CreditCustomerDef(
        "胖子烧烤", MERCHANT_MEAT, Decimal("1000.00"), 7, True, "累计欠款超额度，已停赊"
    ),
)


# ────────────────────────────────────────────────────────────────────
#  辅助函数
# ────────────────────────────────────────────────────────────────────

# 固定随机种子：演示数据每次生成都一样（答辩可复现）
RNG_SEED = 20260721


def make_rng() -> random.Random:
    return random.Random(RNG_SEED)


def days_ago(d: int, *, hour: int = 0, minute: int = 0) -> datetime:
    """N 天前的某个时刻（基于今天）。"""
    base = date.today() - timedelta(days=d)
    return datetime(base.year, base.month, base.day, hour, minute)


def date_ago(d: int) -> date:
    return date.today() - timedelta(days=d)


def money(v: float | int | str) -> Decimal:
    """统一 Decimal 金额（红线 #7：金额禁止 float 累加）。"""
    return Decimal(str(v)).quantize(Decimal("0.01"))


def qty(v: float | int | str) -> Decimal:
    return Decimal(str(v)).quantize(Decimal("0.01"))


# ────────────────────────────────────────────────────────────────────
#  幂等写入辅助
# ────────────────────────────────────────────────────────────────────


async def get_or_create(db, model, pk_value, **fields):
    """按主键判重：存在跳过，不存在则 add。返回 (obj, created)。"""
    obj = await db.get(model, pk_value)
    if obj is not None:
        return obj, False
    obj = model(id=pk_value, **fields)
    db.add(obj)
    return obj, True


async def count_rows(db, model, merchant_id: uuid.UUID) -> int:
    """统计某商户在某表的行数（用于判重批量数据）。"""
    from sqlalchemy import func, select

    result = await db.execute(
        select(func.count()).select_from(model).where(model.merchant_id == merchant_id)
    )
    return int(result.scalar_one())
