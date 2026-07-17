"""Tests for supplier performance scoring system."""
import pytest

from app.services.supplier_scorer import (
    DEFAULT_WEIGHTS,
    DimensionScore,
    LeadTimePrediction,
    RiskLevel,
    SupplierGrade,
    SupplierMetrics,
    SupplierScorecard,
    SupplierScorer,
    quick_evaluate,
)


class TestSupplierMetrics:
    """供应商指标数据类测试。"""

    def test_default_metrics(self):
        """默认指标应该合理。"""
        m = SupplierMetrics(supplier_id="S001", supplier_name="老王")
        assert m.total_orders == 0
        assert m.avg_lead_time_hours == 0
        assert m.flexibility_score == 3.0


class TestSupplierScorer:
    """核心评分引擎测试。"""

    def _make_perfect_metrics(self) -> SupplierMetrics:
        """创建一个完美供应商的指标。"""
        return SupplierMetrics(
            supplier_id="S001",
            supplier_name="金牌供应商",
            total_orders=50,
            on_time_deliveries=49,
            avg_lead_time_hours=12,
            lead_time_std_hours=2,
            promised_vs_actual_ratio=1.0,
            total_qty_ordered=1000,
            accepted_qty=990,
            shortage_qty=5,
            damaged_qty=3,
            rejected_qty=2,
            avg_unit_price=8.0,
            price_vs_market_ratio=0.9,
            price_volatility=0.05,
            response_time_hours=1,
            flexibility_score=5.0,
            communication_score=5.0,
        )

    def _make_bad_metrics(self) -> SupplierMetrics:
        """创建一个糟糕供应商的指标。"""
        return SupplierMetrics(
            supplier_id="S002",
            supplier_name="问题供应商",
            total_orders=10,
            on_time_deliveries=2,
            avg_lead_time_hours=48,
            lead_time_std_hours=24,
            promised_vs_actual_ratio=2.0,
            total_qty_ordered=500,
            accepted_qty=300,
            shortage_qty=100,
            damaged_qty=50,
            rejected_qty=50,
            avg_unit_price=12.0,
            price_vs_market_ratio=1.4,
            price_volatility=0.5,
            response_time_hours=48,
            flexibility_score=1.0,
            communication_score=1.0,
        )

    def _make_empty_metrics(self) -> SupplierMetrics:
        """无历史数据的供应商。"""
        return SupplierMetrics(
            supplier_id="S003",
            supplier_name="新供应商",
        )

    # ── 单个评估 ──────────────────────────────────────────────

    def test_perfect_supplier_high_score(self):
        """完美供应商应该得高分。"""
        scorer = SupplierScorer()
        card = scorer.evaluate(self._make_perfect_metrics())
        assert card.composite_score >= 80
        assert card.grade in (SupplierGrade.A, SupplierGrade.B)
        assert card.risk_level == RiskLevel.LOW

    def test_bad_supplier_low_score(self):
        """糟糕供应商应该得低分。"""
        scorer = SupplierScorer()
        card = scorer.evaluate(self._make_bad_metrics())
        assert card.composite_score < 60
        assert card.grade in (SupplierGrade.D, SupplierGrade.F)
        assert card.risk_level in (RiskLevel.HIGH, RiskLevel.CRITICAL)

    def test_empty_supplier_neutral_score(self):
        """无数据的供应商给中性分。"""
        scorer = SupplierScorer()
        card = scorer.evaluate(self._make_empty_metrics())
        assert 40 <= card.composite_score <= 75
        assert "数据较少" in card.recommendations[0] or "历史数据" in str(card.recommendations)

    def test_scorecard_has_all_dimensions(self):
        """评分卡包含所有5个维度。"""
        scorer = SupplierScorer()
        card = scorer.evaluate(self._make_perfect_metrics())
        dim_names = {d.name for d in card.dimensions}
        assert dim_names == {"质量", "交期", "价格", "稳定性", "服务"}

    def test_scorecard_has_strengths_and_weaknesses(self):
        """评分卡包含强弱项。"""
        scorer = SupplierScorer()
        card = scorer.evaluate(self._make_perfect_metrics())
        assert card.strengths  # 至少有一个强项
        # 完美供应商不应该有弱项, 但不强制

    def test_scorecard_has_recommendations(self):
        """评分卡包含建议。"""
        scorer = SupplierScorer()
        card = scorer.evaluate(self._make_bad_metrics())
        assert card.recommendations

    def test_lead_time_prediction_present(self):
        """评分卡包含提前期预测。"""
        scorer = SupplierScorer()
        card = scorer.evaluate(self._make_perfect_metrics())
        pred = card.lead_time_prediction
        assert "expected_hours" in pred
        assert "safety_buffer_hours" in pred
        assert "worst_case_hours" in pred
        assert pred["expected_hours"] > 0

    # ── 质量维度 ──────────────────────────────────────────────

    def test_quality_perfect_acceptance(self):
        """100%合格率得满分质量。"""
        scorer = SupplierScorer()
        m = SupplierMetrics(
            supplier_id="S", supplier_name="Q",
            total_qty_ordered=100, accepted_qty=100,
            shortage_qty=0, damaged_qty=0, rejected_qty=0,
        )
        dim = scorer._score_quality(m)
        assert dim.raw_score >= 45  # 接近50满分

    def test_quality_penalty_for_shortage(self):
        """缺斤扣分。"""
        scorer = SupplierScorer()
        m = SupplierMetrics(
            supplier_id="S", supplier_name="Q",
            total_qty_ordered=100, accepted_qty=80,
            shortage_qty=20, damaged_qty=0, rejected_qty=0,
        )
        dim = scorer._score_quality(m)
        assert dim.raw_score < 50  # 扣分了

    def test_quality_no_data_neutral(self):
        """无质量数据给中性分。"""
        scorer = SupplierScorer()
        m = SupplierMetrics(supplier_id="S", supplier_name="Q")
        dim = scorer._score_quality(m)
        assert dim.raw_score == 60.0

    # ── 交期维度 ──────────────────────────────────────────────

    def test_delivery_perfect_on_time(self):
        """完美准时得满分。"""
        scorer = SupplierScorer()
        m = SupplierMetrics(
            supplier_id="S", supplier_name="D",
            total_orders=20, on_time_deliveries=20,
            avg_lead_time_hours=24, lead_time_std_hours=2,
            promised_vs_actual_ratio=1.0,
        )
        dim = scorer._score_delivery(m)
        assert dim.raw_score >= 80

    def test_delivery_poor_on_time(self):
        """不准时得分低。"""
        scorer = SupplierScorer()
        m = SupplierMetrics(
            supplier_id="S", supplier_name="D",
            total_orders=20, on_time_deliveries=2,
            avg_lead_time_hours=72, lead_time_std_hours=48,
            promised_vs_actual_ratio=2.5,
        )
        dim = scorer._score_delivery(m)
        assert dim.raw_score < 40

    # ── 价格维度 ──────────────────────────────────────────────

    def test_price_below_market_good(self):
        """低于市场价得分高。"""
        scorer = SupplierScorer()
        m = SupplierMetrics(
            supplier_id="S", supplier_name="P",
            avg_unit_price=8.0, price_vs_market_ratio=0.8,
            price_volatility=0.05,
        )
        dim = scorer._score_price(m)
        assert dim.raw_score >= 80

    def test_price_above_market_poor(self):
        """远高于市场价得分低。"""
        scorer = SupplierScorer()
        m = SupplierMetrics(
            supplier_id="S", supplier_name="P",
            avg_unit_price=15.0, price_vs_market_ratio=1.5,
            price_volatility=0.3,
        )
        dim = scorer._score_price(m)
        assert dim.raw_score < 40

    # ── 批量评估 ──────────────────────────────────────────────

    def test_batch_evaluate_sets_percentiles(self):
        """批量评估设置百分位排名。"""
        scorer = SupplierScorer()
        metrics = [self._make_perfect_metrics(), self._make_bad_metrics()]
        cards = scorer.batch_evaluate(metrics)
        assert len(cards) == 2
        # 更好的供应商应该有更高或相等百分位
        best = max(cards, key=lambda c: c.composite_score)
        assert best.comparison_percentile >= 50

    def test_batch_single_supplier(self):
        """单个供应商百分位为50。"""
        scorer = SupplierScorer()
        cards = scorer.batch_evaluate([self._make_perfect_metrics()])
        assert cards[0].comparison_percentile == 50.0

    # ── 供应商对比 ────────────────────────────────────────────

    def test_compare_returns_rankings(self):
        """对比分析返回排名。"""
        scorer = SupplierScorer()
        cards = scorer.batch_evaluate([
            self._make_perfect_metrics(),
            self._make_bad_metrics(),
            self._make_empty_metrics(),
        ])
        result = scorer.compare(cards)
        assert "rankings" in result
        assert result["rankings"][0]["name"] == "金牌供应商"
        assert result["rankings"][-1]["name"] == "问题供应商"

    def test_compare_returns_best_by_dimension(self):
        """对比分析返回各维度最佳。"""
        scorer = SupplierScorer()
        cards = scorer.batch_evaluate([
            self._make_perfect_metrics(),
            self._make_bad_metrics(),
        ])
        result = scorer.compare(cards)
        assert "best_by_dimension" in result
        assert len(result["best_by_dimension"]) == 5

    def test_compare_empty_returns_error(self):
        """空列表返回错误。"""
        scorer = SupplierScorer()
        result = scorer.compare([])
        assert "error" in result

    # ── 提前期预测 ────────────────────────────────────────────

    def test_lead_time_prediction_empty_data(self):
        """无数据时保守估计。"""
        scorer = SupplierScorer()
        m = SupplierMetrics(supplier_id="S", supplier_name="L")
        pred = scorer._predict_lead_time(m)
        assert pred.expected_hours > 0
        assert pred.safety_buffer_hours > 0
        assert pred.confidence < 0.5  # 数据少 = 置信度低

    def test_lead_time_confidence_increases_with_data(self):
        """数据越多置信度越高。"""
        scorer = SupplierScorer()
        few = SupplierMetrics(
            supplier_id="S", supplier_name="L",
            total_orders=3, avg_lead_time_hours=12,
            lead_time_std_hours=4,
        )
        many = SupplierMetrics(
            supplier_id="S", supplier_name="L",
            total_orders=30, avg_lead_time_hours=12,
            lead_time_std_hours=4,
        )
        p_few = scorer._predict_lead_time(few)
        p_many = scorer._predict_lead_time(many)
        assert p_many.confidence > p_few.confidence

    # ── 等级判定 ──────────────────────────────────────────────

    def test_grade_thresholds(self):
        """等级阈值正确。"""
        scorer = SupplierScorer()
        assert scorer._grade(95) == SupplierGrade.A
        assert scorer._grade(80) == SupplierGrade.B
        assert scorer._grade(70) == SupplierGrade.C
        assert scorer._grade(50) == SupplierGrade.D
        assert scorer._grade(20) == SupplierGrade.F

    # ── 风险判定 ──────────────────────────────────────────────

    def test_risk_critical_for_very_low_score(self):
        """极低分 = CRITICAL风险。"""
        scorer = SupplierScorer()
        m = SupplierMetrics(supplier_id="S", supplier_name="R")
        risk = scorer._risk_level(20, m)
        assert risk == RiskLevel.CRITICAL

    def test_risk_critical_for_high_shortage(self):
        """高缺斤率 = CRITICAL风险。"""
        scorer = SupplierScorer()
        m = SupplierMetrics(
            supplier_id="S", supplier_name="R",
            total_qty_ordered=100, shortage_qty=25,  # 25%缺斤
        )
        risk = scorer._risk_level(70, m)  # 虽然评分还行
        assert risk == RiskLevel.CRITICAL  # 但缺斤严重

    # ── 自定义权重 ────────────────────────────────────────────

    def test_custom_weights(self):
        """自定义权重影响评分。"""
        default = SupplierScorer()
        price_focused = SupplierScorer(
            weights={"quality": 0.1, "delivery": 0.1, "price": 0.5, "stability": 0.15, "service": 0.15}
        )
        m = self._make_bad_metrics()
        card_default = default.evaluate(m)
        card_price = price_focused.evaluate(m)
        # 价格维度权重更高 → 低价格评分影响更大
        assert card_price.composite_score != card_default.composite_score

    def test_weights_auto_normalize(self):
        """权重自动归一化。"""
        scorer = SupplierScorer(
            weights={"quality": 5, "delivery": 5, "price": 5, "stability": 5, "service": 5}
        )
        total = sum(scorer.weights.values())
        assert abs(total - 1.0) < 0.001


class TestConvenienceFunctions:
    """便捷函数测试。"""

    def test_quick_evaluate_returns_scorecard(self):
        """快速评估返回完整的评分卡。"""
        card = quick_evaluate(
            supplier_id="S001",
            supplier_name="老王",
            total_orders=20,
            on_time_deliveries=18,
            avg_lead_time_hours=24,
            total_qty_ordered=500,
            accepted_qty=480,
            shortage_qty=10,
            avg_unit_price=10.0,
            price_vs_market_ratio=0.95,
        )
        assert isinstance(card, SupplierScorecard)
        assert card.composite_score > 0
        assert card.grade is not None
        assert len(card.dimensions) == 5

    def test_quick_evaluate_minimal_input(self):
        """最少参数也能调用。"""
        card = quick_evaluate(supplier_id="S", supplier_name="新")
        assert card.composite_score > 0
        assert len(card.recommendations) > 0
