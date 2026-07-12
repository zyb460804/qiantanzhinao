"""Unit tests for environment-enhanced demand estimation engine."""

import sys
from datetime import date
from pathlib import Path


sys.path.insert(0, str(Path(__file__).parent.parent))

from app.services.env_engine import (
    EnvFactors,
    estimate_demand,
    get_holiday_coefficient,
    get_rainfall_coefficient,
    get_temperature_coefficient,
    get_weekend_coefficient,
)


class TestEnvEngine:
    """Test environmental factor calculations."""

    def test_temp_coefficient_normal_range(self):
        """Normal temp returns 1.0."""
        assert get_temperature_coefficient("白菜", 22, {}) == 1.0

    def test_temp_coefficient_hot_watermelon(self):
        """Hot weather boosts watermelon demand."""
        coef = get_temperature_coefficient(
            "西瓜",
            33,
            {
                "temperature": {
                    "hot": {
                        "threshold": 30,
                        "groups": {
                            "cooling_demand": {"products": ["西瓜"], "coefficient": 1.20},
                        },
                    },
                },
            },
        )
        assert coef == 1.20

    def test_rainfall_gradual_decline(self):
        """Higher rain probability → lower coefficient."""
        config = {
            "rainfall": {
                "brackets": [
                    {"range": [0, 30], "coefficient": 1.00},
                    {"range": [30, 50], "coefficient": 0.90},
                    {"range": [50, 70], "coefficient": 0.75},
                    {"range": [70, 100], "coefficient": 0.60},
                ],
            },
        }
        assert get_rainfall_coefficient(10, config) == 1.00
        assert get_rainfall_coefficient(40, config) == 0.90
        assert get_rainfall_coefficient(60, config) == 0.75
        assert get_rainfall_coefficient(80, config) == 0.60

    def test_rainfall_none_defaults(self):
        """None rainfall returns 1.0 (no adjustment)."""
        assert get_rainfall_coefficient(None, {}) == 1.00

    def test_weekend_coefficients(self):
        """Saturday and Sunday get different boosts."""
        config = {"weekend": {"saturday": 1.12, "sunday": 1.15, "weekday": 1.00}}
        assert get_weekend_coefficient(False, 5, config) == 1.12  # Saturday
        assert get_weekend_coefficient(False, 6, config) == 1.15  # Sunday
        assert get_weekend_coefficient(False, 0, config) == 1.00  # Monday

    def test_holiday_spring_festival(self):
        """Spring Festival has special coefficient."""
        # With empty config, falls back to default 1.35 for Spring Festival
        coef = get_holiday_coefficient(True, "春节", date.today(), {})
        assert coef == 1.35

    def test_holiday_coefficient_with_config(self):
        """Holiday coefficient read from config."""
        config = {
            "holidays": {
                "spring_festival_3d_before": {"coefficient": 1.35},
                "national_holiday": {"coefficient": 1.20},
            },
        }
        assert get_holiday_coefficient(True, "春节", date.today(), config) == 1.35
        assert get_holiday_coefficient(True, "国庆", date.today(), config) == 1.20
        # Non-holidays return 1.0
        assert get_holiday_coefficient(False, None, date.today(), config) == 1.00

    def test_estimate_demand_basic(self):
        """Basic demand estimation with normal conditions."""
        factors = EnvFactors(
            date=date.today(),
            temp_high=22,
            temp_low=15,
            rainfall_prob=10,
            is_holiday=False,
            is_weekend=False,
            day_of_week=0,
        )
        result = estimate_demand(
            product_name="白菜",
            moving_avg_7d=20.0,
            moving_avg_30d=18.0,
            max_historical_daily=35.0,
            env_factors=factors,
        )
        assert result["predicted_qty"] > 0
        assert "coefficients" in result
        assert result["coefficients"]["temperature"] == 1.00  # Normal temp

    def test_estimate_demand_weekend_boost(self):
        """Weekend boosts demand."""
        factors = EnvFactors(
            date=date(2026, 9, 13),  # Sunday
            temp_high=25,
            rainfall_prob=10,
            is_holiday=False,
            is_weekend=True,
            day_of_week=6,
        )
        result = estimate_demand(
            product_name="白菜",
            moving_avg_7d=20.0,
            moving_avg_30d=18.0,
            max_historical_daily=40.0,
            env_factors=factors,
        )
        assert result["coefficients"]["weekend"] == 1.15

    def test_estimate_demand_clamped_at_max(self):
        """Prediction is capped at max_historical * 1.3."""
        factors = EnvFactors(
            date=date.today(),
            temp_high=25,
            rainfall_prob=0,
            is_holiday=False,
            is_weekend=False,
            day_of_week=0,
        )
        result = estimate_demand(
            product_name="白菜",
            moving_avg_7d=100.0,
            moving_avg_30d=80.0,
            max_historical_daily=50.0,
            env_factors=factors,
        )
        # Should be clamped to 50 * 1.3 = 65
        assert result["predicted_qty"] <= 65.0


if __name__ == "__main__":
    test = TestEnvEngine()
    test.test_temp_coefficient_normal_range()
    test.test_temp_coefficient_hot_watermelon()
    test.test_rainfall_gradual_decline()
    test.test_rainfall_none_defaults()
    test.test_weekend_coefficients()
    test.test_holiday_spring_festival()
    test.test_estimate_demand_basic()
    test.test_estimate_demand_weekend_boost()
    test.test_estimate_demand_clamped_at_max()
    print("All env_engine tests passed!")
