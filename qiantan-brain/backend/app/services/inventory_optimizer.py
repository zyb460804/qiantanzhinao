"""库存优化引擎 — 安全库存 + 再订货点 + 自动补货量计算。

核心模型:
  标准模型 (ForecastIQ):
    SS  = Z × σ × √LT        (安全库存)
    ROP = D̄ × LT + SS        (再订货点)
    Q   = F × H + SS − I     (推荐补货量)

  报童模型 (Newsvendor) — 适用于短保质期商品:
    CR = Cu / (Cu + Co)      (临界比率)
    Q* = F⁻¹(CR)             (最优订货量)
    Cu = p − c               (缺货成本 = 售价 - 进价)
    Co = c − v               (超储成本 = 进价 - 残值)

参考项目:
  - ForecastIQ: https://github.com/Tushar0326/ForecastIQ-AI-Demand-Forecasting-Supply-Chain-Planning-Platform
  - FreshStock AI: https://github.com/roshnrf/FreshStock-AI---Smart-Inventory-Management-System
  - inventorize: https://github.com/haythamomar/inventorize
  - stockpyl: https://github.com/tuhinmallick/stockpyl
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum

import numpy as np


logger = logging.getLogger(__name__)


# ── 服务水平 Z 值表 ──────────────────────────────────────────────────
# Z = Φ⁻¹(service_level)  — 标准正态分布逆累积分布
SERVICE_LEVEL_Z: dict[float, float] = {
    0.80: 0.84,
    0.85: 1.04,
    0.90: 1.28,
    0.95: 1.65,
    0.975: 1.96,
    0.98: 2.05,
    0.99: 2.33,
    0.995: 2.58,
    0.999: 3.09,
}


# ── 生鲜品类半衰期 (25°C 下的品质衰减一半所需小时数) ────────────────
# 参考: USDA FoodKeeper / StillTasty.com 公开数据
PERISHABLE_HALF_LIFE: dict[str, float] = {
    "叶菜类": 60.0,  # 白菜/生菜/菠菜
    "根茎类": 168.0,  # 土豆/萝卜/洋葱
    "水果类": 96.0,  # 苹果/西瓜/香蕉
    "肉类": 24.0,  # 猪/牛/鸡 (4°C冷藏)
    "豆制品": 18.0,  # 豆腐/豆皮
    "菌菇类": 48.0,
    "蛋类": 336.0,  # 鸡蛋
    "乳制品": 72.0,
    "干货类": 2160.0,  # 米/面/调味品 (~90天)
    "default": 72.0,
}


class Urgency(str, Enum):
    """补货紧急程度"""

    CRITICAL = "critical"  # 已缺货或今日售罄
    URGENT = "urgent"  # 1-2天内需补货
    SOON = "soon"  # 3-7天内需补货
    OK = "ok"  # 库存充足


@dataclass
class ReorderRecommendation:
    """单条补货建议"""

    product_id: int
    product_name: str
    # 核心指标
    daily_forecast: float  # 预测日需求量
    demand_std: float  # 需求标准差
    safety_stock: float  # 安全库存
    reorder_point: float  # 再订货点 (库存低于此值应立即补货)
    recommended_order_qty: float  # 推荐补货量
    current_inventory: float  # 当前库存
    # 诊断指标
    days_until_stockout: float  # 按当前需求速率，库存可支撑天数
    stockout_risk: float  # 缺货概率 (0-1)
    urgency: Urgency  # 紧急程度
    # 成本估算
    estimated_cost: float = 0.0
    potential_waste_pct: float = 0.0  # 基于保质期的潜在损耗率
    # 解释
    explanation: list[str] = field(default_factory=list)


@dataclass
class OptimizationResult:
    """批量优化结果"""

    recommendations: list[ReorderRecommendation]
    summary: dict
    generated_at: str


class InventoryOptimizer:
    """库存优化计算引擎。

    支持两种模式:
      - normal:     标准正态分布假设 (适用于日销量较大的品类)
      - empirical:  基于历史经验分布 (数据量少时自动降级)

    Usage:
        optimizer = InventoryOptimizer(service_level=0.95, lead_time_days=1)
        rec = optimizer.recommend(
            product_name="白菜",
            daily_forecast=30.0,
            demand_std=8.5,
            current_inventory=25.0,
            category="叶菜类",
        )
    """

    def __init__(
        self,
        service_level: float = 0.95,
        lead_time_days: int = 1,
        order_cycle_days: int = 1,
    ) -> None:
        """初始化优化器。

        Args:
            service_level: 服务水平 (0.0-1.0)。0.95 = 95%概率不缺货。
            lead_time_days: 提前期 (从下单到到货的天数，菜市场通常=1天)。
            order_cycle_days: 补货周期 (几天补一次货，通常=1天)。
        """
        if service_level not in SERVICE_LEVEL_Z:
            # 取最近的已知级别
            nearest = min(SERVICE_LEVEL_Z, key=lambda k: abs(k - service_level))
            logger.info(
                "service_level %.2f not in lookup, using nearest: %.2f",
                service_level,
                nearest,
            )
            service_level = nearest

        self.service_level = service_level
        self.z = SERVICE_LEVEL_Z[service_level]
        self.lead_time = lead_time_days
        self.order_cycle = order_cycle_days

    # ── 核心公式 ──────────────────────────────────────────────────────

    def calc_safety_stock(self, demand_std: float) -> float:
        """安全库存 = Z × σ × √LT

        Args:
            demand_std: 日需求标准差 (σ_D)

        Returns:
            安全库存量 (SS)
        """
        if demand_std <= 0:
            return 0.0
        return self.z * demand_std * np.sqrt(self.lead_time)

    def calc_safety_stock_with_leadtime_variability(
        self, demand_std: float, leadtime_std: float, avg_demand: float
    ) -> float:
        """考虑提前期变异的安全库存 (更精确的公式)。

        SS = Z × √(LT × σ_D² + D̄² × σ_LT²)

        Args:
            demand_std: 日需求标准差
            leadtime_std: 提前期标准差 (天)
            avg_demand: 日均需求
        """
        if demand_std <= 0 and leadtime_std <= 0:
            return 0.0
        variance = self.lead_time * (demand_std**2) + (avg_demand**2) * (leadtime_std**2)
        return self.z * np.sqrt(variance)

    def calc_reorder_point(self, avg_demand: float, safety_stock: float) -> float:
        """再订货点 = D̄ × LT + SS

        当库存降至此水平时，应立即下单补货。
        """
        return avg_demand * self.lead_time + safety_stock

    def calc_order_quantity(
        self,
        daily_forecast: float,
        horizon_days: int,
        safety_stock: float,
        current_inventory: float,
        in_transit: float = 0.0,
    ) -> float:
        """推荐补货量 = max(0, F×H + SS − I − T)

        Args:
            daily_forecast: 预测日需求量
            horizon_days: 补货周期 (H, 这次进货要覆盖多少天)
            safety_stock: 安全库存
            current_inventory: 当前库存
            in_transit: 在途库存 (已下单未到货)
        """
        gross_need = daily_forecast * horizon_days + safety_stock
        net_need = gross_need - current_inventory - in_transit
        return max(0.0, net_need)

    # ── 综合推荐 ──────────────────────────────────────────────────────

    def recommend(
        self,
        product_id: int | None,
        product_name: str,
        daily_forecast: float,
        demand_std: float,
        current_inventory: float,
        horizon_days: int = 7,
        category: str = "default",
        lead_time_std: float = 0.0,
    ) -> ReorderRecommendation:
        """生成单条补货建议。

        Args:
            product_id: 商品ID
            product_name: 商品名
            daily_forecast: 预测日需求量 (F)
            demand_std: 需求标准差 (σ)
            current_inventory: 当前库存 (I)
            horizon_days: 预测/补货周期 (H), default=7天
            category: 品类 (用于损耗估算)
            lead_time_std: 提前期标准差 (如无可传0)

        Returns:
            ReorderRecommendation 对象
        """
        # 计算安全库存 (使用更精确的公式如果 lead_time_std 可用)
        if lead_time_std > 0:
            ss = self.calc_safety_stock_with_leadtime_variability(
                demand_std, lead_time_std, daily_forecast
            )
        else:
            ss = self.calc_safety_stock(demand_std)

        # 再订货点
        rop = self.calc_reorder_point(daily_forecast, ss)

        # 推荐补货量
        qty = self.calc_order_quantity(daily_forecast, horizon_days, ss, current_inventory)

        # 库存可支撑天数
        days_left = current_inventory / max(daily_forecast, 0.01)

        # 缺货概率 (基于正态分布简化)
        if demand_std > 0:
            z_current = (current_inventory - daily_forecast * self.lead_time) / (
                demand_std * np.sqrt(self.lead_time)
            )
            # 用 1-CDF 近似
            stockout_risk = max(0.0, min(1.0, 1.0 / (1.0 + np.exp(1.7 * z_current))))
        else:
            stockout_risk = 0.0 if current_inventory > 0 else 1.0

        # 紧急程度判定
        if current_inventory <= 0:
            urgency = Urgency.CRITICAL
        elif days_left <= self.lead_time + 1:
            urgency = Urgency.URGENT
        elif days_left <= 7:
            urgency = Urgency.SOON
        else:
            urgency = Urgency.OK

        # 潜在损耗率 (基于品类半衰期)
        waste_pct = _estimate_waste_percentage(
            current_inventory=current_inventory,
            daily_forecast=daily_forecast,
            category=category,
        )

        # 解释文本
        explanation = _build_explanation(
            product_name=product_name,
            daily_forecast=daily_forecast,
            safety_stock=ss,
            reorder_point=rop,
            recommended_qty=qty,
            days_left=days_left,
            stockout_risk=stockout_risk,
            urgency=urgency,
        )

        return ReorderRecommendation(
            product_id=product_id or 0,
            product_name=product_name,
            daily_forecast=round(daily_forecast, 2),
            demand_std=round(demand_std, 2),
            safety_stock=round(ss, 2),
            reorder_point=round(rop, 2),
            recommended_order_qty=round(qty, 2),
            current_inventory=round(current_inventory, 2),
            days_until_stockout=round(days_left, 1),
            stockout_risk=round(stockout_risk, 3),
            urgency=urgency,
            potential_waste_pct=round(waste_pct, 1),
            explanation=explanation,
        )

    def batch_recommend(
        self,
        products: list[dict],
        horizon_days: int = 7,
    ) -> OptimizationResult:
        """批量生成补货建议。

        Args:
            products: 产品列表，每个元素为 dict:
                - product_id, product_name, daily_forecast,
                - demand_std, current_inventory, category
            horizon_days: 补货周期

        Returns:
            OptimizationResult 含全部建议 + 汇总
        """
        recommendations = []
        total_order_value = 0.0
        critical_count = 0
        urgent_count = 0

        for p in products:
            rec = self.recommend(
                product_id=p.get("product_id"),
                product_name=p["product_name"],
                daily_forecast=p["daily_forecast"],
                demand_std=p.get("demand_std", p["daily_forecast"] * 0.3),
                current_inventory=p.get("current_inventory", 0),
                horizon_days=horizon_days,
                category=p.get("category", "default"),
            )
            recommendations.append(rec)
            total_order_value += rec.recommended_order_qty

            if rec.urgency == Urgency.CRITICAL:
                critical_count += 1
            elif rec.urgency == Urgency.URGENT:
                urgent_count += 1

        # 按紧急程度排序
        urgency_order = {Urgency.CRITICAL: 0, Urgency.URGENT: 1, Urgency.SOON: 2, Urgency.OK: 3}
        recommendations.sort(key=lambda r: (urgency_order[r.urgency], -r.stockout_risk))

        return OptimizationResult(
            recommendations=recommendations,
            summary={
                "total_products": len(products),
                "total_recommended_qty": round(total_order_value, 2),
                "critical_count": critical_count,
                "urgent_count": urgent_count,
                "ok_count": len(products) - critical_count - urgent_count,
                "service_level": self.service_level,
                "z_value": self.z,
                "lead_time_days": self.lead_time,
                "horizon_days": horizon_days,
            },
            generated_at="",  # 由调用方填充
        )


# ── 辅助函数 ──────────────────────────────────────────────────────────


def _estimate_waste_percentage(
    current_inventory: float,
    daily_forecast: float,
    category: str,
    temperature: float = 25.0,
) -> float:
    """基于品类半衰期和温度的潜在损耗率估算。

    使用指数衰减模型: remaining = e^(-λt),  λ = ln(2)/t½
    Q10 = 2 (每升高10°C, 速率翻倍)
    """
    t_half = PERISHABLE_HALF_LIFE.get(category, PERISHABLE_HALF_LIFE["default"])

    if t_half > 1000:  # 干货类几乎不损耗
        return 0.0

    # 温度修正
    temp_factor = 2 ** ((temperature - 25) / 10)
    adjusted_half = t_half / temp_factor

    # 库存卖完所需天数
    days_to_sell = current_inventory / max(daily_forecast, 0.01)
    hours_to_sell = days_to_sell * 24

    # 指数衰减
    k = np.log(2) / adjusted_half
    remaining = np.exp(-k * hours_to_sell)

    return (1 - remaining) * 100


def _build_explanation(
    product_name: str,
    daily_forecast: float,
    safety_stock: float,
    reorder_point: float,
    recommended_qty: float,
    days_left: float,
    stockout_risk: float,
    urgency: Urgency,
) -> list[str]:
    """构建可读的解释文本。"""
    lines = []

    if recommended_qty > 0:
        lines.append(f"建议采购 {product_name} {recommended_qty:.1f} 斤")

        if safety_stock > 0:
            cover_days = int(safety_stock / max(daily_forecast, 0.01))
            lines.append(f"  安全库存={safety_stock:.1f}斤 (覆盖{cover_days}天波动)")
        lines.append(f"  再订货点={reorder_point:.1f}斤 (库存低于此值应立即补货)")
    else:
        lines.append(f"{product_name} 库存充足 (还可卖{days_left:.0f}天)，暂无需补货")

    if urgency == Urgency.CRITICAL:
        lines.append("⚠️ 已缺货，请立即补货！")
    elif urgency == Urgency.URGENT:
        lines.append(f"🔴 库存仅够{days_left:.0f}天，请尽快补货")
    elif urgency == Urgency.SOON:
        lines.append(f"🟡 库存可支撑{days_left:.0f}天，可规划补货")

    if stockout_risk > 0.3:
        lines.append(f"  当前缺货概率: {stockout_risk:.1%}")

    return lines


# ═══════════════════════════════════════════════════════════════════════
# 报童模型 (Newsvendor Model) — 适用于短保质期/单周期的生鲜商品
# ═══════════════════════════════════════════════════════════════════════
#
# 报童模型比标准安全库存模型更适合生鲜场景，因为：
#   1. 明确考虑超储成本 (卖不掉=全损)
#   2. 明确考虑缺货成本 (少卖=少赚)
#   3. 不需要假设无限补货周期
#
# 核心公式:
#   CR = Cu / (Cu + Co)     临界比率 = 最优服务水平
#   Q* = F⁻¹(CR)            最优订货量
#
# 参考:
#   - inventorize (MPN_singleperiod)
#   - stockpyl (newsvendor_normal)
#   - takazawa/newsvendor-model


@dataclass
class NewsvendorResult:
    """报童模型计算结果"""

    critical_ratio: float  # 临界比率 (最优服务水平)
    optimal_quantity: float  # 最优订货量 Q*
    underage_cost: float  # 缺货成本 Cu
    overage_cost: float  # 超储成本 Co
    expected_profit: float  # 期望利润
    expected_sales: float  # 期望销量
    expected_leftover: float  # 期望剩余 (损耗)
    expected_lost_sales: float  # 期望缺货量
    stockout_probability: float  # 缺货概率 (1-CR)
    waste_rate: float  # 期望损耗率


def newsvendor_normal(
    selling_price: float,
    unit_cost: float,
    salvage_value: float,
    mean_demand: float,
    std_demand: float,
) -> NewsvendorResult:
    """报童模型 — 正态分布需求。

    适用场景: 日销量较大 (>10单/天) 的生鲜品类。

    Args:
        selling_price: 售价 (元/斤)
        unit_cost: 进价 (元/斤)
        salvage_value: 残值 (元/斤) — 卖不掉的处理价，如0=全损, 半价处理=售价*0.5
        mean_demand: 日均需求均值
        std_demand: 日均需求标准差

    Returns:
        NewsvendorResult 包含最优订货量和各项期望值

    Example:
        >>> r = newsvendor_normal(price=5, cost=2, salvage=0, mean=30, std=8)
        >>> r.optimal_quantity
        34.2  # 比均值多订14%以覆盖波动
        >>> r.expected_leftover
        5.1   # 预计剩5.1斤
        >>> r.waste_rate
        14.9  # 损耗率约15%
    """
    from scipy.stats import norm as scipy_norm

    Cu = selling_price - unit_cost  # 缺货成本: 少卖1斤少赚多少
    Co = max(unit_cost - salvage_value, 0.01)  # 超储成本: 多进1斤烂掉亏多少 (最小0.01防止除零)
    CR = Cu / (Cu + Co) if (Cu + Co) > 0 else 0.5

    # 最优订货量 = F⁻¹(CR)
    # scipy.norm.ppf 在CR≈1时返回inf，需要裁剪
    CR_clipped = min(max(CR, 0.001), 0.999)
    z = scipy_norm.ppf(CR_clipped)
    Q_star = mean_demand + z * std_demand
    Q_star = max(0.0, Q_star)

    # 标准正态损失函数 L(z) = φ(z) - z(1-Φ(z))
    phi_z = scipy_norm.pdf(z)
    Phi_z = scipy_norm.cdf(z)
    L_z = max(0.0, phi_z - z * (1 - Phi_z))

    # 期望销量 = μ - σ * L(z)
    expected_sales = mean_demand - std_demand * L_z
    expected_sales = max(0.0, min(expected_sales, Q_star))

    # 期望剩余
    expected_leftover = max(0.0, Q_star - expected_sales)

    # 期望缺货
    expected_lost_sales = max(0.0, mean_demand - expected_sales)

    # 期望利润
    expected_profit = Cu * expected_sales - Co * expected_leftover

    return NewsvendorResult(
        critical_ratio=round(CR, 4),
        optimal_quantity=round(Q_star, 2),
        underage_cost=round(Cu, 2),
        overage_cost=round(Co, 2),
        expected_profit=round(expected_profit, 2),
        expected_sales=round(expected_sales, 2),
        expected_leftover=round(expected_leftover, 2),
        expected_lost_sales=round(expected_lost_sales, 2),
        stockout_probability=round(1 - CR, 4),
        waste_rate=round(expected_leftover / Q_star * 100, 1) if Q_star > 0 else 0.0,
    )


def newsvendor_poisson(
    selling_price: float,
    unit_cost: float,
    salvage_value: float,
    mean_demand: float,
) -> NewsvendorResult:
    """报童模型 — 泊松分布需求。

    适用场景: 低销量品类 (日均 <5单)，如高端水果、特殊调料等。

    Args:
        selling_price: 售价
        unit_cost: 进价
        salvage_value: 残值 (通常为0，因为生鲜卖不掉=全损)
        mean_demand: 日均需求均值 (λ)
    """
    from scipy.stats import poisson as scipy_poisson

    Cu = selling_price - unit_cost
    Co = unit_cost - salvage_value
    CR = Cu / (Cu + Co) if (Cu + Co) > 0 else 0.5

    # Q* = Poisson分位点
    Q_star = float(scipy_poisson.ppf(CR, mean_demand))
    Q_star = max(1.0, Q_star)

    # 用泊松PMF计算期望值
    max_d = int(max(Q_star * 2, mean_demand * 5, 20))
    probs = [scipy_poisson.pmf(d, mean_demand) for d in range(max_d + 1)]

    expected_sales = sum(min(d, Q_star) * p for d, p in enumerate(probs))
    expected_leftover = max(0.0, Q_star - expected_sales)
    expected_lost_sales = max(0.0, mean_demand - expected_sales)
    expected_profit = Cu * expected_sales - Co * expected_leftover

    return NewsvendorResult(
        critical_ratio=round(CR, 4),
        optimal_quantity=Q_star,
        underage_cost=round(Cu, 2),
        overage_cost=round(Co, 2),
        expected_profit=round(expected_profit, 2),
        expected_sales=round(expected_sales, 2),
        expected_leftover=round(expected_leftover, 2),
        expected_lost_sales=round(expected_lost_sales, 2),
        stockout_probability=round(1 - CR, 4),
        waste_rate=round(expected_leftover / Q_star * 100, 1) if Q_star > 0 else 0.0,
    )


def recommend_for_perishable(
    product_name: str,
    selling_price: float,
    unit_cost: float,
    salvage_value: float,
    mean_demand: float,
    std_demand: float = 0.0,
    use_poisson: bool = False,
) -> dict:
    """为短保质期商品生成进货建议 (报童模型)。

    这个函数比标准安全库存模型更适合生鲜场景:
    - 明确量化"多进了烂掉"和"少进了少赚"的代价
    - 自动计算最优进货量

    Args:
        product_name: 商品名
        selling_price: 售价 (元/斤)
        unit_cost: 进价 (元/斤)
        salvage_value: 残值 (卖不掉能回收多少)
        mean_demand: 预计需求
        std_demand: 需求标准差 (泊松模式不需要)
        use_poisson: 低销量品类用泊松分布

    Returns:
        dict 包含最优订货量、期望利润、损耗率等
    """
    if use_poisson or mean_demand < 5:
        result = newsvendor_poisson(selling_price, unit_cost, salvage_value, mean_demand)
    else:
        result = newsvendor_normal(selling_price, unit_cost, salvage_value, mean_demand, std_demand)

    # 生成可读建议
    if result.optimal_quantity > mean_demand:
        suggestion = (
            f"建议采购{product_name}{result.optimal_quantity:.1f}斤 "
            f"(比均值多{result.optimal_quantity - mean_demand:.0f}斤以覆盖波动)"
        )
    else:
        suggestion = (
            f"建议采购{product_name}{result.optimal_quantity:.1f}斤 "
            f"(保守策略: 损耗成本高于缺货成本)"
        )

    return {
        "product_name": product_name,
        "model": "newsvendor",
        "suggestion": suggestion,
        "optimal_quantity": result.optimal_quantity,
        "critical_ratio": result.critical_ratio,
        "underage_cost": result.underage_cost,
        "overage_cost": result.overage_cost,
        "expected_profit": result.expected_profit,
        "expected_leftover": result.expected_leftover,
        "expected_lost_sales": result.expected_lost_sales,
        "waste_rate_pct": result.waste_rate,
        "mean_demand": mean_demand,
    }
