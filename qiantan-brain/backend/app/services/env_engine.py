"""
Environment-enhanced demand estimation engine.
Quantifies external factors (temperature, rain, holiday, weekend) on sales.
"""

import json
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path


_RULES_DIR = Path(__file__).parent.parent / "rules"


def _load_holiday_table() -> dict[str, str]:
    """Load the Chinese statutory holiday date table.

    Imported lazily from weather.py so env_engine stays decoupled at import time
    and any test-time stubbing of ``app.services.weather._HOLIDAYS`` is picked up.
    """
    from app.services.weather import _HOLIDAYS

    return _HOLIDAYS


@dataclass
class EnvFactors:
    date: date
    temp_high: float | None = None
    temp_low: float | None = None
    rainfall_prob: float | None = None
    is_holiday: bool = False
    holiday_name: str | None = None
    is_weekend: bool = False
    day_of_week: int | None = None


def _load_env_coefficients() -> dict:
    """Load environmental coefficient rules."""
    config_path = _RULES_DIR / "env_coefficients.json"
    if config_path.exists():
        with open(config_path, encoding="utf-8") as f:
            return json.load(f)
    return {}


def _load_product_categories() -> dict:
    """Load product category group mapping."""
    config_path = _RULES_DIR / "product_categories.json"
    if config_path.exists():
        with open(config_path, encoding="utf-8") as f:
            return json.load(f)
    return {}


def get_temperature_coefficient(product_name: str, temp_high: float, config: dict) -> float:
    """Calculate temperature adjustment coefficient for a product."""
    temp_config = config.get("temperature", {})
    base = temp_config.get("base_range", {"min": 15, "max": 30})

    if base["min"] <= temp_high <= base["max"]:
        return 1.00

    # Hot weather
    hot_cfg = temp_config.get("hot", {})
    if temp_high > hot_cfg.get("threshold", 30):
        cooling = hot_cfg.get("groups", {}).get("cooling_demand", {})
        if product_name in cooling.get("products", []):
            return cooling.get("coefficient", 1.20)
        sensitive = hot_cfg.get("groups", {}).get("heat_sensitive", {})
        # Check category group
        categories = _load_product_categories()
        product_group = _get_product_group(product_name, categories)
        if product_group in sensitive.get("products", []):
            return sensitive.get("coefficient", 0.85)
        return hot_cfg.get("groups", {}).get("default", {}).get("coefficient", 1.00)

    # Cold weather
    cold_cfg = temp_config.get("cold", {})
    if temp_high < cold_cfg.get("threshold", 10):
        warm = cold_cfg.get("groups", {}).get("warm_demand", {})
        categories = _load_product_categories()
        product_group = _get_product_group(product_name, categories)
        if product_group in warm.get("products", []) or product_name in warm.get("products", []):
            return warm.get("coefficient", 1.15)
        return cold_cfg.get("groups", {}).get("default", {}).get("coefficient", 1.00)

    return 1.00


def get_rainfall_coefficient(rain_prob: float | None, config: dict) -> float:
    """Calculate rainfall adjustment coefficient."""
    if rain_prob is None:
        return 1.00
    brackets = config.get("rainfall", {}).get("brackets", [])
    for bracket in brackets:
        lo, hi = bracket["range"]
        if lo <= rain_prob < hi:
            return bracket["coefficient"]
    return 1.00


def _is_spring_festival_name(name: str) -> bool:
    """春节 streak includes 除夕 (New Year's Eve) and 春节 days."""
    return "春节" in name or "除夕" in name


def get_holiday_coefficient(
    is_holiday: bool, holiday_name: str | None, date_val: date, config: dict
) -> float:
    """Calculate holiday adjustment coefficient based on ``date_val`` position.

    Coefficient selection uses ``date_val`` against the statutory holiday table:
      - 3 days before 春节/除夕 → ``spring_festival_3d_before`` (pre-holiday rush)
      - First day of any holiday streak → ``holiday_eve``
      - Middle of a holiday streak → ``national_holiday``
      - 1 day after 春节 → ``spring_festival_1d_after``
      - 1 day after other holidays → ``post_holiday``
      - Otherwise → 1.00

    Falls back to name-based selection when ``date_val`` is not in the holiday
    table but the caller still reports ``is_holiday=True`` (keeps the function
    robust for callers that pass inconsistent mock inputs).
    """
    holidays = config.get("holidays", {})
    hol = _load_holiday_table()
    date_str = date_val.strftime("%Y-%m-%d")

    # 1) date_val is itself a holiday → first day vs mid-streak
    if date_str in hol:
        prev_str = (date_val - timedelta(days=1)).strftime("%Y-%m-%d")
        is_first_day = prev_str not in hol
        if is_first_day:
            return holidays.get("holiday_eve", {}).get("coefficient", 1.10)
        return holidays.get("national_holiday", {}).get("coefficient", 1.20)

    # 2) date_val is the day after a holiday ends
    prev_str = (date_val - timedelta(days=1)).strftime("%Y-%m-%d")
    if prev_str in hol:
        prev_name = hol[prev_str]
        if _is_spring_festival_name(prev_name):
            return holidays.get("spring_festival_1d_after", {}).get("coefficient", 0.60)
        return holidays.get("post_holiday", {}).get("coefficient", 0.80)

    # 3) date_val is within 3 days before 春节/除夕 streak starts
    for days_ahead in range(1, 4):
        future_str = (date_val + timedelta(days=days_ahead)).strftime("%Y-%m-%d")
        if future_str in hol and _is_spring_festival_name(hol[future_str]):
            # Ensure this really is a run-up day: the day before the future
            # 春节/除夕 date should not itself already be a holiday.
            if (
                days_ahead == 1
                or (date_val + timedelta(days=days_ahead - 1)).strftime("%Y-%m-%d") not in hol
            ):
                return holidays.get("spring_festival_3d_before", {}).get("coefficient", 1.35)

    # 4) Fallback for inconsistent mock inputs: caller says is_holiday but the
    #    date isn't in the statutory table. Preserve legacy behaviour.
    if is_holiday and holiday_name:
        if _is_spring_festival_name(holiday_name):
            return holidays.get("spring_festival_3d_before", {}).get("coefficient", 1.35)
        return holidays.get("national_holiday", {}).get("coefficient", 1.20)

    return 1.00


def get_weekend_coefficient(is_weekend: bool, day_of_week: int | None, config: dict) -> float:
    """Calculate weekend adjustment coefficient."""
    wcfg = config.get("weekend", {})
    if day_of_week == 5:  # Saturday
        return wcfg.get("saturday", 1.12)
    elif day_of_week == 6:  # Sunday
        return wcfg.get("sunday", 1.15)
    return wcfg.get("weekday", 1.00)


def _get_product_group(product_name: str, categories: dict) -> str:
    """Find which category group a product belongs to."""
    for group, info in categories.get("categories", {}).items():
        if product_name in info.get("products", []):
            return group
    return "other"


def estimate_demand(
    product_name: str,
    moving_avg_7d: float,
    moving_avg_30d: float,
    max_historical_daily: float,
    env_factors: EnvFactors,
) -> dict:
    """
    Estimate demand for a product given environmental factors.

    Formula: Predicted = MA_7 × Temp × Rain × Holiday × Weekend × Trend

    Returns dict with predicted_qty and breakdown of coefficients.
    """
    config = _load_env_coefficients()

    temp_coef = get_temperature_coefficient(product_name, env_factors.temp_high or 20, config)
    rain_coef = get_rainfall_coefficient(env_factors.rainfall_prob, config)
    holiday_coef = get_holiday_coefficient(
        env_factors.is_holiday, env_factors.holiday_name, env_factors.date, config
    )
    weekend_coef = get_weekend_coefficient(env_factors.is_weekend, env_factors.day_of_week, config)

    # Trend coefficient
    if moving_avg_30d > 0 and moving_avg_7d < moving_avg_30d * 0.9:
        trend_coef = 0.95
    elif moving_avg_30d > 0 and moving_avg_7d > moving_avg_30d * 1.1:
        trend_coef = 1.05
    else:
        trend_coef = 1.00

    predicted = moving_avg_7d * temp_coef * rain_coef * holiday_coef * weekend_coef * trend_coef

    # Clamp to reasonable bounds
    min_recommended = 1.0
    max_predicted = max_historical_daily * 1.3 if max_historical_daily > 0 else predicted * 1.5
    predicted = max(min_recommended, min(predicted, max_predicted))

    return {
        "predicted_qty": round(predicted, 1),
        "coefficients": {
            "moving_avg_7d": round(moving_avg_7d, 1),
            "temperature": temp_coef,
            "rainfall": rain_coef,
            "holiday": holiday_coef,
            "weekend": weekend_coef,
            "trend": trend_coef,
        },
        "product": temp_coef,
    }
