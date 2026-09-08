"""Demand forecasting service — automatic model selection based on data volume.

Model selection strategy:
  - < 7 days of data:   Rule-based (simple heuristics)
  - 7-30 days:          Moving average with environment coefficients
  - > 30 days:          Prophet with weather/holiday regressors
  - Prophet failure:    Automatic fallback to moving average

This service integrates with advisor.py to provide online predictions.
"""

import logging
import uuid
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.timezone import cst_date_of_utc_naive, cst_days_ago_bounds_utc, cst_today
from app.models.environment import EnvironmentRecord
from app.models.inventory import InventoryRecord


logger = logging.getLogger(__name__)


async def _get_daily_sales_history(
    db: AsyncSession,
    merchant_id: uuid.UUID,
    product_id: int,
    sku_id: uuid.UUID | None = None,
    days: int = 90,
) -> list[dict]:
    """Get daily sales history for a product/SKU, filled with zeros for missing days.

    按日聚合拉到 Python 侧按 CST 业务日重算（审计 C4 / 方言陷阱）：
    SQL 的 func.date(event_time) 在 SQLite/PG 下都只能得到 UTC 日期键
    （SQLite 的 date(col,'+8 hours') 修饰符 PG 不支持），CST 00:00-08:00 的
    销售会被记到前一天，污染移动平均与 Prophet 训练序列。单 SKU 数据量小。
    """
    start = cst_days_ago_bounds_utc(days)[0]

    filters = [
        InventoryRecord.merchant_id == merchant_id,
        InventoryRecord.product_id == product_id,
        InventoryRecord.is_voided == False,  # noqa: E712
        InventoryRecord.event_type == "sale",
        InventoryRecord.event_time >= start,
    ]
    if sku_id is not None:
        filters.append((InventoryRecord.sku_id == sku_id) | (InventoryRecord.sku_id.is_(None)))
    else:
        filters.append(InventoryRecord.sku_id.is_(None))

    rows = (
        await db.execute(
            select(InventoryRecord.event_time, InventoryRecord.quantity).where(*filters)
        )
    ).all()

    # Build CST-date -> qty map
    sales_map: dict[str, float] = {}
    for event_time, quantity in rows:
        d_str = cst_date_of_utc_naive(event_time).isoformat()
        sales_map[d_str] = sales_map.get(d_str, 0.0) + abs(float(quantity or 0))

    # Fill missing dates with 0
    history = []
    for i in range(days):
        d_str = (cst_today() - timedelta(days=days - 1 - i)).isoformat()
        history.append(
            {
                "date": d_str,
                "qty": sales_map.get(d_str, 0.0),
            }
        )

    return history


async def _get_env_factors(db: AsyncSession, merchant_id: uuid.UUID) -> dict:
    """Get today's environment factors for prediction adjustment."""
    # EnvironmentRecord.date 是业务日（CST），取业务「今天」而非服务器本地日期
    today = cst_today()
    env_query = select(EnvironmentRecord).where(EnvironmentRecord.date == today)
    env_result = await db.execute(env_query)
    env = env_result.scalar_one_or_none()

    if not env:
        return {
            "temp_high": 25.0,
            "rainfall_prob": 10.0,
            "is_weekend": today.weekday() >= 5,
            "is_holiday": False,
        }

    return {
        "temp_high": float(env.temp_high or 25),
        "rainfall_prob": float(env.rainfall_prob or 10),
        "is_weekend": env.is_weekend if env.is_weekend is not None else today.weekday() >= 5,
        "is_holiday": env.is_holiday if env.is_holiday is not None else False,
    }


def _rule_based_predict(history: list[dict], env: dict) -> dict:
    """Rule-based prediction for cold start (< 7 days data)."""
    # Use simple average of available data
    recent_sales = [h["qty"] for h in history[-7:] if h["qty"] > 0]
    if not recent_sales:
        base_qty = 10.0  # Default assumption
    else:
        base_qty = sum(recent_sales) / len(recent_sales)

    # Apply environment coefficients
    temp = env["temp_high"]
    rain = env["rainfall_prob"]
    is_weekend = env["is_weekend"]
    is_holiday = env["is_holiday"]

    temp_coeff = 1.0
    if temp > 32:
        temp_coeff = 1.15  # Hot weather boosts some products
    elif temp < 10:
        temp_coeff = 0.85

    rain_coeff = 1.0 - (rain / 100) * 0.3  # Rain reduces foot traffic
    weekend_coeff = 1.2 if is_weekend else 1.0
    holiday_coeff = 1.3 if is_holiday else 1.0

    predicted = base_qty * temp_coeff * rain_coeff * weekend_coeff * holiday_coeff

    # 需求标准差估算 (数据少时用 base_qty * 0.5 作为粗略估计)
    demand_std = base_qty * 0.5 if base_qty > 0 else 1.0

    return {
        "predicted_qty": round(max(0, predicted), 1),
        "baseline_qty": round(base_qty, 1),
        "model": "rule_based",
        "data_days": len([h for h in history if h["qty"] > 0]),
        "factors": [
            {"name": "基础均值", "value": round(base_qty, 1)},
            {"name": "温度系数", "value": round(temp_coeff, 2)},
            {"name": "降雨系数", "value": round(rain_coeff, 2)},
            {"name": "周末系数", "value": round(weekend_coeff, 2)},
            {"name": "节假日系数", "value": round(holiday_coeff, 2)},
        ],
        "confidence": 0.4,
        "lower_bound": round(max(0, predicted * 0.6), 1),
        "upper_bound": round(predicted * 1.5, 1),
        "demand_std": round(demand_std, 2),
    }


def _moving_average_predict(history: list[dict], env: dict) -> dict:
    """Moving average prediction with environment coefficients (7-30 days data)."""
    # 7-day moving average
    recent_7d = [h["qty"] for h in history[-7:]]
    avg_7d = sum(recent_7d) / max(len(recent_7d), 1)

    # 30-day moving average if available
    recent_30d = [h["qty"] for h in history[-30:]]
    avg_30d = sum(recent_30d) / max(len(recent_30d), 1)

    # Weighted blend: 70% recent 7-day, 30% 30-day
    base_qty = avg_7d * 0.7 + avg_30d * 0.3

    # Environment adjustments
    temp = env["temp_high"]
    rain = env["rainfall_prob"]
    is_weekend = env["is_weekend"]
    is_holiday = env["is_holiday"]

    temp_coeff = 1.0
    if temp > 32:
        temp_coeff = 1.15
    elif temp > 28:
        temp_coeff = 1.08
    elif temp < 10:
        temp_coeff = 0.85

    rain_coeff = 1.0 - (rain / 100) * 0.3
    weekend_coeff = 1.2 if is_weekend else 1.0
    holiday_coeff = 1.3 if is_holiday else 1.0

    predicted = base_qty * temp_coeff * rain_coeff * weekend_coeff * holiday_coeff

    # Calculate variance for confidence interval
    if len(recent_7d) >= 3:
        variance = sum((x - avg_7d) ** 2 for x in recent_7d) / len(recent_7d)
        std_dev = variance**0.5
    else:
        std_dev = base_qty * 0.3

    return {
        "predicted_qty": round(max(0, predicted), 1),
        "baseline_qty": round(base_qty, 1),
        "model": "moving_average",
        "data_days": len([h for h in history if h["qty"] > 0]),
        "factors": [
            {"name": "7日均值", "value": round(avg_7d, 1)},
            {"name": "30日均值", "value": round(avg_30d, 1)},
            {"name": "温度系数", "value": round(temp_coeff, 2)},
            {"name": "降雨系数", "value": round(rain_coeff, 2)},
            {"name": "周末系数", "value": round(weekend_coeff, 2)},
            {"name": "节假日系数", "value": round(holiday_coeff, 2)},
        ],
        "confidence": 0.65,
        "lower_bound": round(max(0, predicted - std_dev), 1),
        "upper_bound": round(predicted + std_dev, 1),
        "demand_std": round(std_dev, 2),
    }


def _prophet_predict(history: list[dict], env: dict) -> dict | None:
    """Prophet prediction with weather and holiday regressors (> 30 days data).

    Returns None if Prophet is not available or prediction fails.
    """
    try:
        import pandas as pd
        from prophet import Prophet
    except ImportError:
        logger.info("Prophet not installed — falling back to moving average")
        return None

    try:
        # Prepare dataframe
        df = pd.DataFrame(history)
        df.columns = ["ds", "y"]
        df["ds"] = pd.to_datetime(df["ds"])

        # 修复（审计 P1-Prophet）：原 df = df[df["y"] > 0] 剔除零销日会系统性
        # 抬高预测均值并破坏时序等间距（Prophet 需要连续日期）。现保留零销日。
        if len(df) < 14:
            return None

        # 修复（审计 P1-Prophet）：原实现把 df["temp"] 整列赋成同一个常数
        # （env["temp_high"]），零方差回归变量与截距共线，Prophet 学不出任何
        # 有意义的系数。且 _get_env_factors 只返回当天天气，无历史逐日数据。
        # 现改为：仅当我们真的能取到逐日历史天气时才加 regressor；
        # 否则只保留趋势 + 周季节性（Prophet 的核心能力），不假装有天气回归。
        has_historical_weather = (
            isinstance(env, dict)
            and isinstance(env.get("history_temp"), list)
            and len(env["history_temp"]) == len(df)
        )

        if has_historical_weather:
            df["temp"] = env["history_temp"]
            df["rain"] = env.get("history_rain", [0.0] * len(df))

        # Create and fit model
        model = Prophet(
            yearly_seasonality=False,
            weekly_seasonality=True,
            daily_seasonality=False,
            changepoint_prior_scale=0.05,
        )
        if has_historical_weather:
            model.add_regressor("temp")
            model.add_regressor("rain")
        model.fit(df)

        # Predict tomorrow
        future = model.make_future_dataframe(periods=1)
        if has_historical_weather:
            # 明天的天气用 forecast 值（env 里的 temp_high / rainfall_prob）
            future["temp"] = env["history_temp"] + [env.get("temp_high", df["temp"].mean())]
            future["rain"] = env.get("history_rain", [0.0] * len(df)) + [
                env.get("rainfall_prob", 0.0)
            ]

        forecast = model.predict(future)
        tomorrow = forecast.iloc[-1]

        predicted = max(0, float(tomorrow["yhat"]))
        lower = max(0, float(tomorrow["yhat_lower"]))
        upper = float(tomorrow["yhat_upper"])

        # Calculate baseline (without regressors)
        baseline = float(df["y"].tail(7).mean())
        # 需求标准差 (7日滚动)
        demand_std = float(df["y"].tail(7).std()) if len(df["y"]) >= 7 else baseline * 0.3

        # 修复（审计 P1-Prophet）：Prophet 的 forecast DataFrame 没有
        # extra_regressors 这一列，实际列名是 extra_regressors_additive。
        # 原实现取不存在的列名 → 界面恒显示 0.00。
        factors = [
            {"name": "趋势", "value": round(float(tomorrow["trend"]), 1)},
            {"name": "周季节性", "value": round(float(tomorrow.get("weekly", 0)), 1)},
        ]
        if has_historical_weather:
            factors.append(
                {
                    "name": "温度回归",
                    "value": round(float(tomorrow.get("extra_regressors_additive", 0)), 2),
                }
            )
            factors.append(
                {
                    "name": "降雨回归",
                    "value": round(float(tomorrow.get("extra_regressors_additive", 0)), 2),
                }
            )

        return {
            "predicted_qty": round(predicted, 1),
            "baseline_qty": round(baseline, 1),
            "model": "prophet",
            "demand_std": round(demand_std, 2),
            "data_days": len(df),
            "factors": factors,
            "confidence": 0.80,
            "lower_bound": round(lower, 1),
            "upper_bound": round(upper, 1),
        }
    except Exception as e:
        logger.warning("Prophet prediction failed: %s — falling back", e)
        return None


async def predict_demand(
    db: AsyncSession,
    merchant_id: uuid.UUID,
    product_id: int,
    sku_id: uuid.UUID | None = None,
    max_days: int = 90,
) -> dict:
    """Predict tomorrow's demand for a product/SKU.

    Automatically selects the best model based on available data volume:
    - < 7 days:   rule_based
    - 7-30 days:  moving_average
    - > 30 days:  prophet (with fallback to moving_average)

    Returns dict with: predicted_qty, baseline_qty, model, data_days,
                       factors, confidence, lower_bound, upper_bound
    """
    history = await _get_daily_sales_history(db, merchant_id, product_id, sku_id, max_days)
    env = await _get_env_factors(db, merchant_id)

    # Count days with actual sales data
    active_days = len([h for h in history if h["qty"] > 0])

    # Model selection
    result: dict
    if active_days < 7:
        result = _rule_based_predict(history, env)
    elif active_days < 30:
        result = _moving_average_predict(history, env)
    else:
        # Try Prophet first, fall back to moving average
        prophet_result = _prophet_predict(history, env)
        result = (
            prophet_result if prophet_result is not None else _moving_average_predict(history, env)
        )

    result["product_id"] = product_id
    result["sku_id"] = str(sku_id) if sku_id else None
    result["env_summary"] = {
        "temp_high": env["temp_high"],
        "rainfall_prob": env["rainfall_prob"],
        "is_weekend": env["is_weekend"],
        "is_holiday": env["is_holiday"],
    }

    return result
