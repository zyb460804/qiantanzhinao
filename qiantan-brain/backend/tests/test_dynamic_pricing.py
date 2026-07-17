"""Tests for dynamic pricing engine."""
import math

import pytest

from app.services.dynamic_pricing import (
    CATEGORY_SHELF_LIFE,
    DEFAULT_MARKDOWN_STEPS,
    DynamicPricingEngine,
    MarkdownStrategy,
    PriceTier,
    PricingContext,
    PricingRecommendation,
    MarkdownStep,
    estimate_demand_uplift,
    quality_factor,
    recommend_price,
    temp_correction,
)


class TestTempCorrection:
    """Q10温度修正测试。"""

    def test_base_temperature_no_correction(self):
        """基准温度20°C不修正。"""
        assert temp_correction(20.0, 20.0) == 1.0

    def test_hotter_faster_spoilage(self):
        """温度每升高10°C, 变质速率翻倍。"""
        assert temp_correction(30.0, 20.0) == pytest.approx(2.0)
        assert temp_correction(40.0, 20.0) == pytest.approx(4.0)

    def test_colder_slower_spoilage(self):
        """温度每降低10°C, 变质速率减半。"""
        assert temp_correction(10.0, 20.0) == pytest.approx(0.5)
        assert temp_correction(0.0, 20.0) == pytest.approx(0.25)

    def test_custom_base_temperature(self):
        """自定义基准温度。"""
        assert temp_correction(35.0, 25.0) == pytest.approx(2.0)


class TestQualityFactor:
    """质量衰减模型测试。"""

    def test_fresh_is_perfect(self):
        """新到货质量评分100%。"""
        q = quality_factor(0, 72, 20.0)
        assert q == pytest.approx(1.0, abs=0.01)

    def test_decay_over_time(self):
        """质量随时间衰减。"""
        q_half = quality_factor(24, 72, 20.0)
        q_full = quality_factor(72, 72, 20.0)
        assert q_half < 1.0
        assert q_full < q_half  # 时间越长质量越低

    def test_near_expiry_very_low(self):
        """接近保质期结束时质量很低。"""
        q = quality_factor(72, 72, 20.0)  # 正好达到保质期
        assert q < 0.15

    def test_hot_temperature_accelerates_decay(self):
        """高温加速衰减。"""
        q_cool = quality_factor(12, 72, 20.0)
        q_hot = quality_factor(12, 72, 35.0)
        assert q_hot < q_cool

    def test_quality_bounded_zero_to_one(self):
        """质量因子始终在[0,1]区间。"""
        for t in [0, 12, 48, 100, 1000]:
            q = quality_factor(t, 72, 25.0)
            assert 0.0 <= q <= 1.0


class TestDemandUplift:
    """需求弹性估算测试。"""

    def test_no_discount_no_uplift(self):
        """不打折需求不变。"""
        assert estimate_demand_uplift(0.0, "vegetable") == 1.0

    def test_vegetable_high_elasticity(self):
        """蔬菜需求弹性高。"""
        uplift = estimate_demand_uplift(0.10, "vegetable")
        assert uplift > 1.15  # 蔬菜弹性1.8

    def test_seafood_low_elasticity(self):
        """水产需求弹性低 (新鲜度决定)。"""
        uplift = estimate_demand_uplift(0.10, "seafood")
        assert uplift < 1.15  # 水产弹性0.8 → 1.08

    def test_discount_never_reduces_demand(self):
        """降价不会减少需求。"""
        for d in [0.0, 0.1, 0.3, 0.5]:
            assert estimate_demand_uplift(d, "vegetable") >= 1.0

    def test_dry_goods_low_elasticity(self):
        """干货几乎无弹性。"""
        uplift = estimate_demand_uplift(0.2, "dry_goods")
        assert uplift < 1.2


class TestDynamicPricingEngine:
    """核心定价引擎测试。"""

    def _make_ctx(self, **overrides) -> PricingContext:
        defaults = {
            "product_name": "白菜",
            "category": "vegetable",
            "unit_cost": 2.0,
            "original_price": 5.0,
            "current_inventory": 50.0,
            "daily_forecast": 20.0,
            "shelf_life_hours": 72,
            "hours_since_arrival": 0,
            "hours_until_close": 8,
        }
        defaults.update(overrides)
        return PricingContext(**defaults)

    def test_fresh_product_full_price(self):
        """刚到的鲜货建议原价。"""
        engine = DynamicPricingEngine(price_tier=PriceTier.BALANCED)
        ctx = self._make_ctx(hours_since_arrival=0)
        rec = engine.recommend(ctx)
        assert rec.recommended_price >= ctx.original_price * 0.95
        assert rec.strategy == MarkdownStrategy.AGE_BASED

    def test_aged_product_gets_discount(self):
        """放了2天的菜应该降价。"""
        engine = DynamicPricingEngine(price_tier=PriceTier.BALANCED)
        ctx = self._make_ctx(hours_since_arrival=48)
        rec = engine.recommend(ctx)
        assert rec.recommended_price < ctx.original_price
        assert rec.discount_pct > 0

    def test_near_expiry_clearance(self):
        """快过期的商品进入出清模式。"""
        engine = DynamicPricingEngine(price_tier=PriceTier.BALANCED)
        ctx = self._make_ctx(hours_since_arrival=68)
        rec = engine.recommend(ctx)
        assert rec.strategy == MarkdownStrategy.CLEARANCE
        assert rec.recommended_price < ctx.original_price * 0.5

    def test_closing_time_clearance(self):
        """关门前进入出清模式。"""
        engine = DynamicPricingEngine(price_tier=PriceTier.BALANCED)
        ctx = self._make_ctx(hours_until_close=1)
        rec = engine.recommend(ctx)
        assert rec.strategy == MarkdownStrategy.CLEARANCE

    def test_overstock_triggers_inventory_strategy(self):
        """严重积压触发库存驱动降价。"""
        engine = DynamicPricingEngine(price_tier=PriceTier.BALANCED)
        ctx = self._make_ctx(
            current_inventory=200.0,  # 够卖10天
            daily_forecast=20.0,
        )
        rec = engine.recommend(ctx)
        assert rec.strategy == MarkdownStrategy.INVENTORY_BASED
        assert rec.discount_pct > 0

    def test_combined_strategy(self):
        """货龄下降 + 库存偏高 → 综合策略。"""
        engine = DynamicPricingEngine(price_tier=PriceTier.BALANCED)
        ctx = self._make_ctx(
            hours_since_arrival=24,  # 已放1天 (72h保质期, 质量剩余~0.5)
            current_inventory=50.0,  # 够卖2.5天 (1.5 < days < 3 → combined)
        )
        rec = engine.recommend(ctx)
        assert rec.strategy == MarkdownStrategy.COMBINED
        assert rec.alternative_prices  # 应该有备选价格

    def test_never_below_floor_price(self):
        """价格不低于底价。"""
        engine = DynamicPricingEngine(price_tier=PriceTier.BALANCED)
        ctx = self._make_ctx(
            unit_cost=3.0,
            original_price=5.0,
            hours_since_arrival=70,  # 快过期
        )
        rec = engine.recommend(ctx)
        # 底价至少是成本的50%
        assert rec.recommended_price >= ctx.unit_cost * 0.5

    def test_conservative_tier_smaller_discounts(self):
        """保守档位降幅更小。"""
        ctx = self._make_ctx(hours_since_arrival=48)
        cons = DynamicPricingEngine(price_tier=PriceTier.CONSERVATIVE)
        bal = DynamicPricingEngine(price_tier=PriceTier.BALANCED)
        agg = DynamicPricingEngine(price_tier=PriceTier.AGGRESSIVE)

        r_cons = cons.recommend(ctx)
        r_bal = bal.recommend(ctx)
        r_agg = agg.recommend(ctx)

        assert r_cons.recommended_price >= r_bal.recommended_price
        assert r_bal.recommended_price >= r_agg.recommended_price

    def test_recommendation_has_all_fields(self):
        """定价建议包含所有必要字段。"""
        engine = DynamicPricingEngine()
        ctx = self._make_ctx()
        rec = engine.recommend(ctx)

        assert rec.product_name == "白菜"
        assert rec.strategy is not None
        assert rec.original_price > 0
        assert rec.recommended_price > 0
        assert rec.reason
        assert rec.urgency in ("low", "medium", "high", "critical")
        assert rec.expected_revenue > 0

    def test_batch_recommend_sorts_by_urgency(self):
        """批量建议按紧急程度排序。"""
        engine = DynamicPricingEngine()
        contexts = [
            self._make_ctx(product_name="鲜菜", hours_since_arrival=0),
            self._make_ctx(product_name="临期菜", hours_since_arrival=68),
            self._make_ctx(product_name="中等菜", hours_since_arrival=36),
        ]
        recs = engine.batch_recommend(contexts)
        assert recs[0].product_name == "临期菜"
        assert recs[-1].product_name == "鲜菜"

    def test_simulate_returns_all_discounts(self):
        """模拟返回所有折扣率的结果。"""
        engine = DynamicPricingEngine()
        ctx = self._make_ctx()
        results = engine.simulate(ctx)
        assert len(results) == 9  # 默认9个折扣率
        assert results[0]["discount_pct"] == 0.0
        # 折扣越大价格越低
        prices = [r["price"] for r in results]
        assert prices == sorted(prices, reverse=True)

    def test_leafy_green_shorter_shelf_life(self):
        """叶菜默认货架期更短。"""
        ctx = self._make_ctx(category="leafy_green", shelf_life_hours=None)
        # should use CATEGORY_SHELF_LIFE default
        life = CATEGORY_SHELF_LIFE.get("leafy_green", 72)
        assert life <= 48  # 叶菜 ≤ 2天


class TestConvenienceFunctions:
    """便捷函数测试。"""

    def test_recommend_price_one_liner(self):
        """一行调用定价建议。"""
        rec = recommend_price(
            product_name="菠菜",
            category="leafy_green",
            unit_cost=3.0,
            original_price=6.0,
            current_inventory=30.0,
            daily_forecast=10.0,
            hours_since_arrival=20,
        )
        assert isinstance(rec, PricingRecommendation)
        assert rec.product_name == "菠菜"

    def test_recommend_price_with_tier(self):
        """指定定价档位。"""
        rec = recommend_price(
            product_name="猪肉",
            category="meat",
            unit_cost=15.0,
            original_price=25.0,
            current_inventory=20.0,
            daily_forecast=8.0,
            hours_since_arrival=30,
            price_tier=PriceTier.AGGRESSIVE,
        )
        assert rec.discount_pct > 0


class TestEdgeCases:
    """边界情况测试。"""

    def test_zero_inventory_no_crash(self):
        """零库存不崩溃。"""
        engine = DynamicPricingEngine()
        ctx = PricingContext(
            product_name="空货",
            category="vegetable",
            unit_cost=2.0,
            original_price=5.0,
            current_inventory=0.0,
            daily_forecast=10.0,
        )
        rec = engine.recommend(ctx)
        assert rec is not None

    def test_zero_forecast_no_crash(self):
        """零预测不崩溃。"""
        engine = DynamicPricingEngine()
        ctx = PricingContext(
            product_name="新品",
            category="fruit",
            unit_cost=2.0,
            original_price=5.0,
            current_inventory=10.0,
            daily_forecast=0.0,
        )
        rec = engine.recommend(ctx)
        assert rec is not None

    def test_negative_cost_handled(self):
        """负成本不崩溃。"""
        engine = DynamicPricingEngine()
        ctx = PricingContext(
            product_name="异常",
            category="default",
            unit_cost=-1.0,
            original_price=5.0,
            current_inventory=10.0,
            daily_forecast=5.0,
        )
        rec = engine.recommend(ctx)
        assert rec.recommended_price > 0

    def test_custom_markdown_steps(self):
        """自定义降价阶梯。"""
        custom_steps = [
            MarkdownStep(0.5, 0.30, "特价"),
            MarkdownStep(0.2, 0.60, "甩卖"),
        ]
        engine = DynamicPricingEngine(markdown_steps=custom_steps)
        ctx = PricingContext(
            product_name="测试",
            category="vegetable",
            unit_cost=1.0,
            original_price=3.0,
            current_inventory=10.0,
            daily_forecast=5.0,
            hours_since_arrival=50,
            shelf_life_hours=72,
        )
        rec = engine.recommend(ctx)
        assert rec is not None

    def test_high_ambient_temp_increases_discounts(self):
        """高温环境应增加折扣幅度。"""
        ctx = PricingContext(
            product_name="猪肉",
            category="meat",
            unit_cost=12.0,
            original_price=20.0,
            current_inventory=15.0,
            daily_forecast=5.0,
            hours_since_arrival=24,
            shelf_life_hours=48,
        )
        cool = DynamicPricingEngine(ambient_temp_c=20.0, price_tier=PriceTier.BALANCED)
        hot = DynamicPricingEngine(ambient_temp_c=35.0, price_tier=PriceTier.BALANCED)

        r_cool = cool.recommend(ctx)
        r_hot = hot.recommend(ctx)
        # 高温下质量衰减更快 → 折扣应该 ≥ 常温
        assert r_hot.discount_pct >= r_cool.discount_pct
