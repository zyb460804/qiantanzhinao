"""单位换算服务 — 账本口径统一的单一事实来源。

核心原则（这是摊主账本不出错的根基）：
- 每一条 InventoryRecord 的数量，必须以该 SKU 的「标准单位」(canonical_unit) 入账。
  界面可以说 筐/袋/件，但流水里永远是 斤（或 个/盒）。
- 换算只在「边界」完成：语音解析、采购单导入、POS 称重时把口语单位换算成标准单位。
- 换算因子优先来自 DB（unit_conversions，可随商品/商家不同），无 DB 时回退到内置表。

工程要点：
- 纯函数 + 可注入 lookup，便于单元测试（测试里传 fake resolver，不依赖数据库）。
- 内置表 BUILTIN_FACTORS 覆盖常见重量换算；包装/计件换算（筐→斤）必须走 DB，
  因为一筐西红柿和一筐土豆的重量天差地别，不可能写死。
"""

from __future__ import annotations

from collections.abc import Callable


# 内置回退：1 个 from_unit = ? 斤。包装/计件（筐/袋/件…）为 None，必须走 DB。
BUILTIN_FACTORS: dict[str, float | None] = {
    "斤": 1.0,
    "公斤": 2.0,
    "千克": 2.0,
    "两": 0.1,
    "克": 0.002,
    "钱": 0.01,
    "筐": None,
    "箱": None,
    "袋": None,
    "件": None,
    "盒": None,
    "个": None,
    "把": None,
    "份": None,
}

# 换算因子查询函数签名：(merchant_id, from_unit, to_unit, sku_id|None) -> factor|None
ConversionLookup = Callable[[str, str, str, str | None], float | None]


def convert(
    quantity: float,
    from_unit: str,
    to_unit: str,
    *,
    lookup: ConversionLookup | None = None,
    sku_id: str | None = None,
    merchant_id: str | None = None,
) -> float:
    """把 quantity 从 from_unit 换算到 to_unit，返回标准单位下的数量。

    规则：
    - from == to：直接返回。
    - 同为标准重量单位（斤/公斤/克…）：用因子连乘到 斤 再转出。
    - 涉及包装/计件（筐/袋…）：必须提供 lookup（DB），否则抛 ValueError。
    """
    if quantity is None:
        raise ValueError("quantity 不能为空")
    from_unit = (from_unit or "").strip()
    to_unit = (to_unit or "").strip()
    if not from_unit or not to_unit:
        raise ValueError("from_unit / to_unit 不能为空")

    if from_unit == to_unit:
        return float(quantity)

    # 1) 尝试 DB 直接换算（最准确，含商品级因子）
    if lookup is not None and merchant_id is not None:
        direct = lookup(merchant_id, from_unit, to_unit, sku_id)
        if direct is not None:
            return round(float(quantity) * float(direct), 4)

    # 2) 走「以斤为中介」的通用路径
    f_from = _factor_to_jin(from_unit, lookup, merchant_id, sku_id)
    f_to = _factor_to_jin(to_unit, lookup, merchant_id, sku_id)
    # 斤数 = quantity * f_from；再 / f_to 得到目标单位
    jin = float(quantity) * f_from
    return round(jin / f_to, 4)


def _factor_to_jin(
    unit: str,
    lookup: ConversionLookup | None,
    merchant_id: str | None,
    sku_id: str | None,
) -> float:
    """返回 1 个 unit 等于多少 斤。包装单位必须能从 lookup 拿到因子。"""
    # DB 优先：包装/计件单位在内置表里是 None，必须查 DB
    if lookup is not None and merchant_id is not None:
        db_factor = lookup(merchant_id, unit, "斤", sku_id)
        if db_factor is not None:
            return float(db_factor)
    builtin = BUILTIN_FACTORS.get(unit)
    if builtin is None:
        raise ValueError(f"未知单位 '{unit}'，且无数据库换算因子；包装/计件单位必须配置换算关系")
    return float(builtin)


def is_package_unit(unit: str) -> bool:
    """判断是否为包装/计件单位（需要换算到重量才有意义）。"""
    return BUILTIN_FACTORS.get((unit or "").strip()) is None
