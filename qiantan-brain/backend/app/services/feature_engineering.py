"""需求预测特征工程 — 基于 FreshStock AI 的 12 维特征体系。

参考: FreshStock AI (Random Forest 87.3% 准确率)
  - Lag features: 滞后 1/2/3/7 天的需求量
  - Rolling statistics: 7/30 天滚动均值 & 标准差
  - Calendar features: 星期几、是否周末、月份、年中天数
  - Momentum: 需求动量 (1天前 - 7天前)

用于:
  - 为 ML 模型提供训练特征
  - 增强 forecast.py 的 Prophet 预测 (额外特征)
  - 支持未来添加 Random Forest / XGBoost 模型
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import numpy as np

logger = logging.getLogger(__name__)

# ── 默认参数 ──────────────────────────────────────────────────────────
DEFAULT_LAG_DAYS: tuple[int, ...] = (1, 2, 3, 7)
DEFAULT_ROLLING_WINDOWS: tuple[int, ...] = (7, 30)
DEFAULT_FEATURE_NAMES: list[str] = [
    "day_of_week",
    "is_weekend",
    "month",
    "day_of_year",
    "demand_lag_1",
    "demand_lag_2",
    "demand_lag_3",
    "demand_lag_7",
    "demand_rolling_7",
    "demand_rolling_30",
    "demand_std_7",
    "demand_std_30",
    "demand_momentum",
]


@dataclass
class FeatureSet:
    """一个时间点的特征向量。"""
    date_str: str
    values: dict[str, float]  # feature_name → value
    target: float | None = None  # 实际需求 (训练时有值，预测时为 None)


@dataclass
class FeatureMatrix:
    """特征矩阵 — 训练或预测用。"""
    features: list[str]
    X: list[list[float]]
    y: list[float] | None = None
    dates: list[str] | None = None


def build_features_from_history(
    history: list[dict],
    lag_days: tuple[int, ...] = DEFAULT_LAG_DAYS,
    rolling_windows: tuple[int, ...] = DEFAULT_ROLLING_WINDOWS,
) -> FeatureMatrix:
    """从历史销售数据构建特征矩阵。

    Args:
        history: 按时间排序的销售记录列表，每条为 {"date": str, "qty": float}
        lag_days: 滞后天数元组，默认 (1, 2, 3, 7)
        rolling_windows: 滚动窗口元组，默认 (7, 30)

    Returns:
        FeatureMatrix 包含特征名、特征矩阵 X、目标值 y (如有)、日期列表
    """
    n = len(history)
    if n < max(lag_days) + 1:
        logger.warning(
            "History too short (%d days) for lag=%s features. Minimum %d required.",
            n, lag_days, max(lag_days) + 1,
        )
        return FeatureMatrix(features=[], X=[], y=[], dates=[])

    # 构建日期列表和需求量列表
    dates: list[str] = [h["date"] for h in history]
    qtys: list[float] = [h["qty"] for h in history]
    qty_arr = np.array(qtys, dtype=float)

    # 为每个日期构建特征
    feature_names = _build_feature_names(lag_days, rolling_windows)
    X: list[list[float]] = []
    y: list[float] = []

    max_lag = max(lag_days)
    max_window = max(rolling_windows)

    for i in range(max(max_lag, max_window), n):
        row = _build_single_row(
            date_str=dates[i],
            qty_arr=qty_arr,
            idx=i,
            lag_days=lag_days,
            rolling_windows=rolling_windows,
        )
        X.append([row[name] for name in feature_names])
        y.append(qtys[i])

    return FeatureMatrix(
        features=feature_names,
        X=X,
        y=y,
        dates=dates[max(max_lag, max_window):],
    )


def build_prediction_features(
    history: list[dict],
    target_date_str: str,
    lag_days: tuple[int, ...] = DEFAULT_LAG_DAYS,
    rolling_windows: tuple[int, ...] = DEFAULT_ROLLING_WINDOWS,
) -> dict[str, float] | None:
    """为未来的某一天构建预测特征向量。

    使用最新的历史数据来填充 lag/rolling 特征。

    Args:
        history: 历史销售记录 (不含预测日)
        target_date_str: 目标日期 "YYYY-MM-DD"
        lag_days: 滞后天数
        rolling_windows: 滚动窗口

    Returns:
        特征字典 {feature_name: value}，数据不足时返回 None
    """
    n = len(history)
    max_lag = max(lag_days)
    if n < max_lag:
        logger.warning("Not enough history for lag=%s prediction", max_lag)
        return None

    qtys = [h["qty"] for h in history]
    qty_arr = np.array(qtys, dtype=float)

    # 对于预测日，lag 特征是历史的最后 N 天
    features: dict[str, float] = {}

    # Calendar features (基于目标日期)
    from datetime import date, timedelta

    try:
        target_date = date.fromisoformat(target_date_str)
    except (ValueError, TypeError):
        # 如果日期解析失败，使用历史最后一天 + 1
        last_date = date.fromisoformat(history[-1]["date"])
        target_date = last_date + timedelta(days=1)

    features["day_of_week"] = float(target_date.weekday())
    features["is_weekend"] = 1.0 if target_date.weekday() >= 5 else 0.0
    features["month"] = float(target_date.month)
    features["day_of_year"] = float(target_date.timetuple().tm_yday)

    # Lag features: 使用最近 N 天的实际需求
    for lag in lag_days:
        idx = n - lag
        features[f"demand_lag_{lag}"] = qty_arr[idx]

    # Rolling features: 使用最后 N 天的统计量
    for window in rolling_windows:
        if n >= window:
            window_data = qty_arr[-window:]
            features[f"demand_rolling_{window}"] = float(np.mean(window_data))
            features[f"demand_std_{window}"] = float(np.std(window_data))
        else:
            features[f"demand_rolling_{window}"] = float(np.mean(qty_arr))
            features[f"demand_std_{window}"] = float(np.std(qty_arr))

    # Momentum: 1天前 - 7天前的差值
    if n >= 7:
        features["demand_momentum"] = qty_arr[-1] - qty_arr[-7]
    else:
        features["demand_momentum"] = 0.0

    return features


# ── 内部辅助 ──────────────────────────────────────────────────────────

def _build_feature_names(
    lag_days: tuple[int, ...],
    rolling_windows: tuple[int, ...],
) -> list[str]:
    """构建特征名列表。"""
    names = ["day_of_week", "is_weekend", "month", "day_of_year"]
    for lag in lag_days:
        names.append(f"demand_lag_{lag}")
    for window in rolling_windows:
        names.append(f"demand_rolling_{window}")
        names.append(f"demand_std_{window}")
    names.append("demand_momentum")
    return names


def _build_single_row(
    date_str: str,
    qty_arr: np.ndarray,
    idx: int,
    lag_days: tuple[int, ...],
    rolling_windows: tuple[int, ...],
) -> dict[str, float]:
    """为单个时间点构建特征行。"""
    from datetime import date as dt_date

    row: dict[str, float] = {}

    # Calendar features
    d = dt_date.fromisoformat(date_str)
    row["day_of_week"] = float(d.weekday())
    row["is_weekend"] = 1.0 if d.weekday() >= 5 else 0.0
    row["month"] = float(d.month)
    row["day_of_year"] = float(d.timetuple().tm_yday)

    # Lag features
    for lag in lag_days:
        row[f"demand_lag_{lag}"] = qty_arr[idx - lag]

    # Rolling features
    for window in rolling_windows:
        start = max(0, idx - window + 1)
        window_data = qty_arr[start:idx + 1]
        row[f"demand_rolling_{window}"] = float(np.mean(window_data))
        row[f"demand_std_{window}"] = float(np.std(window_data)) if len(window_data) > 1 else 0.0

    # Momentum
    if idx >= 7:
        row["demand_momentum"] = qty_arr[idx - 1] - qty_arr[idx - 7]
    else:
        row["demand_momentum"] = 0.0

    return row
