"""Unit tests for inventory optimization engine.

Covers safety stock, reorder point, order quantity, and recommendations
from the GitHub-learned formulas (ForecastIQ, FreshStock AI, inventorize).
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest
from app.services.inventory_optimizer import (
    PERISHABLE_HALF_LIFE,
    SERVICE_LEVEL_Z,
    InventoryOptimizer,
    Urgency,
    _estimate_waste_percentage,
)


# ── Constants ────────────────────────────────────────────────────────


class TestServiceLevelZ:
    """验证 Z 值表正确性 (标准正态分布逆CDF)."""

    def test_known_z_values(self):
        """Verify well-known Z-scores."""
        assert SERVICE_LEVEL_Z[0.90] == pytest.approx(1.28, rel=0.01)
        assert SERVICE_LEVEL_Z[0.95] == pytest.approx(1.65, rel=0.01)
        assert SERVICE_LEVEL_Z[0.99] == pytest.approx(2.33, rel=0.01)

    def test_all_z_positive(self):
        """All Z values should be positive since service level > 50%."""
        for sl, z in SERVICE_LEVEL_Z.items():
            assert sl > 0.5
            assert z > 0


class TestPerishableHalfLife:
    """验证品类半衰期合理性."""

    def test_all_have_values(self):
        """All categories should have half-life values."""
        assert len(PERISHABLE_HALF_LIFE) >= 6

    def test_leafy_greens_perish_fastest(self):
        """叶菜类 should be among the shortest half-lives."""
        assert PERISHABLE_HALF_LIFE["叶菜类"] < PERISHABLE_HALF_LIFE["根茎类"]
        assert PERISHABLE_HALF_LIFE["豆制品"] < PERISHABLE_HALF_LIFE["水果类"]

    def test_dry_goods_last_longest(self):
        """干货类 should have the longest half-life."""
        assert PERISHABLE_HALF_LIFE["干货类"] > PERISHABLE_HALF_LIFE["default"]


# ── Safety Stock ──────────────────────────────────────────────────────


class TestSafetyStock:
    """安全库存公式: SS = Z × σ × √LT"""

    def test_zero_std_gives_zero_ss(self):
        """No demand variance → no safety stock needed."""
        opt = InventoryOptimizer(service_level=0.95, lead_time_days=7)
        assert opt.calc_safety_stock(0.0) == 0.0

    def test_basic_calculation(self):
        """SS = 1.65 × 10 × √7 ≈ 43.6"""
        opt = InventoryOptimizer(service_level=0.95, lead_time_days=7)
        ss = opt.calc_safety_stock(10.0)
        expected = 1.65 * 10.0 * (7**0.5)
        assert ss == pytest.approx(expected, rel=0.01)

    def test_higher_service_level_increases_ss(self):
        """Higher service level → more safety stock."""
        opt_low = InventoryOptimizer(service_level=0.90, lead_time_days=7)
        opt_high = InventoryOptimizer(service_level=0.99, lead_time_days=7)
        assert opt_high.calc_safety_stock(10.0) > opt_low.calc_safety_stock(10.0)

    def test_longer_lead_time_increases_ss(self):
        """Longer lead time → more safety stock."""
        opt_short = InventoryOptimizer(service_level=0.95, lead_time_days=1)
        opt_long = InventoryOptimizer(service_level=0.95, lead_time_days=7)
        assert opt_long.calc_safety_stock(10.0) > opt_short.calc_safety_stock(10.0)

    def test_with_leadtime_variability(self):
        """SS with lead time variance should be ≥ without."""
        opt = InventoryOptimizer(service_level=0.95, lead_time_days=7)

        ss_no_var = opt.calc_safety_stock(10.0)
        ss_with_var = opt.calc_safety_stock_with_leadtime_variability(
            demand_std=10.0,
            leadtime_std=2.0,
            avg_demand=50.0,
        )

        assert ss_with_var >= ss_no_var
        assert ss_with_var > 0


# ── Reorder Point ─────────────────────────────────────────────────────


class TestReorderPoint:
    """再订货点公式: ROP = D̄ × LT + SS"""

    def test_basic_calculation(self):
        """ROP = 30 × 3 + 43.6 ≈ 133.6"""
        opt = InventoryOptimizer(service_level=0.95, lead_time_days=3)
        ss = opt.calc_safety_stock(10.0)
        rop = opt.calc_reorder_point(30.0, ss)
        expected = 30.0 * 3 + ss
        assert rop == pytest.approx(expected, rel=0.01)

    def test_rop_always_positive(self):
        """ROP should always be at least SS."""
        opt = InventoryOptimizer(service_level=0.95, lead_time_days=1)
        ss = opt.calc_safety_stock(5.0)
        rop = opt.calc_reorder_point(10.0, ss)
        assert rop > ss
        assert rop > 0


# ── Order Quantity ────────────────────────────────────────────────────


class TestOrderQuantity:
    """推荐补货量公式: Q = max(0, F×H + SS − I − T)"""

    def test_need_to_order(self):
        """When inventory below target, recommend ordering."""
        opt = InventoryOptimizer(service_level=0.95, lead_time_days=1)
        ss = opt.calc_safety_stock(5.0)
        qty = opt.calc_order_quantity(
            daily_forecast=30.0,
            horizon_days=7,
            safety_stock=ss,
            current_inventory=50.0,
        )
        assert qty > 0

    def test_no_need_to_order(self):
        """When inventory exceeds target, don't order."""
        opt = InventoryOptimizer(service_level=0.95, lead_time_days=1)
        ss = opt.calc_safety_stock(5.0)
        qty = opt.calc_order_quantity(
            daily_forecast=10.0,
            horizon_days=7,
            safety_stock=ss,
            current_inventory=500.0,
        )
        assert qty == 0.0

    def test_negative_never_returned(self):
        """Order quantity should never be negative."""
        opt = InventoryOptimizer()
        qty = opt.calc_order_quantity(
            daily_forecast=0.5,
            horizon_days=1,
            safety_stock=0.0,
            current_inventory=100.0,
        )
        assert qty == 0.0

    def test_in_transit_reduces_order(self):
        """In-transit inventory should reduce the order quantity."""
        opt = InventoryOptimizer(service_level=0.95, lead_time_days=1)
        ss = opt.calc_safety_stock(5.0)

        qty_no_transit = opt.calc_order_quantity(30.0, 7, ss, 50.0, in_transit=0.0)
        qty_with_transit = opt.calc_order_quantity(30.0, 7, ss, 50.0, in_transit=100.0)

        assert qty_with_transit < qty_no_transit


# ── Recommend ─────────────────────────────────────────────────────────


class TestRecommend:
    """综合推荐功能."""

    def test_normal_scenario(self):
        """Normal scenario: daily demand 30, std 8, inventory 25."""
        opt = InventoryOptimizer(service_level=0.95, lead_time_days=1)
        rec = opt.recommend(
            product_id=1,
            product_name="白菜",
            daily_forecast=30.0,
            demand_std=8.0,
            current_inventory=25.0,
            category="叶菜类",
        )

        assert rec.product_name == "白菜"
        assert rec.safety_stock > 0
        assert rec.reorder_point > 0
        assert rec.recommended_order_qty > 0
        assert rec.days_until_stockout < 1.0
        assert rec.urgency in (Urgency.URGENT, Urgency.CRITICAL)
        assert len(rec.explanation) > 0

    def test_well_stocked_scenario(self):
        """Well-stocked: no need to reorder."""
        opt = InventoryOptimizer(service_level=0.95, lead_time_days=1)
        rec = opt.recommend(
            product_id=2,
            product_name="土豆",
            daily_forecast=10.0,
            demand_std=3.0,
            current_inventory=500.0,
            category="根茎类",
        )

        assert rec.urgency == Urgency.OK
        assert rec.recommended_order_qty == 0.0
        assert rec.stockout_risk < 0.1

    def test_critical_out_of_stock(self):
        """Zero inventory → CRITICAL urgency."""
        opt = InventoryOptimizer()
        rec = opt.recommend(
            product_id=3,
            product_name="豆腐",
            daily_forecast=20.0,
            demand_std=5.0,
            current_inventory=0.0,
            category="豆制品",
        )

        assert rec.urgency == Urgency.CRITICAL
        assert rec.recommended_order_qty > 0
        assert "缺货" in "".join(rec.explanation)

    def test_waste_estimate_for_perishable(self):
        """Highly perishable items should have higher waste estimate."""
        opt = InventoryOptimizer()

        rec_leafy = opt.recommend(
            product_id=4, product_name="生菜",
            daily_forecast=5.0, demand_std=1.0,
            current_inventory=100.0, category="叶菜类",
        )
        rec_dry = opt.recommend(
            product_id=5, product_name="大米",
            daily_forecast=5.0, demand_std=1.0,
            current_inventory=100.0, category="干货类",
        )

        # 叶菜类损耗率应显著高于干货类
        assert rec_leafy.potential_waste_pct > rec_dry.potential_waste_pct
        assert rec_dry.potential_waste_pct < 5.0

    def test_service_level_affects_recommendation(self):
        """Higher service level → higher safety stock → higher reorder qty."""
        opt_low = InventoryOptimizer(service_level=0.90)
        opt_high = InventoryOptimizer(service_level=0.99)

        rec_low = opt_low.recommend(6, "苹果", 20.0, 5.0, 30.0)
        rec_high = opt_high.recommend(6, "苹果", 20.0, 5.0, 30.0)

        assert rec_high.safety_stock > rec_low.safety_stock


# ── Batch Recommend ────────────────────────────────────────────────────


class TestBatchRecommend:
    """批量推荐功能."""

    def test_batch_normal(self):
        """Batch processing of multiple products."""
        opt = InventoryOptimizer(service_level=0.95)

        products = [
            {"product_id": 1, "product_name": "白菜", "daily_forecast": 30.0,
             "demand_std": 8.0, "current_inventory": 10.0, "category": "叶菜类"},
            {"product_id": 2, "product_name": "土豆", "daily_forecast": 15.0,
             "demand_std": 3.0, "current_inventory": 200.0, "category": "根茎类"},
            {"product_id": 3, "product_name": "豆腐", "daily_forecast": 20.0,
             "demand_std": 5.0, "current_inventory": 0.0, "category": "豆制品"},
        ]

        result = opt.batch_recommend(products)

        assert len(result.recommendations) == 3
        # 最紧急的排前面
        assert result.recommendations[0].urgency == Urgency.CRITICAL  # 豆腐 (0库存)
        assert result.summary["critical_count"] == 1
        assert result.summary["total_products"] == 3
        assert result.summary["service_level"] == 0.95

    def test_batch_empty(self):
        """Empty product list returns empty result."""
        opt = InventoryOptimizer()
        result = opt.batch_recommend([])
        assert len(result.recommendations) == 0
        assert result.summary["total_products"] == 0


# ── Waste Estimation ──────────────────────────────────────────────────


class TestWasteEstimation:
    """损耗率估算."""

    def test_high_temp_increases_waste(self):
        """Higher temperature → higher waste percentage."""
        waste_cool = _estimate_waste_percentage(50.0, 10.0, "叶菜类", temperature=15.0)
        waste_hot = _estimate_waste_percentage(50.0, 10.0, "叶菜类", temperature=35.0)
        assert waste_hot > waste_cool

    def test_dry_goods_zero_waste(self):
        """干货类 should have near-zero waste."""
        waste = _estimate_waste_percentage(100.0, 5.0, "干货类", temperature=25.0)
        assert waste < 2.0

    def test_fast_moving_inventory_low_waste(self):
        """Fast-selling items have lower waste."""
        waste_slow = _estimate_waste_percentage(100.0, 1.0, "叶菜类")   # 100天卖完
        waste_fast = _estimate_waste_percentage(20.0, 20.0, "叶菜类")   # 1天卖完
        assert waste_fast < waste_slow


# ── Edge Cases ────────────────────────────────────────────────────────


class TestEdgeCases:
    """边界条件测试."""

    def test_zero_demand(self):
        """Zero forecast demand should still produce valid results."""
        opt = InventoryOptimizer()
        rec = opt.recommend(
            product_id=1, product_name="测试品",
            daily_forecast=0.0, demand_std=0.0,
            current_inventory=50.0,
        )
        assert rec.daily_forecast == 0.0
        assert rec.safety_stock == 0.0
        assert rec.recommended_order_qty == 0.0
        assert rec.urgency == Urgency.OK

    def test_very_high_variance(self):
        """Extremely high demand variance should give large safety stock."""
        opt = InventoryOptimizer(service_level=0.99)
        ss_normal = opt.calc_safety_stock(5.0)
        ss_high = opt.calc_safety_stock(50.0)
        assert ss_high > ss_normal * 5  # approximately proportional

    def test_custom_service_level_rounds_to_nearest(self):
        """Non-standard service level should round to nearest known value."""
        opt = InventoryOptimizer(service_level=0.93)
        # 0.93 is closer to 0.95 than 0.90
        assert opt.service_level == 0.95

    def test_default_values(self):
        """Default constructor should work."""
        opt = InventoryOptimizer()
        assert opt.service_level == 0.95
        assert opt.z > 0
        assert opt.lead_time == 1


# ═══════════════════════════════════════════════════════════════════════
# Newsvendor Model Tests — 报童模型
# ═══════════════════════════════════════════════════════════════════════


class TestNewsvendorNormal:
    """报童模型 — 正态分布需求."""

    def test_high_margin_orders_more(self):
        """高毛利商品应多进货。"""
        from app.services.inventory_optimizer import newsvendor_normal

        # 利润率 = (5-2)/5 = 60%
        r = newsvendor_normal(selling_price=5, unit_cost=2, salvage_value=0,
                              mean_demand=30, std_demand=8)
        # 缺货成本(3) > 超储成本(2) → 应多订
        assert r.optimal_quantity > 30
        assert r.critical_ratio > 0.5

    def test_low_margin_orders_less(self):
        """低毛利商品应保守进货。"""
        from app.services.inventory_optimizer import newsvendor_normal

        # 利润率 = (3-2.8)/3 = 6.7%
        r = newsvendor_normal(selling_price=3, unit_cost=2.8, salvage_value=0,
                              mean_demand=30, std_demand=8)
        # 缺货成本(0.2) < 超储成本(2.8) → 应少订
        assert r.optimal_quantity < 30
        assert r.critical_ratio < 0.5

    def test_salvage_value_reduces_risk(self):
        """有残值的商品可以更激进订货。"""
        from app.services.inventory_optimizer import newsvendor_normal

        # 无残值 (全损)
        r_no_salvage = newsvendor_normal(selling_price=5, unit_cost=2, salvage_value=0,
                                         mean_demand=30, std_demand=8)
        # 有残值 (半价处理)
        r_with_salvage = newsvendor_normal(selling_price=5, unit_cost=2, salvage_value=2.5,
                                           mean_demand=30, std_demand=8)
        assert r_with_salvage.optimal_quantity > r_no_salvage.optimal_quantity

    def test_zero_demand_variance(self):
        """需求完全确定时，Q* ≈ mean。"""
        from app.services.inventory_optimizer import newsvendor_normal

        r = newsvendor_normal(selling_price=5, unit_cost=2, salvage_value=0,
                              mean_demand=30, std_demand=0.01)
        assert r.optimal_quantity == pytest.approx(30.0, abs=5.0)

    def test_expected_profit_positive(self):
        """利润为正的商品应有正期望利润。"""
        from app.services.inventory_optimizer import newsvendor_normal

        r = newsvendor_normal(selling_price=8, unit_cost=3, salvage_value=1,
                              mean_demand=20, std_demand=5)
        assert r.expected_profit > 0

    def test_waste_rate_reasonable(self):
        """损耗率应在合理范围内 (0-100%)."""
        from app.services.inventory_optimizer import newsvendor_normal

        r = newsvendor_normal(selling_price=5, unit_cost=2, salvage_value=0,
                              mean_demand=30, std_demand=8)
        assert 0 <= r.waste_rate <= 100


class TestNewsvendorPoisson:
    """报童模型 — 泊松分布需求 (低销量品类)."""

    def test_low_volume_uses_poisson(self):
        """日均<5单用泊松模型。"""
        from app.services.inventory_optimizer import newsvendor_poisson

        r = newsvendor_poisson(selling_price=12, unit_cost=5, salvage_value=0, mean_demand=3)
        # 泊松分布下 Q* 必须是整数
        assert r.optimal_quantity == float(int(r.optimal_quantity))
        assert r.optimal_quantity >= 1

    def test_poisson_versus_normal_low_volume(self):
        """低销量下泊松 vs 正态对比。"""
        from app.services.inventory_optimizer import newsvendor_normal, newsvendor_poisson

        # 日均3单，标准差≈√3≈1.7
        r_poisson = newsvendor_poisson(selling_price=10, unit_cost=4, salvage_value=0, mean_demand=3)
        r_normal = newsvendor_normal(selling_price=10, unit_cost=4, salvage_value=0,
                                     mean_demand=3, std_demand=1.7)
        # 两者都在合理范围内
        assert r_poisson.optimal_quantity > 0
        assert r_normal.optimal_quantity > 0


class TestRecommendForPerishable:
    """为生鲜商品生成报童模型建议."""

    def test_leafy_green_scenario(self):
        """叶菜类: 进价2元, 售价5元, 卖不掉全损."""
        from app.services.inventory_optimizer import recommend_for_perishable

        advice = recommend_for_perishable(
            product_name="白菜",
            selling_price=5,
            unit_cost=2,
            salvage_value=0,
            mean_demand=30,
            std_demand=8,
        )
        assert advice["model"] == "newsvendor"
        assert advice["optimal_quantity"] > 0
        assert "建议采购" in advice["suggestion"]
        assert advice["waste_rate_pct"] > 0  # 生鲜总会有损耗

    def test_dry_good_scenario(self):
        """干货: 残值高 → 损耗风险小 → 可激进订货."""
        from app.services.inventory_optimizer import recommend_for_perishable

        advice = recommend_for_perishable(
            product_name="大米",
            selling_price=20,
            unit_cost=15,
            salvage_value=10,  # 干货有较高残值 (可退换/长期保存)
            mean_demand=10,
            std_demand=2,
        )
        # 残值较高 → 超储成本降低 → 可适度多订
        assert advice["optimal_quantity"] > 0
        assert advice["waste_rate_pct"] >= 0

    def test_auto_poisson_for_low_volume(self):
        """日均 <5 单自动切换泊松模型."""
        from app.services.inventory_optimizer import recommend_for_perishable

        advice = recommend_for_perishable(
            product_name="松露",
            selling_price=100,
            unit_cost=50,
            salvage_value=20,
            mean_demand=2,
            use_poisson=True,
        )
        assert advice["optimal_quantity"] > 0
