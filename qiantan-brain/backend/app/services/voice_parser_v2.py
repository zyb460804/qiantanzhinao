"""语音/文本 → 结构化业务事件（新版，对应战略文档第一节「建立真实业务账本」）。

相对旧版 voice_parser.parse_voice_text 的关键升级：
1. 提取【供应商】：从老王那里 / 给张记饭店 / 老王结了…
2. 分离【包装】与【净重】：三筐（包装） + 142斤（净重），不再混为一谈。
3. 别名 → SKU 解析：西红柿/洋柿子 都落到标准 SKU「番茄」。
4. 单位换算：口语单位在边界换算成 SKU 标准单位（由 unit_service 负责）。
5. 单斤成本：采购「花了310块 / 净重142斤」自动算出 2.18 元/斤。
6. 幂等键：客户端可携带，否则按归一化文本派生，防止网络重试重复入账。
7. 不再「unknown 默认 purchase」——未知事件标记为 needs_confirmation。

解析器保持纯函数 + 可注入 catalog resolver，便于单元测试（测试传 fake resolver）。
"""

from __future__ import annotations

import hashlib
import re

from pydantic import BaseModel, Field

from app.services.unit_service import convert, is_package_unit


# ---------------------------------------------------------------------------
# 内置别名表（系统级，覆盖常见同物异名）。生产环境应主要来自 DB product_aliases。
# ---------------------------------------------------------------------------
BUILTIN_ALIAS: dict[str, str] = {
    "西红柿": "番茄",
    "洋柿子": "番茄",
    "番茄": "番茄",
    "洋白菜": "白菜",
    "圆白菜": "白菜",
    "土豆": "土豆",
    "马铃薯": "土豆",
    "李子": "李",
}

# 中文数字（用于口语数量，如 五十斤）
CN_NUM = {
    "零": 0,
    "一": 1,
    "二": 2,
    "两": 2,
    "三": 3,
    "四": 4,
    "五": 5,
    "六": 6,
    "七": 7,
    "八": 8,
    "九": 9,
    "十": 10,
    "百": 100,
    "千": 1000,
    "万": 10000,
}

WEIGHT_UNITS = {"斤", "公斤", "千克", "克", "两", "钱"}
PACKAGE_UNITS = {"筐", "箱", "袋", "件", "盒", "个", "把", "份", "篓", "桶"}

PURCHASE_KW = ["进了", "进来", "进货", "上了", "拉了", "批了", "采购", "买", "收"]
SALE_KW = ["卖了", "卖出", "收入", "收成", "收银", "拿了", "卖得"]
WASTE_KW = ["坏了", "扔了", "烂了", "损耗", "报废", "不能卖", "扔掉"]

# Payment terms: 赊账/记账/欠着 = credit, 现付/现结 = cash
CREDIT_KW = ["欠着", "赊账", "记账", "先欠", "赊", "挂账", "月底结", "月结"]
CASH_KW = ["现付", "现结", "现金", "给现钱", "付现"]

# Specifications: 精品/大果/小果/次品/普通
SPEC_KW = {
    "精品": "精品",
    "特级": "精品",
    "大果": "大果",
    "大个": "大果",
    "小果": "小果",
    "小个": "小果",
    "次品": "次品",
    "差的": "次品",
    "普通": "普通",
    "一般的": "普通",
    "中等": "普通",
}

SUPPLIER_PATTERNS = [
    r"(?:从|跟|给|欠|和|找)\s*([一-龥]{1,6}?)\s*(?:那里|家|老板|师傅|户)",
    r"([一-龥]{1,6}?)(?:饭店|超市|食堂|酒楼|菜场|摊)",
]


class CatalogResolver:
    """生产环境实现：查 DB（product_aliases / unit_conversions）。

    测试用 fake 实现只要提供同签名方法即可（见 tests/test_voice_parser_v2.py）。
    """

    def resolve_alias(self, alias: str) -> tuple[str, str] | None:
        """返回 (sku_id, canonical_unit)，未知返回 None。"""
        return None

    def conversion_lookup(
        self, merchant_id: str, from_unit: str, to_unit: str, sku_id: str | None
    ) -> float | None:
        return None


class BusinessEvent(BaseModel):
    raw_text: str
    idempotency_key: str
    event_type: str  # purchase / sale / waste / unknown
    supplier: str | None = None
    product_alias: str | None = None
    sku_id: str | None = None
    canonical_unit: str = "斤"

    # 规格：精品/大果/小果/次品/普通
    specification: str | None = None

    # 包装与净重分离（旧版完全没有）
    package_count: float | None = None
    package_unit: str | None = None
    net_qty: float | None = None
    net_unit: str | None = None

    # 换算后的标准单位数量（入库/出库都以它为真相）
    quantity_canonical: float | None = None

    unit_cost: float | None = None  # 采购：每标准单位成本
    unit_price: float | None = None  # 销售：每标准单位售价
    total_amount: float | None = None
    currency: str = "CNY"

    # Payment terms: cash / credit
    payment_terms: str | None = None  # "credit" | "cash"

    confidence: float = 1.0
    missing_fields: list[str] = Field(default_factory=list)
    guessed_fields: list[str] = Field(default_factory=list)
    needs_confirmation: bool = False
    note: str = ""

    def to_inventory_payload(self) -> dict:
        """转换为 InventoryRecord 可直接入库的字段（标准单位 + 幂等键）。"""
        signed = self.quantity_canonical or 0
        if self.event_type == "purchase":
            signed = abs(signed)
        elif self.event_type in ("sale", "waste"):
            signed = -abs(signed)
        return {
            "event_type": self.event_type,
            "quantity": signed,
            "unit": self.canonical_unit,
            "unit_cost": self.unit_cost if self.event_type == "purchase" else None,
            "unit_price": self.unit_price if self.event_type == "sale" else None,
            "total_amount": self.total_amount,
            "idempotency_key": self.idempotency_key,
            "supplier": self.supplier,
            "package_count": self.package_count,
            "package_unit": self.package_unit,
            "specification": self.specification,
            "payment_terms": self.payment_terms,
        }


# ---------------------------------------------------------------------------
# 解析工具
# ---------------------------------------------------------------------------


def _normalize_cn_numbers(text: str) -> str:
    def tens_ones(m):
        return str(CN_NUM.get(m.group(1), 0) * 10 + CN_NUM.get(m.group(2), 0))

    def tens(m):
        return str(CN_NUM.get(m.group(1), 0) * 10)

    def ten_plus(m):
        return str(10 + CN_NUM.get(m.group(1), 0))

    text = re.sub(r"([一二三四五六七八九])十([一二三四五六七八九])", tens_ones, text)
    text = re.sub(r"([一二三四五六七八九])十", tens, text)
    text = re.sub(r"十([一二三四五六七八九])", ten_plus, text)
    text = re.sub(r"十", "10", text)
    # 处理孤立的中文数字（如「三筐」→「3筐」；「五十」已被上面的规则转成 50）
    for ch, val in CN_NUM.items():
        if ch == "十":
            continue
        text = text.replace(ch, str(val))
    return text


def _normalize_money(text: str) -> str:
    text = re.sub(r"([一二三四五六七八九])块", lambda m: f"{CN_NUM.get(m.group(1), 0)}元", text)
    text = re.sub(r"(\d+)\s*块钱", r"\1元", text)
    return text


def _extract_supplier(text: str) -> str | None:
    for pat in SUPPLIER_PATTERNS:
        m = re.search(pat, text)
        if m:
            return m.group(1).strip()
    return None


def _detect_event(text: str) -> str:
    for kw in PURCHASE_KW:
        if kw in text:
            return "purchase"
    for kw in SALE_KW:
        if kw in text:
            return "sale"
    for kw in WASTE_KW:
        if kw in text:
            return "waste"
    return "unknown"


def _extract_qty_units(text: str) -> list[tuple[float, str]]:
    """抽取文本中所有「数字+单位」对，保留出现顺序。"""
    results: list[tuple[float, str]] = []
    for m in re.finditer(
        r"(\d+(?:\.\d+)?)\s*(斤|公斤|千克|克|两|钱|筐|箱|袋|件|盒|个|把|份|篓|桶)", text
    ):
        results.append((float(m.group(1)), m.group(2)))
    return results


def _extract_total(text: str) -> float | None:
    # 通用金额识别：数字 + 块/元/块钱，且后面不紧跟重量/计件单位
    # （避免误抓「2元一斤」这类单价）。覆盖「花了310块」「拿了80块」等口语。
    m = re.search(
        r"(\d+(?:\.\d+)?)\s*(?:块|元|块钱)(?!\s*[斤个把箱筐袋件盒公斤千克克两])",
        text,
    )
    if m:
        return float(m.group(1))
    return None


def _extract_spec(text: str) -> str | None:
    """Detect product specification from text (精品/大果/小果/次品)."""
    for kw, spec in SPEC_KW.items():
        if kw in text:
            return spec
    return None


def _detect_payment_terms(text: str) -> str | None:
    """Detect payment terms: credit (赊账/欠着) or cash (现付)."""
    for kw in CREDIT_KW:
        if kw in text:
            return "credit"
    for kw in CASH_KW:
        if kw in text:
            return "cash"
    return None


def _resolve_sku(alias: str | None, resolver: CatalogResolver) -> tuple[str | None, str]:
    if not alias:
        return None, "斤"
    # 1) 内置别名表
    std = BUILTIN_ALIAS.get(alias, alias)
    # 2) 外部 resolver（DB）
    hit = resolver.resolve_alias(std) or resolver.resolve_alias(alias)
    if hit:
        return hit[0], hit[1]
    return None, "斤"


def parse_voice_text_v2(
    text: str,
    *,
    resolver: CatalogResolver | None = None,
    merchant_id: str | None = None,
    idempotency_key: str | None = None,
) -> BusinessEvent:
    """把口语/文本变成结构化业务事件。"""
    resolver = resolver or CatalogResolver()
    raw = (text or "").strip()
    norm = _normalize_cn_numbers(_normalize_money(raw))

    event_type = _detect_event(norm)
    supplier = _extract_supplier(norm)
    total = _extract_total(norm)
    qty_units = _extract_qty_units(norm)
    specification = _extract_spec(norm)
    payment_terms = _detect_payment_terms(norm)

    # 分离包装与净重
    package_count = package_unit = None
    net_qty = net_unit = None
    for qty, unit in qty_units:
        if unit in PACKAGE_UNITS:
            package_count, package_unit = qty, unit
        elif unit in WEIGHT_UNITS:
            net_qty, net_unit = qty, unit

    # 商品别名（取文本中第一个命中的已知别名/标准名）
    product_alias = None
    for known in list(BUILTIN_ALIAS.keys()) + list(BUILTIN_ALIAS.values()):
        if known in norm:
            product_alias = known
            break

    sku_id, canonical_unit = _resolve_sku(product_alias, resolver)

    # 标准单位数量：优先用净重（若其单位就是标准单位或可转换）
    quantity_canonical = None
    if net_qty is not None and net_unit:
        try:
            quantity_canonical = convert(
                net_qty,
                net_unit,
                canonical_unit,
                lookup=resolver.conversion_lookup if merchant_id else None,
                sku_id=sku_id,
                merchant_id=merchant_id,
            )
        except ValueError:
            quantity_canonical = net_qty  # 无换算因子时先用原值，标记待确认
    elif package_count is not None and not is_package_unit(canonical_unit):
        # 只有包装、没有净重：需要用户输入净重或配置换算因子
        pass

    # 成本 / 售价
    unit_cost = unit_price = None
    if event_type == "purchase" and total is not None and net_qty:
        unit_cost = round(total / net_qty, 2)
    if event_type == "sale" and total is not None and net_qty:
        unit_price = round(total / net_qty, 2)

    # 幂等键
    if not idempotency_key:
        idempotency_key = hashlib.sha256(norm.encode("utf-8")).hexdigest()[:32]

    # 置信度与待确认
    missing: list[str] = []
    guessed: list[str] = []
    if event_type == "unknown":
        missing.append("event_type")
    if not product_alias:
        missing.append("product")
    if quantity_canonical is None:
        missing.append("quantity")
    if event_type == "purchase" and supplier is None:
        missing.append("supplier")
    if unit_cost is not None and net_qty:
        guessed.append("unit_cost")

    confidence = max(0.0, min(1.0, 1.0 - 0.1 * len(missing) - 0.05 * len(guessed)))
    needs_confirmation = (
        event_type == "unknown"
        or "product" in missing
        or "quantity" in missing
        or (event_type == "purchase" and "supplier" in missing)
    )

    note = ""
    if package_count and net_qty is None:
        note = "仅识别到包装数量，缺少净重，请补充或配置换算因子"

    return BusinessEvent(
        raw_text=raw,
        idempotency_key=idempotency_key,
        event_type=event_type,
        supplier=supplier,
        product_alias=product_alias,
        sku_id=sku_id,
        canonical_unit=canonical_unit,
        specification=specification,
        package_count=package_count,
        package_unit=package_unit,
        net_qty=net_qty,
        net_unit=net_unit,
        quantity_canonical=quantity_canonical,
        unit_cost=unit_cost,
        unit_price=unit_price,
        total_amount=total,
        payment_terms=payment_terms,
        confidence=round(confidence, 2),
        missing_fields=missing,
        guessed_fields=guessed,
        needs_confirmation=needs_confirmation,
        note=note,
    )
