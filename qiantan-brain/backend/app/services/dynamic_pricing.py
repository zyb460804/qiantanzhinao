"""Dynamic pricing & markdown engine for perishable goods.

参考项目:
  - normanrz/dynamic-prices: https://github.com/normanrz/dynamic-prices
    → 需求弹性建模 + 最优价格策略 + 仿真引擎
  - amattas/retail-demo: https://github.com/amattas/retail-demo/pull/250
    → 基于规则的三阶段降价引擎 (Age/Inventory/Seasonal)
  - Dynamic_Noshinom: https://github.com/shrryl/Dynamic_Noshinom
    → Firebase实时价格 + Arduino电子价签
  - Tencent Cloud DQN: 200行深度强化学习动态定价
    → DQN state/action/reward 设计模式

核心概念:
  - 生鲜产品价值随时间指数衰减 (Arrhenius Q10模型)
  - 降价策略: 基于剩余货架期的阶梯式降价
  - 价格弹性: 降价幅度 → 需求增量预测
  - 底价约束: 不低于成本 / (1 - 最低毛利率)
  - 出清策略: 关门前2小时激进降价

策略类型:
  - AGE_BASED: 按已消耗货架期百分比阶梯降价
  - INVENTORY_BASED: 库存超出预测时触发降价
  - COMBINED: 加权货龄 + 库存双重信号
  - CLEARANCE: 临近保质期/关门的激进出清
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from typing import Optional


# ── 策略枚举 ──────────────────────────────────────────────────────
class MarkdownStrategy(str, Enum):
    AGE_BASED = "age_based"          # 按剩余货架期
    INVENTORY_BASED = "inventory_based"  # 按库存水平
    COMBINED = "combined"            # 综合信号
    CLEARANCE = "clearance"          # 关门前出清


class PriceTier(str, Enum):
    """价格档位 — 控制降价激进程度。"""
    CONSERVATIVE = "conservative"  # 保守: 保利润
    BALANCED = "balanced"          # 平衡: 利润与出清兼顾
    AGGRESSIVE = "aggressive"      # 激进: 优先出清


# ── 数据类 ────────────────────────────────────────────────────────
@dataclass
class PricingContext:
    """定价决策所需的上下文信息。"""
    product_name: str
    category: str                          # 品类 (蔬菜/水果/肉类/水产/熟食...)
    unit_cost: float                       # 单位成本
    original_price: float                  # 原始售价
    current_inventory: float               # 当前库存
    daily_forecast: float                  # 预测日销量
    shelf_life_hours: int = 72            # 总货架期(小时)
    hours_since_arrival: float = 0        # 已上架小时数
    hours_until_close: float = 8          # 距离关门小时数
    demand_std: float = 0                 # 需求标准差
    min_margin: float = 0.10              # 最低毛利率


@dataclass
class MarkdownStep:
    """一个降价阶梯。"""
    trigger_hours_remaining: float  # 剩余货架期低于此时触发
    discount_pct: float             # 降价百分比 (0.0-1.0)
    label: str                      # 人类可读标签


@dataclass
class PricingRecommendation:
    """定价建议输出。"""
    product_name: str
    strategy: MarkdownStrategy
    original_price: float
    recommended_price: float
    discount_pct: float
    reason: str
    urgency: str                    # low / medium / high / critical
    expected_revenue: float         # 预期收入 (按当前库存)
    expected_waste_pct: float       # 预期损耗率
    alternative_prices: list[dict] = field(default_factory=list)


# ── 品类货架期常量 (Q10=2, 基准温度20°C) ──────────────────────────
CATEGORY_SHELF_LIFE: dict[str, int] = {
    "vegetable": 72,     # 蔬菜: 3天
    "leafy_green": 36,   # 叶菜: 1.5天
    "fruit": 96,         # 水果: 4天
    "meat": 48,          # 鲜肉: 2天
    "seafood": 24,       # 水产: 1天
    "cooked_food": 12,   # 熟食: 半天
    "dairy": 120,        # 乳制品: 5天
    "dry_goods": 2160,   # 干货: 90天 (基本不涉及降价)
    "default": 72,
}

# 温度修正系数 (每升高10°C, 变质速率翻倍)
# 返回 shelf_life 的修正因子
def temp_correction(actual_temp_c: float, base_temp_c: float = 20.0) -> float:
    """Q10温度修正: 温度越高, 有效货架期越短。"""
    delta = actual_temp_c - base_temp_c
    return 2.0 ** (delta / 10.0)


# ── 质量衰减模型 ──────────────────────────────────────────────────
def quality_factor(
    hours_since_arrival: float,
    total_shelf_life_hours: float,
    actual_temp_c: float = 20.0,
) -> float:
    """计算当前质量因子 (0.0-1.0)。

    使用指数衰减: Q(t) = exp(-λ * t)
    其中 λ = ln(2) / half_life (Arrhenius修正后)
    当 t = total_shelf_life 时, Q ≈ 0.05 (基本不能卖)
    """
    correction = temp_correction(actual_temp_c)
    effective_life = total_shelf_life_hours / correction
    if effective_life <= 0:
        return 0.0

    # 设定半衰期 = effective_life / 3 (即经过1/3货架期后质量降低一半)
    half_life = effective_life / 3.0
    lam = math.log(2) / half_life
    q = math.exp(-lam * hours_since_arrival)
    return max(0.0, min(1.0, q))


# ── 降价阶梯 (基于质量因子) ──────────────────────────────────────
# 质量因子阈值 → 降价建议
# quality >= 0.9: 全价
# 0.7 <= quality < 0.9: 轻微折扣
# 0.5 <= quality < 0.7: 中等折扣
# 0.3 <= quality < 0.5: 大幅折扣
# quality < 0.3: 出清价
DEFAULT_MARKDOWN_STEPS: list[MarkdownStep] = [
    MarkdownStep(trigger_hours_remaining=0.7, discount_pct=0.0, label="全价"),
    MarkdownStep(trigger_hours_remaining=0.5, discount_pct=0.10, label="9折-轻微"),
    MarkdownStep(trigger_hours_remaining=0.3, discount_pct=0.20, label="8折-中等"),
    MarkdownStep(trigger_hours_remaining=0.15, discount_pct=0.30, label="7折-大幅"),
    MarkdownStep(trigger_hours_remaining=0.05, discount_pct=0.50, label="5折-出清"),
]


# ── 需求弹性模型 ──────────────────────────────────────────────────
def estimate_demand_uplift(
    discount_pct: float,
    category: str = "default",
) -> float:
    """估算降价带来的需求增量倍率。

    不同品类价格弹性不同:
      - 蔬菜/水果: 弹性高 (降价10% → 需求+18%)
      - 肉类: 弹性中等 (降价10% → 需求+12%)
      - 水产: 弹性低 (新鲜度决定, 价格次要)
      - 熟食: 弹性中等
    """
    elasticity: dict[str, float] = {
        "vegetable": 1.8,
        "leafy_green": 2.0,
        "fruit": 1.6,
        "meat": 1.2,
        "seafood": 0.8,
        "cooked_food": 1.3,
        "dairy": 1.1,
        "dry_goods": 0.3,
        "default": 1.5,
    }
    e = elasticity.get(category, elasticity["default"])
    # 线性需求模型: ΔD/D = e * ΔP/P
    uplift = 1.0 + e * discount_pct
    return max(1.0, uplift)  # 降价不会减少需求


# ── 核心定价引擎 ──────────────────────────────────────────────────
class DynamicPricingEngine:
    """动态定价引擎 — 为生鲜摊贩量身定制的智能降价系统。

    使用方式:
        engine = DynamicPricingEngine(price_tier=PriceTier.BALANCED)
        rec = engine.recommend(ctx)

    三种定价档位:
      - CONSERVATIVE: 降幅小, 保利润 (适合高毛利/长保质期)
      - BALANCED: 平衡利润与出清 (默认)
      - AGGRESSIVE: 优先出清 (适合叶菜/水产等短保质期)
    """

    def __init__(
        self,
        price_tier: PriceTier = PriceTier.BALANCED,
        markdown_steps: list[MarkdownStep] | None = None,
        ambient_temp_c: float = 25.0,
    ):
        self.price_tier = price_tier
        self.markdown_steps = markdown_steps or DEFAULT_MARKDOWN_STEPS
        self.ambient_temp_c = ambient_temp_c

        # 档位调整系数
        self._tier_multipliers = {
            PriceTier.CONSERVATIVE: 0.6,
            PriceTier.BALANCED: 1.0,
            PriceTier.AGGRESSIVE: 1.5,
        }

    # ── 主入口 ──────────────────────────────────────────────────
    def recommend(self, ctx: PricingContext) -> PricingRecommendation:
        """综合定价建议 — 自动选择最佳策略。"""
        total_life = ctx.shelf_life_hours or CATEGORY_SHELF_LIFE.get(
            ctx.category, CATEGORY_SHELF_LIFE["default"]
        )

        q = quality_factor(ctx.hours_since_arrival, total_life, self.ambient_temp_c)

        # 库存覆盖天数
        days_cover = ctx.current_inventory / max(ctx.daily_forecast, 0.01)

        # ── 策略选择 ────────────────────────────────────────────
        strategy = self._select_strategy(ctx, q, days_cover)

        if strategy == MarkdownStrategy.AGE_BASED:
            rec = self._age_based_pricing(ctx, q, total_life)
        elif strategy == MarkdownStrategy.INVENTORY_BASED:
            rec = self._inventory_based_pricing(ctx, days_cover)
        elif strategy == MarkdownStrategy.CLEARANCE:
            rec = self._clearance_pricing(ctx, q)
        else:  # COMBINED
            rec = self._combined_pricing(ctx, q, days_cover, total_life)

        return rec

    def _select_strategy(
        self,
        ctx: PricingContext,
        quality: float,
        days_cover: float,
    ) -> MarkdownStrategy:
        """自动选择最优定价策略。"""
        # 关门前2小时 → 出清模式
        if ctx.hours_until_close <= 2 and ctx.current_inventory > 0:
            return MarkdownStrategy.CLEARANCE

        # 质量已严重下降 → 出清
        if quality < 0.3:
            return MarkdownStrategy.CLEARANCE

        # 库存严重过剩 (>3天覆盖) → 库存驱动降价
        if days_cover > 3:
            return MarkdownStrategy.INVENTORY_BASED

        # 质量下降 + 库存偏高 → 综合
        if quality < 0.7 and days_cover > 1.5:
            return MarkdownStrategy.COMBINED

        # 默认 → 按货龄
        return MarkdownStrategy.AGE_BASED

    # ── 策略实现 ────────────────────────────────────────────────

    def _age_based_pricing(
        self,
        ctx: PricingContext,
        quality: float,
        total_life: float,
    ) -> PricingRecommendation:
        """基于货龄的阶梯降价。

        质量因子越高 → 折扣越小 (越接近原价)
        质量因子越低 → 折扣越大
        """
        tier = self._tier_multipliers[self.price_tier]

        # 找匹配的降价阶梯
        discount = 0.0
        label = "全价"
        quality_pct = quality  # 0.0-1.0

        for step in sorted(
            self.markdown_steps,
            key=lambda s: s.trigger_hours_remaining,
            reverse=True,
        ):
            if quality_pct <= step.trigger_hours_remaining:
                discount = step.discount_pct * tier
                label = step.label
                break

        discount = min(discount, 0.70)  # 最多打3折

        recommended_price = round(ctx.original_price * (1.0 - discount), 2)
        min_price = self._floor_price(ctx)
        recommended_price = max(recommended_price, min_price)

        # 预期效果
        demand_uplift = estimate_demand_uplift(discount, ctx.category)
        effective_demand = ctx.daily_forecast * demand_uplift
        expected_sell = min(ctx.current_inventory, effective_demand)
        expected_waste = max(0.0, ctx.current_inventory - expected_sell) / max(ctx.current_inventory, 0.01)
        expected_revenue = expected_sell * recommended_price

        return PricingRecommendation(
            product_name=ctx.product_name,
            strategy=MarkdownStrategy.AGE_BASED,
            original_price=ctx.original_price,
            recommended_price=recommended_price,
            discount_pct=round(discount * 100, 1),
            reason=f"货架期已过{round((1-quality)*100)}% — 质量评分{round(quality*100)}% → {label}",
            urgency=self._urgency(quality, ctx.current_inventory / max(ctx.daily_forecast, 0.01)),
            expected_revenue=round(expected_revenue, 2),
            expected_waste_pct=round(expected_waste * 100, 1),
        )

    def _inventory_based_pricing(
        self,
        ctx: PricingContext,
        days_cover: float,
    ) -> PricingRecommendation:
        """基于库存水平的降价 — 库存积压时触发。"""
        tier = self._tier_multipliers[self.price_tier]

        # 库存过剩程度
        excess_ratio = days_cover / 3.0  # >1 表示超过3天覆盖

        # 过剩越多, 折扣越大
        if excess_ratio <= 1.0:
            discount = 0.0
        elif excess_ratio <= 1.5:
            discount = 0.10 * tier
        elif excess_ratio <= 2.5:
            discount = 0.20 * tier
        elif excess_ratio <= 4.0:
            discount = 0.30 * tier
        else:
            discount = 0.40 * tier

        discount = min(discount, 0.60)
        recommended_price = round(ctx.original_price * (1.0 - discount), 2)
        min_price = self._floor_price(ctx)
        recommended_price = max(recommended_price, min_price)

        demand_uplift = estimate_demand_uplift(discount, ctx.category)
        effective_demand = ctx.daily_forecast * demand_uplift
        expected_sell = min(ctx.current_inventory, effective_demand)
        expected_waste = max(0.0, ctx.current_inventory - expected_sell) / max(ctx.current_inventory, 0.01)
        expected_revenue = expected_sell * recommended_price

        label = f"{round((1-discount)*10)}折" if discount > 0 else "原价"
        return PricingRecommendation(
            product_name=ctx.product_name,
            strategy=MarkdownStrategy.INVENTORY_BASED,
            original_price=ctx.original_price,
            recommended_price=recommended_price,
            discount_pct=round(discount * 100, 1),
            reason=f"库存可售{days_cover:.1f}天(超出3天正常水平) → {label}促销",
            urgency="medium" if days_cover > 5 else "low",
            expected_revenue=round(expected_revenue, 2),
            expected_waste_pct=round(expected_waste * 100, 1),
        )

    def _clearance_pricing(
        self,
        ctx: PricingContext,
        quality: float,
    ) -> PricingRecommendation:
        """关门前/临期出清 — 激进降价清库存。"""
        tier = self._tier_multipliers[self.price_tier]

        # 基础出清折扣
        if quality < 0.15:
            base_discount = 0.60  # 4折
        elif quality < 0.3:
            base_discount = 0.45  # 5.5折
        elif ctx.hours_until_close <= 1:
            base_discount = 0.40  # 6折
        elif ctx.hours_until_close <= 2:
            base_discount = 0.30  # 7折
        else:
            base_discount = 0.20  # 8折

        discount = min(base_discount * tier, 0.80)  # 最多2折
        recommended_price = round(ctx.original_price * (1.0 - discount), 2)

        # 出清底价 = max(成本价, 残值)
        floor = max(ctx.unit_cost * 0.7, ctx.original_price * 0.1)
        recommended_price = max(recommended_price, round(floor, 2))

        # 出清策略: 不关心利润, 只求少亏
        demand_uplift = estimate_demand_uplift(discount, ctx.category)
        effective_demand = ctx.daily_forecast * demand_uplift * 1.5  # 出清时额外1.5倍需求
        expected_sell = min(ctx.current_inventory, effective_demand)
        expected_waste = max(0.0, ctx.current_inventory - expected_sell) / max(ctx.current_inventory, 0.01)
        expected_revenue = expected_sell * recommended_price

        close_str = f"距关门{ctx.hours_until_close:.0f}小时" if ctx.hours_until_close <= 2 else ""
        quality_str = f"质量仅{round(quality*100)}%" if quality < 0.3 else ""
        reason_parts = [p for p in [close_str, quality_str, "出清"] if p]

        return PricingRecommendation(
            product_name=ctx.product_name,
            strategy=MarkdownStrategy.CLEARANCE,
            original_price=ctx.original_price,
            recommended_price=recommended_price,
            discount_pct=round(discount * 100, 1),
            reason="、".join(reason_parts),
            urgency="critical",
            expected_revenue=round(expected_revenue, 2),
            expected_waste_pct=round(expected_waste * 100, 1),
        )

    def _combined_pricing(
        self,
        ctx: PricingContext,
        quality: float,
        days_cover: float,
        total_life: float,
    ) -> PricingRecommendation:
        """综合定价 — 同时考虑货龄和库存的信号加权。"""
        tier = self._tier_multipliers[self.price_tier]

        # 分别计算两种信号的建议折扣
        age_rec = self._age_based_pricing(ctx, quality, total_life)
        inv_rec = self._inventory_based_pricing(ctx, days_cover)

        # 加权融合: 质量信号权重 0.6, 库存信号权重 0.4
        age_discount = age_rec.discount_pct / 100.0
        inv_discount = inv_rec.discount_pct / 100.0

        # 品质越差, 质量权重越高
        quality_weight = 0.4 + 0.3 * (1.0 - quality)
        inventory_weight = 1.0 - quality_weight

        combined_discount = (
            quality_weight * age_discount + inventory_weight * inv_discount
        )
        combined_discount = min(combined_discount * tier, 0.65)

        recommended_price = round(ctx.original_price * (1.0 - combined_discount), 2)
        recommended_price = max(recommended_price, self._floor_price(ctx))

        demand_uplift = estimate_demand_uplift(combined_discount, ctx.category)
        effective_demand = ctx.daily_forecast * demand_uplift
        expected_sell = min(ctx.current_inventory, effective_demand)
        expected_waste = max(0.0, ctx.current_inventory - expected_sell) / max(ctx.current_inventory, 0.01)
        expected_revenue = expected_sell * recommended_price

        return PricingRecommendation(
            product_name=ctx.product_name,
            strategy=MarkdownStrategy.COMBINED,
            original_price=ctx.original_price,
            recommended_price=recommended_price,
            discount_pct=round(combined_discount * 100, 1),
            reason=(
                f"质量{round(quality*100)}% × 库存{days_cover:.1f}天 → "
                f"综合折扣{round(combined_discount*100)}%"
            ),
            urgency=self._urgency(quality, days_cover),
            expected_revenue=round(expected_revenue, 2),
            expected_waste_pct=round(expected_waste * 100, 1),
            alternative_prices=[
                {"strategy": "纯货龄", "price": age_rec.recommended_price},
                {"strategy": "纯库存", "price": inv_rec.recommended_price},
            ],
        )

    # ── 辅助方法 ──────────────────────────────────────────────────

    def _floor_price(self, ctx: PricingContext) -> float:
        """计算底价 — 不低于成本 / (1 - 最低毛利率)。"""
        if ctx.unit_cost <= 0:
            return ctx.original_price * 0.3
        floor = ctx.unit_cost / (1.0 - ctx.min_margin)
        # 但也允许赔本出清: 底价至少是成本的50%
        absolute_floor = ctx.unit_cost * 0.5
        return min(floor, absolute_floor)

    def _urgency(self, quality: float, days_cover: float) -> str:
        """判定紧急程度。"""
        if quality < 0.15 or days_cover <= 0:
            return "critical"
        if quality < 0.3 or days_cover <= 0.5:
            return "high"
        if quality < 0.6 or days_cover <= 1.5:
            return "medium"
        return "low"

    # ── 批量定价 ──────────────────────────────────────────────
    def batch_recommend(
        self,
        contexts: list[PricingContext],
    ) -> list[PricingRecommendation]:
        """批量定价建议。按紧急程度排序。"""
        recs = [self.recommend(ctx) for ctx in contexts]
        urgency_order = {"critical": 0, "high": 1, "medium": 2, "low": 3}
        recs.sort(key=lambda r: urgency_order.get(r.urgency, 99))
        return recs

    # ── What-If 场景 ────────────────────────────────────────────
    def simulate(
        self,
        ctx: PricingContext,
        discount_pcts: list[float] | None = None,
    ) -> list[dict]:
        """模拟不同折扣率下的利润/损耗/收入。

        返回每种折扣率下的预期结果，帮助决策。
        """
        if discount_pcts is None:
            discount_pcts = [0.0, 0.05, 0.10, 0.15, 0.20, 0.25, 0.30, 0.40, 0.50]

        results = []
        for d in discount_pcts:
            price = ctx.original_price * (1.0 - d)
            floor = self._floor_price(ctx)
            price = max(price, floor)

            demand_uplift = estimate_demand_uplift(d, ctx.category)
            effective_demand = ctx.daily_forecast * demand_uplift
            expected_sell = min(ctx.current_inventory, effective_demand)
            expected_waste = max(0.0, ctx.current_inventory - expected_sell)
            revenue = expected_sell * price
            cost = ctx.current_inventory * ctx.unit_cost
            profit = revenue - cost
            margin = profit / revenue if revenue > 0 else -999.0

            results.append({
                "discount_pct": round(d * 100, 1),
                "price": round(price, 2),
                "expected_sell": round(expected_sell, 1),
                "expected_waste": round(expected_waste, 1),
                "revenue": round(revenue, 2),
                "profit": round(profit, 2),
                "margin_pct": round(margin * 100, 1),
                "waste_rate": round(expected_waste / max(ctx.current_inventory, 0.01) * 100, 1),
            })

        return results


# ── 便捷函数 ──────────────────────────────────────────────────────
def recommend_price(
    product_name: str,
    category: str,
    unit_cost: float,
    original_price: float,
    current_inventory: float,
    daily_forecast: float,
    hours_since_arrival: float = 0,
    hours_until_close: float = 8,
    shelf_life_hours: int | None = None,
    price_tier: PriceTier = PriceTier.BALANCED,
    ambient_temp_c: float = 25.0,
) -> PricingRecommendation:
    """一键定价建议 — 最简单的调用入口。"""
    sl = shelf_life_hours or CATEGORY_SHELF_LIFE.get(category, 72)
    ctx = PricingContext(
        product_name=product_name,
        category=category,
        unit_cost=unit_cost,
        original_price=original_price,
        current_inventory=current_inventory,
        daily_forecast=daily_forecast,
        shelf_life_hours=sl,
        hours_since_arrival=hours_since_arrival,
        hours_until_close=hours_until_close,
    )
    engine = DynamicPricingEngine(price_tier=price_tier, ambient_temp_c=ambient_temp_c)
    return engine.recommend(ctx)
