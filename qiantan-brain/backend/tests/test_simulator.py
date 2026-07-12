"""Unit tests for the What-if simulation engine."""

import sys
from pathlib import Path


sys.path.insert(0, str(Path(__file__).parent.parent))

from app.services.simulator import simulate_what_if


class TestSimulator:
    """Test the decision simulation sandbox engine."""

    def test_basic_simulation(self):
        """Basic simulation produces sensible output."""
        result = simulate_what_if(
            purchase_qty=50,
            unit_cost=0.3,
            unit_price=1.5,
            product_name="白菜",
            estimated_sales_base=18.0,
        )
        assert "output" in result
        assert "comparison" in result
        out = result["output"]
        assert out["total_cost"] == 15.0  # 50 * 0.3
        assert out["estimated_sales"] <= 50  # Can't sell more than purchased
        assert out["waste_rate"] >= 0
        assert out["waste_rate"] <= 1

    def test_sales_cannot_exceed_purchase(self):
        """Estimated sales cannot exceed purchase quantity."""
        result = simulate_what_if(
            purchase_qty=10,
            unit_cost=0.5,
            unit_price=2.0,
            product_name="白菜",
            estimated_sales_base=100.0,  # Predicted demand far exceeds purchase
        )
        assert result["output"]["estimated_sales"] <= 10

    def test_zero_profit_on_zero_purchase(self):
        """Buying nothing → zero cost, zero revenue."""
        result = simulate_what_if(
            purchase_qty=0,
            unit_cost=0.3,
            unit_price=1.5,
            product_name="白菜",
            estimated_sales_base=18.0,
        )
        out = result["output"]
        assert out["total_cost"] == 0
        assert out["estimated_revenue"] == 0
        assert out["net_profit"] == 0

    def test_comparison_included(self):
        """Result includes comparison with baseline."""
        result = simulate_what_if(
            purchase_qty=50,
            unit_cost=0.3,
            unit_price=1.5,
            product_name="白菜",
            estimated_sales_base=18.0,
        )
        cmp = result["comparison"]
        assert "baseline_net_profit" in cmp
        assert "improvement" in cmp
        assert "verdict" in cmp
        assert "recommendation" in cmp

    def test_large_purchase_high_waste(self):
        """Buying far more than demand → high waste rate."""
        result = simulate_what_if(
            purchase_qty=200,
            unit_cost=0.3,
            unit_price=1.5,
            product_name="白菜",
            estimated_sales_base=18.0,
        )
        assert result["output"]["waste_rate"] > 0.5  # More than half wasted

    def test_price_discount_simulation(self):
        """Lower price below historical → can increase sales."""
        result = simulate_what_if(
            purchase_qty=50,
            unit_cost=0.3,
            unit_price=0.9,  # Discounted from 1.5
            product_name="白菜",
            estimated_sales_base=18.0,
            avg_historical_price=1.5,
        )
        # Price elasticity: ratio = 0.9/1.5 = 0.6, mult = 1 + (1-0.6)*1.5 = 1.6
        # est_sales = min(18 * 1.6, 50) = 28.8
        out = result["output"]
        assert out["estimated_sales"] > 18.0  # Sales increase from discount


if __name__ == "__main__":
    test = TestSimulator()
    test.test_basic_simulation()
    test.test_sales_cannot_exceed_purchase()
    test.test_zero_profit_on_zero_purchase()
    test.test_comparison_included()
    test.test_large_purchase_high_waste()
    test.test_price_discount_simulation()
    print("All simulator tests passed!")
