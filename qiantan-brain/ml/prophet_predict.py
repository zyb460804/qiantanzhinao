"""
Prophet time-series forecasting pipeline for QianTan Brain.
Replaces/supplements rule-engine with ML prediction.

Install: pip install prophet pandas numpy
Usage:   python prophet_predict.py --product baicai --days 7 --compare
"""

import argparse
import json
import os
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

try:
    from prophet import Prophet
except ImportError:
    print("Install: pip install prophet pandas numpy")
    exit(1)

sys.path.insert(0, str(Path(__file__).parent.parent / "backend"))

# Chinese holidays config
CN_HOLIDAYS = {
    "Spring Festival": [
        date(2027, 1, 26), date(2027, 1, 27), date(2027, 1, 28),
        date(2027, 1, 29), date(2027, 1, 30), date(2027, 1, 31),
        date(2027, 2, 1),
    ],
    "Labor Day": [date(2027, 5, 1), date(2027, 5, 2), date(2027, 5, 3)],
    "National Day": [date(2027, 10, 1)] + [date(2027, 10, d) for d in range(2, 8)],
}


def build_prophet_dataframe(records: list[dict]) -> pd.DataFrame:
    """Convert DB records to Prophet format (ds, y, + regressors)."""
    if not records:
        raise ValueError("Empty dataset")
    df = pd.DataFrame(records)
    df["ds"] = pd.to_datetime(df["date"])
    df["y"] = df["quantity"].astype(float)
    return df


def train_prophet(df: pd.DataFrame, use_regressors: bool = True) -> Prophet:
    """Train Prophet model with optional environmental regressors."""
    # Build custom holidays
    rows = []
    for name, dates in CN_HOLIDAYS.items():
        for d in dates:
            rows.append({"holiday": name, "ds": d, "lower_window": -2, "upper_window": 1})
    holiday_df = pd.DataFrame(rows) if rows else None

    model = Prophet(
        growth="linear",
        yearly_seasonality=False,
        weekly_seasonality=True,
        daily_seasonality=False,
        seasonality_mode="additive",
        changepoint_prior_scale=0.05,
        interval_width=0.80,
        holidays=holiday_df,
    )

    if use_regressors:
        for col in ["temp_high", "rainfall_prob", "is_weekend"]:
            if col in df.columns:
                model.add_regressor(col)

    model.fit(df)
    return model


def predict(model: Prophet, periods: int = 7, future_regressors: dict | None = None) -> pd.DataFrame:
    """Generate N-day forecast with optional future regressor values."""
    future = model.make_future_dataframe(periods=periods)

    # Ensure regressor columns exist in future dataframe
    for col in model.extra_regressors:
        if col not in future.columns:
            # Fill with training mean (historical) and given values (future)
            if future_regressors and col in future_regressors:
                vals = future_regressors[col]
                col_data = []
                for i, row in future.iterrows():
                    d = row["ds"].date() if hasattr(row["ds"], "date") else row["ds"]
                    idx = (d - date.today()).days
                    if 0 < idx <= len(vals):
                        col_data.append(vals[idx - 1])
                    else:
                        col_data.append(0)
                future[col] = col_data
            else:
                future[col] = 0

    forecast = model.predict(future)
    return forecast


def ensemble_predict(prophet_qty: float, rule_qty: float, data_days: int,
                     prophet_lower: float = 0, prophet_upper: float = 0) -> dict:
    """Blend Prophet and rule-engine predictions by data maturity."""
    w_p = min(0.7, data_days / 120)
    w_r = 1 - w_p
    blended = round(w_p * prophet_qty + w_r * rule_qty, 1)

    return {
        "blended_prediction": blended,
        "prophet_prediction": round(prophet_qty, 1),
        "rule_engine_prediction": round(rule_qty, 1),
        "prophet_weight": round(w_p, 2),
        "rule_weight": round(w_r, 2),
        "prophet_80ci": [round(prophet_lower, 1), round(prophet_upper, 1)],
        "message": f"ML(w={w_p:.0%}) + rules(w={w_r:.0%}) => {blended} jin",
    }


def _generate_mock_data(product_name: str, days: int = 90) -> list[dict]:
    """Generate mock sales history for demo/testing."""
    np.random.seed(abs(hash(product_name)) % 2**32)
    records = []
    base = abs(hash(product_name)) % 30 + 10  # 10-40 jin/day

    for d in range(days, 0, -1):
        day = date.today() - timedelta(days=d)
        dow = day.weekday()
        is_weekend = dow >= 5
        temp = 20 + np.random.normal(5, 5)

        mult = 1.0
        if is_weekend: mult += 0.2
        if temp > 30: mult += 0.15
        if np.random.random() < 0.2: mult -= 0.1

        qty = max(0, base * mult + np.random.normal(0, base * 0.15))
        records.append({
            "date": day.strftime("%Y-%m-%d"),
            "quantity": round(qty, 1),
            "temp_high": round(temp, 1),
            "rainfall_prob": round(np.random.uniform(0, 80), 1) if np.random.random() < 0.25 else 0,
            "is_weekend": is_weekend,
        })
    return records


def main():
    parser = argparse.ArgumentParser(description="QianTan Prophet Prediction")
    parser.add_argument("--product", default="baicai")
    parser.add_argument("--days", type=int, default=7)
    parser.add_argument("--data_file", default=None)
    parser.add_argument("--output", default=None)
    parser.add_argument("--compare", action="store_true")
    args = parser.parse_args()

    print(f"QianTan Brain - Prophet Sales Forecast")
    print(f"  Product: {args.product}")
    print(f"  Horizon: {args.days} days")

    # 1. Load data
    if args.data_file:
        records = pd.read_csv(args.data_file).to_dict("records")
    else:
        print("  [MOCK] No data file provided, using synthetic data")
        records = _generate_mock_data(args.product, 90)

    df = build_prophet_dataframe(records)
    data_days = len(df)
    print(f"  History: {data_days} days")

    # 2. Train
    print("  Training Prophet...")
    model = train_prophet(df)

    # 3. Predict
    forecast = predict(model, periods=args.days)
    future = forecast.tail(args.days)

    results = []
    for _, row in future.iterrows():
        results.append({
            "date": row["ds"].strftime("%Y-%m-%d"),
            "predicted_qty": round(row["yhat"], 1),
            "lower_bound": round(row["yhat_lower"], 1),
            "upper_bound": round(row["yhat_upper"], 1),
        })

    print(f"\n  Forecast:")
    for r in results:
        print(f"    {r['date']}: {r['predicted_qty']} jin ({r['lower_bound']}~{r['upper_bound']})")

    # 4. Compare with rule engine
    if args.compare:
        print(f"\n  [COMPARE] vs Rule Engine:")
        try:
            from app.services.env_engine import EnvFactors, estimate_demand

            rule_pred = estimate_demand(
                product_name=args.product,
                moving_avg_7d=df["y"].tail(7).mean(),
                moving_avg_30d=df["y"].tail(30).mean(),
                max_historical_daily=df["y"].max(),
                env_factors=EnvFactors(
                    date=date.today(),
                    temp_high=28, rainfall_prob=20,
                    is_holiday=False, is_weekend=False,
                    day_of_week=date.today().weekday(),
                ),
            )
            rule_qty = rule_pred["predicted_qty"]

            ensemble = ensemble_predict(
                prophet_qty=results[0]["predicted_qty"],
                rule_qty=rule_qty,
                data_days=data_days,
                prophet_lower=results[0]["lower_bound"],
                prophet_upper=results[0]["upper_bound"],
            )

            print(f"    Prophet:    {results[0]['predicted_qty']} jin")
            print(f"    Rule Engine: {rule_qty} jin")
            print(f"    Ensemble:    {ensemble['blended_prediction']} jin")
            print(f"    {ensemble['message']}")
        except ImportError:
            print("    (Rule engine not available)")

    # 5. Save
    if args.output:
        output = {"product": args.product, "data_days": data_days, "predictions": results}
        with open(args.output, "w", encoding="utf-8") as f:
            json.dump(output, f, ensure_ascii=False, indent=2)
        print(f"\n  [OK] Saved to: {args.output}")


if __name__ == "__main__":
    main()
