"""供应商绩效评分系统 — 基于多维度加权打分与聚类分级。

参考项目:
  - ghazalna/Supplier-Performance-Evaluation-Clustering:
    https://github.com/ghazalna/Supplier-Performance-Evaluation-Clustering
    → K-Means聚类: 按提前期/缺货率/履约时间将供应商分为3组
  - vendor_leadtime_scorecard (Odoo):
    → 0-100评分 + A-F等级 + 自动标记超期未到货
  - asgard-ai-platform/skills:
    → QCDS框架 (Quality/Cost/Delivery/Service) 加权打分
  - Dual-Band Lead Time Predictor (HuggingFace):
    → 两个GBR模型: Model A预测均值, Model B预测95分位最坏情况
  - CPOS论文 (Computers & Industrial Engineering 2024):
    → DT/RF/XGBoost预测PO是否延迟 + 数学规划优化供应商选择

核心设计:
  - 5维度加权打分: 质量(Q) 25% + 交期(D) 25% + 价格(C) 20% + 稳定性(S) 15% + 服务(V) 15%
  - 基于历史采购数据的自动评分
  - A/B/C/D/F 等级分类
  - 供应商对比与推荐
  - 提前期预测 (统计方法: 历史均值 + 安全缓冲)
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from typing import Optional


# ── 等级枚举 ──────────────────────────────────────────────────────
class SupplierGrade(str, Enum):
    A = "A"  # 优秀: 90-100
    B = "B"  # 良好: 75-89
    C = "C"  # 合格: 60-74
    D = "D"  # 待改进: 40-59
    F = "F"  # 不合格: 0-39


class RiskLevel(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


# ── 数据类 ────────────────────────────────────────────────────────
@dataclass
class SupplierMetrics:
    """供应商绩效原始指标 — 从历史采购数据聚合。"""
    supplier_id: str
    supplier_name: str

    # 交期指标
    total_orders: int = 0
    on_time_deliveries: int = 0
    avg_lead_time_hours: float = 0          # 平均提前期
    lead_time_std_hours: float = 0          # 提前期标准差
    promised_vs_actual_ratio: float = 1.0   # 承诺/实际 比值 (>1逾期)

    # 质量指标
    total_qty_ordered: float = 0
    accepted_qty: float = 0                 # 合格品
    shortage_qty: float = 0                 # 缺斤
    damaged_qty: float = 0                  # 破损
    rejected_qty: float = 0                 # 拒收

    # 价格指标
    avg_unit_price: float = 0
    price_vs_market_ratio: float = 1.0      # vs 市场均价
    price_volatility: float = 0             # 价格波动 (变异系数)

    # 服务指标
    response_time_hours: float = 24         # 响应时间(小时)
    flexibility_score: float = 3.0          # 灵活度 1-5 (最小起订量/临时加单)
    communication_score: float = 3.0        # 沟通 1-5

    # 时间范围
    first_order_date: str | None = None
    last_order_date: str | None = None


@dataclass
class DimensionScore:
    """单维度评分详情。"""
    name: str
    raw_score: float       # 0-100
    weight: float          # 权重
    weighted_score: float  # raw_score * weight
    details: dict = field(default_factory=dict)


@dataclass
class SupplierScorecard:
    """供应商综合评分卡。"""
    supplier_id: str
    supplier_name: str
    composite_score: float         # 0-100
    grade: SupplierGrade
    risk_level: RiskLevel
    dimensions: list[DimensionScore]
    strengths: list[str]
    weaknesses: list[str]
    recommendations: list[str]
    lead_time_prediction: dict     # 提前期预测
    comparison_percentile: float   # 在所有供应商中的百分位
    updated_at: str


@dataclass
class LeadTimePrediction:
    """提前期预测 — Dual-Band模式。"""
    expected_hours: float       # Model A: 最可能值
    safety_buffer_hours: float  # Model B: 95分位
    worst_case_hours: float     # 最坏情况 = expected + safety_buffer
    confidence: float           # 预测置信度 (0-1)


# ── 评分权重配置 ──────────────────────────────────────────────────
DEFAULT_WEIGHTS = {
    "quality": 0.25,     # 质量: 合格率/缺斤率/退货率
    "delivery": 0.25,    # 交期: 准时率/提前期稳定性
    "price": 0.20,       # 价格: 价格水平/价格波动
    "stability": 0.15,   # 稳定: 供应连续性/历史波动
    "service": 0.15,     # 服务: 响应速度/灵活度/沟通
}


# ── 核心评分引擎 ──────────────────────────────────────────────────
class SupplierScorer:
    """供应商评分引擎。

    使用方式:
        scorer = SupplierScorer()
        card = scorer.evaluate(metrics)
    """

    def __init__(self, weights: dict[str, float] | None = None):
        self.weights = weights or DEFAULT_WEIGHTS.copy()
        # 归一化确保权重和为1
        total = sum(self.weights.values())
        if abs(total - 1.0) > 0.001:
            self.weights = {k: v / total for k, v in self.weights.items()}

    # ── 主入口 ──────────────────────────────────────────────────
    def evaluate(self, m: SupplierMetrics) -> SupplierScorecard:
        """全面评估一个供应商。"""
        dims = [
            self._score_quality(m),
            self._score_delivery(m),
            self._score_price(m),
            self._score_stability(m),
            self._score_service(m),
        ]

        composite = sum(d.weighted_score for d in dims)
        grade = self._grade(composite)
        risk = self._risk_level(composite, m)

        strengths, weaknesses = self._analyze(dims)

        lt_pred = self._predict_lead_time(m)

        return SupplierScorecard(
            supplier_id=m.supplier_id,
            supplier_name=m.supplier_name,
            composite_score=round(composite, 1),
            grade=grade,
            risk_level=risk,
            dimensions=dims,
            strengths=strengths,
            weaknesses=weaknesses,
            recommendations=self._recommendations(dims, grade, risk, m),
            lead_time_prediction={
                "expected_hours": round(lt_pred.expected_hours, 1),
                "safety_buffer_hours": round(lt_pred.safety_buffer_hours, 1),
                "worst_case_hours": round(lt_pred.worst_case_hours, 1),
                "confidence": round(lt_pred.confidence, 2),
            },
            comparison_percentile=50.0,  # 需在batch_evaluate中更新
            updated_at=datetime.now().isoformat(),
        )

    # ── 各维度评分 ──────────────────────────────────────────────

    def _score_quality(self, m: SupplierMetrics) -> DimensionScore:
        """质量维度: 25% 权重。

        考察: 合格率 / 缺斤率 / 破损率 / 退货率
        满分100, 各项扣分。
        """
        if m.total_qty_ordered <= 0:
            # 无历史数据, 给中性分
            return DimensionScore(
                name="质量",
                raw_score=60.0,
                weight=self.weights["quality"],
                weighted_score=60.0 * self.weights["quality"],
                details={"note": "无历史数据, 默认中性评分"},
            )

        # 合格率 (最大50分)
        acceptance_rate = m.accepted_qty / max(m.total_qty_ordered, 0.01)
        acceptance_score = min(50.0, acceptance_rate * 50.0)

        # 缺斤率 (扣分, 最多扣20分)
        shortage_rate = m.shortage_qty / max(m.total_qty_ordered, 0.01)
        shortage_penalty = min(20.0, shortage_rate * 100.0)

        # 破损率 (扣分, 最多扣15分)
        damaged_rate = m.damaged_qty / max(m.total_qty_ordered, 0.01)
        damaged_penalty = min(15.0, damaged_rate * 75.0)

        # 拒收率 (扣分, 最多扣15分)
        rejected_rate = m.rejected_qty / max(m.total_qty_ordered, 0.01)
        rejected_penalty = min(15.0, rejected_rate * 75.0)

        raw = max(0.0, acceptance_score - shortage_penalty - damaged_penalty - rejected_penalty)

        return DimensionScore(
            name="质量",
            raw_score=round(raw, 1),
            weight=self.weights["quality"],
            weighted_score=round(raw * self.weights["quality"], 1),
            details={
                "合格率": f"{round(acceptance_rate * 100, 1)}%",
                "缺斤率": f"{round(shortage_rate * 100, 1)}%",
                "破损率": f"{round(damaged_rate * 100, 1)}%",
                "拒收率": f"{round(rejected_rate * 100, 1)}%",
            },
        )

    def _score_delivery(self, m: SupplierMetrics) -> DimensionScore:
        """交期维度: 25% 权重。

        考察: 准时率 / 提前期均值 / 提前期稳定性 / 承诺偏差
        """
        if m.total_orders <= 0:
            return DimensionScore(
                name="交期",
                raw_score=60.0,
                weight=self.weights["delivery"],
                weighted_score=60.0 * self.weights["delivery"],
                details={"note": "无历史订单, 默认中性评分"},
            )

        # 准时率 (最大40分)
        on_time_rate = m.on_time_deliveries / m.total_orders
        on_time_score = on_time_rate * 40.0

        # 提前期稳定性 (最大30分) — 用变异系数
        if m.avg_lead_time_hours > 0:
            cv = m.lead_time_std_hours / m.avg_lead_time_hours
            # cv < 0.2: 很稳定 (30分), cv > 1.0: 很不稳定 (0分)
            stability_score = max(0.0, 30.0 * (1.0 - min(cv, 1.0)))
        else:
            stability_score = 30.0

        # 承诺偏差 (最大30分)
        if m.promised_vs_actual_ratio > 0:
            # ratio=1.0 → 30分, ratio=2.0 → 0分
            deviation = abs(1.0 - m.promised_vs_actual_ratio)
            promise_score = max(0.0, 30.0 * (1.0 - min(deviation, 1.0)))
        else:
            promise_score = 30.0

        raw = on_time_score + stability_score + promise_score
        return DimensionScore(
            name="交期",
            raw_score=round(raw, 1),
            weight=self.weights["delivery"],
            weighted_score=round(raw * self.weights["delivery"], 1),
            details={
                "准时率": f"{round(on_time_rate * 100, 1)}%",
                "平均提前期": f"{round(m.avg_lead_time_hours, 1)}小时",
                "提前期稳定性(CV)": f"{round(m.lead_time_std_hours / max(m.avg_lead_time_hours, 0.01), 2)}",
                "承诺/实际比": f"{round(m.promised_vs_actual_ratio, 2)}",
                "历史订单数": str(m.total_orders),
            },
        )

    def _score_price(self, m: SupplierMetrics) -> DimensionScore:
        """价格维度: 20% 权重。

        考察: 价格竞争力 / 价格稳定性
        """
        if m.avg_unit_price <= 0:
            return DimensionScore(
                name="价格",
                raw_score=60.0,
                weight=self.weights["price"],
                weighted_score=60.0 * self.weights["price"],
                details={"note": "无价格数据, 默认中性评分"},
            )

        # 价格竞争力 (最大60分) — vs 市场均价
        if m.price_vs_market_ratio > 0:
            if m.price_vs_market_ratio <= 0.85:
                price_score = 60.0  # 低于市场价15%+
            elif m.price_vs_market_ratio <= 0.95:
                price_score = 50.0  # 略低于市场
            elif m.price_vs_market_ratio <= 1.05:
                price_score = 40.0  # 接近市场
            elif m.price_vs_market_ratio <= 1.15:
                price_score = 25.0  # 略高于市场
            elif m.price_vs_market_ratio <= 1.30:
                price_score = 10.0  # 明显高于市场
            else:
                price_score = 0.0   # 远高于市场
        else:
            price_score = 30.0

        # 价格稳定性 (最大40分) — 波动越小越好
        if m.avg_unit_price > 0:
            volatility = m.price_volatility  # 变异系数
            stability_score = max(0.0, 40.0 * (1.0 - min(volatility, 1.0)))
        else:
            stability_score = 40.0

        raw = price_score + stability_score
        return DimensionScore(
            name="价格",
            raw_score=round(raw, 1),
            weight=self.weights["price"],
            weighted_score=round(raw * self.weights["price"], 1),
            details={
                "均价": f"¥{round(m.avg_unit_price, 2)}",
                "vs市场": f"{round(m.price_vs_market_ratio * 100, 1)}%",
                "价格波动(CV)": f"{round(m.price_volatility, 2)}",
            },
        )

    def _score_stability(self, m: SupplierMetrics) -> DimensionScore:
        """稳定性维度: 15% 权重。

        考察: 订单持续性 / 供应断裂次数 / 履约率波动
        """
        if m.total_orders <= 0:
            return DimensionScore(
                name="稳定性",
                raw_score=60.0,
                weight=self.weights["stability"],
                weighted_score=60.0 * self.weights["stability"],
                details={"note": "无历史数据, 默认中性评分"},
            )

        # 订单规模: 太少订单 → 数据不可靠
        if m.total_orders >= 20:
            scale_score = 30.0
        elif m.total_orders >= 10:
            scale_score = 20.0
        elif m.total_orders >= 5:
            scale_score = 10.0
        else:
            scale_score = 5.0

        # 品质一致性: 合格率的标准差相关 (简化: 用缺斤率波动)
        # 这里用 退货+拒收 的总比例作为不一致信号
        inconsistency = (
            (m.shortage_qty + m.damaged_qty + m.rejected_qty)
            / max(m.total_qty_ordered, 0.01)
        )
        consistency_score = max(0.0, 40.0 * (1.0 - min(inconsistency * 2.0, 1.0)))

        # 时间跨度分 (老供应商更可信)
        # 简化: 基于订单数给分
        longevity_score = min(30.0, m.total_orders * 1.5)

        raw = scale_score + consistency_score + longevity_score
        return DimensionScore(
            name="稳定性",
            raw_score=round(min(raw, 100.0), 1),
            weight=self.weights["stability"],
            weighted_score=round(min(raw, 100.0) * self.weights["stability"], 1),
            details={
                "历史订单": str(m.total_orders),
                "不一致率": f"{round(inconsistency * 100, 1)}%",
                "数据可信度": "高" if m.total_orders >= 10 else ("中" if m.total_orders >= 5 else "低"),
            },
        )

    def _score_service(self, m: SupplierMetrics) -> DimensionScore:
        """服务维度: 15% 权重。

        考察: 响应速度 / 灵活度 / 沟通质量
        """
        # 响应速度 (最大40分)
        if m.response_time_hours <= 2:
            resp_score = 40.0
        elif m.response_time_hours <= 6:
            resp_score = 32.0
        elif m.response_time_hours <= 12:
            resp_score = 24.0
        elif m.response_time_hours <= 24:
            resp_score = 16.0
        else:
            resp_score = 5.0

        # 灵活度 (最大30分) — 1-5 映射到 0-30
        flex_score = (m.flexibility_score - 1) / 4.0 * 30.0

        # 沟通质量 (最大30分)
        comm_score = (m.communication_score - 1) / 4.0 * 30.0

        raw = resp_score + flex_score + comm_score
        return DimensionScore(
            name="服务",
            raw_score=round(raw, 1),
            weight=self.weights["service"],
            weighted_score=round(raw * self.weights["service"], 1),
            details={
                "响应时间": f"{round(m.response_time_hours, 1)}小时",
                "灵活度": f"{m.flexibility_score}/5",
                "沟通": f"{m.communication_score}/5",
            },
        )

    # ── 等级与风险 ──────────────────────────────────────────────

    def _grade(self, score: float) -> SupplierGrade:
        if score >= 90:
            return SupplierGrade.A
        elif score >= 75:
            return SupplierGrade.B
        elif score >= 60:
            return SupplierGrade.C
        elif score >= 40:
            return SupplierGrade.D
        return SupplierGrade.F

    def _risk_level(self, score: float, m: SupplierMetrics) -> RiskLevel:
        """综合评分 + 额外风险信号。"""
        # 评分 < 30 → 高危
        if score < 30:
            return RiskLevel.CRITICAL

        # 缺斤率极高 (>20%)
        if m.total_qty_ordered > 0:
            shortage = m.shortage_qty / m.total_qty_ordered
            if shortage > 0.20:
                return RiskLevel.CRITICAL

        # 准时率极低 (<30%) 且有足够订单
        if m.total_orders >= 5:
            otr = m.on_time_deliveries / m.total_orders
            if otr < 0.30:
                return RiskLevel.HIGH

        if score < 50:
            return RiskLevel.HIGH
        if score < 70:
            return RiskLevel.MEDIUM
        return RiskLevel.LOW

    # ── 强弱项分析 ──────────────────────────────────────────────

    def _analyze(
        self, dims: list[DimensionScore]
    ) -> tuple[list[str], list[str]]:
        """分析优势与劣势维度。"""
        strengths = []
        weaknesses = []

        for d in sorted(dims, key=lambda x: x.raw_score, reverse=True):
            if d.raw_score >= 80:
                strengths.append(f"{d.name}({round(d.raw_score)}分)")
            elif d.raw_score < 50:
                weaknesses.append(f"{d.name}({round(d.raw_score)}分)")

        if not strengths and dims:
            best = max(dims, key=lambda x: x.raw_score)
            strengths.append(f"{best.name}({round(best.raw_score)}分)")

        if not weaknesses and dims:
            worst = min(dims, key=lambda x: x.raw_score)
            if worst.raw_score < 60:
                weaknesses.append(f"{worst.name}({round(worst.raw_score)}分)")

        return strengths, weaknesses

    # ── 建议生成 ────────────────────────────────────────────────

    def _recommendations(
        self,
        dims: list[DimensionScore],
        grade: SupplierGrade,
        risk: RiskLevel,
        m: SupplierMetrics,
    ) -> list[str]:
        recs = []

        # 基于风险等级
        if risk == RiskLevel.CRITICAL:
            recs.append("⚠️ 高风险供应商 — 建议寻找替代供应商并逐步减少依赖")
        elif risk == RiskLevel.HIGH:
            recs.append("建议增加对该供应商的质量抽查频次")

        # 基于等级
        if grade in (SupplierGrade.D, SupplierGrade.F):
            recs.append("考虑将该供应商列入观察名单")
        elif grade == SupplierGrade.A:
            recs.append("可作为首选供应商, 考虑签订长期合作")

        # 基于具体维度
        for d in dims:
            if d.name == "质量" and d.raw_score < 50:
                recs.append(f"质量评分偏低({round(d.raw_score)}分), 建议每次到货严格验收")
            elif d.name == "交期" and d.raw_score < 50:
                recs.append(f"交期不稳定({round(d.raw_score)}分), 建议预留更长的安全提前期")
            elif d.name == "价格" and d.raw_score < 40:
                recs.append(f"价格偏高({round(d.raw_score)}分), 建议多询价比较")
            elif d.name == "稳定性" and d.raw_score < 50:
                recs.append("数据不足或供应不稳, 建议备选供应商")

        # 数据不足
        if m.total_orders < 3:
            recs.append("历史数据较少, 评分仅供参考, 建议积累更多数据后重新评估")

        return recs if recs else ["供应商表现正常, 保持现有合作方式"]

    # ── 提前期预测 (Dual-Band模式) ─────────────────────────────

    def _predict_lead_time(self, m: SupplierMetrics) -> LeadTimePrediction:
        """双波段提前期预测。

        Model A: 期望均值 (最可能到达时间)
        Model B: 安全缓冲 (95分位的额外buffer)
        """
        if m.total_orders <= 0:
            return LeadTimePrediction(
                expected_hours=24.0,
                safety_buffer_hours=12.0,
                worst_case_hours=36.0,
                confidence=0.20,
            )

        # Model A: 历史均值
        expected = m.avg_lead_time_hours

        # Model B: 95分位 = mean + 1.645 * std (正态假设)
        # 但为了保守, 用 2 * std
        buffer = 2.0 * m.lead_time_std_hours if m.lead_time_std_hours > 0 else expected * 0.5

        worst = expected + buffer

        # 置信度取决于数据量
        if m.total_orders >= 20:
            confidence = 0.85
        elif m.total_orders >= 10:
            confidence = 0.70
        elif m.total_orders >= 5:
            confidence = 0.50
        else:
            confidence = 0.30

        return LeadTimePrediction(
            expected_hours=round(expected, 1),
            safety_buffer_hours=round(buffer, 1),
            worst_case_hours=round(worst, 1),
            confidence=confidence,
        )

    # ── 批量评估 ────────────────────────────────────────────────

    def batch_evaluate(
        self, metrics_list: list[SupplierMetrics]
    ) -> list[SupplierScorecard]:
        """批量评估 — 自动计算百分位排名。"""
        cards = [self.evaluate(m) for m in metrics_list]

        if len(cards) <= 1:
            for c in cards:
                c.comparison_percentile = 50.0
            return cards

        # 排序并计算百分位
        scores = sorted([c.composite_score for c in cards])
        n = len(scores)

        for card in cards:
            # 百分位 = (低于当前分数的供应商数) / 总数
            below = sum(1 for s in scores if s < card.composite_score)
            card.comparison_percentile = round(below / n * 100, 1)

        return cards

    # ── 供应商对比 ──────────────────────────────────────────────

    def compare(
        self, cards: list[SupplierScorecard]
    ) -> dict:
        """多供应商对比分析。"""
        if not cards:
            return {"error": "无供应商数据"}

        best = max(cards, key=lambda c: c.composite_score)
        worst = min(cards, key=lambda c: c.composite_score)

        # 按维度找最佳
        dim_bests = {}
        dim_names = ["质量", "交期", "价格", "稳定性", "服务"]

        for dn in dim_names:
            best_dim = None
            best_score = -1
            for c in cards:
                for d in c.dimensions:
                    if d.name == dn and d.raw_score > best_score:
                        best_score = d.raw_score
                        best_dim = c.supplier_name
            if best_dim:
                dim_bests[dn] = {"supplier": best_dim, "score": round(best_score, 1)}

        return {
            "supplier_count": len(cards),
            "avg_score": round(sum(c.composite_score for c in cards) / len(cards), 1),
            "best_overall": {
                "name": best.supplier_name,
                "score": best.composite_score,
                "grade": best.grade.value,
            },
            "worst_overall": {
                "name": worst.supplier_name,
                "score": worst.composite_score,
                "grade": worst.grade.value,
            },
            "best_by_dimension": dim_bests,
            "rankings": [
                {
                    "rank": i + 1,
                    "name": c.supplier_name,
                    "score": c.composite_score,
                    "grade": c.grade.value,
                    "risk": c.risk_level.value,
                }
                for i, c in enumerate(
                    sorted(cards, key=lambda x: x.composite_score, reverse=True)
                )
            ],
        }


# ── 便捷函数 ──────────────────────────────────────────────────────
def quick_evaluate(
    supplier_id: str,
    supplier_name: str,
    total_orders: int = 0,
    on_time_deliveries: int = 0,
    avg_lead_time_hours: float = 24,
    lead_time_std_hours: float = 8,
    total_qty_ordered: float = 0,
    accepted_qty: float = 0,
    shortage_qty: float = 0,
    damaged_qty: float = 0,
    rejected_qty: float = 0,
    avg_unit_price: float = 0,
    price_vs_market_ratio: float = 1.0,
    price_volatility: float = 0.1,
    response_time_hours: float = 24,
    flexibility_score: float = 3.0,
    communication_score: float = 3.0,
) -> SupplierScorecard:
    """快速评估 — 传入关键指标即可。"""
    m = SupplierMetrics(
        supplier_id=supplier_id,
        supplier_name=supplier_name,
        total_orders=total_orders,
        on_time_deliveries=on_time_deliveries,
        avg_lead_time_hours=avg_lead_time_hours,
        lead_time_std_hours=lead_time_std_hours,
        total_qty_ordered=total_qty_ordered,
        accepted_qty=accepted_qty,
        shortage_qty=shortage_qty,
        damaged_qty=damaged_qty,
        rejected_qty=rejected_qty,
        avg_unit_price=avg_unit_price,
        price_vs_market_ratio=price_vs_market_ratio,
        price_volatility=price_volatility,
        response_time_hours=response_time_hours,
        flexibility_score=flexibility_score,
        communication_score=communication_score,
    )
    scorer = SupplierScorer()
    return scorer.evaluate(m)
