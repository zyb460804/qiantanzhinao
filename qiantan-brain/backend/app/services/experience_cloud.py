"""
千摊经验云 — Anonymous Cross-Merchant Knowledge Aggregation.
Aggregates de-identified operating patterns across merchants to build
community knowledge: "20 merchants show watermelon sales +35% in hot weather."

Privacy: Statistical aggregation only — no raw individual data leaves the server.
Design: Prepared for future federated learning upgrade.
"""

import logging
import math
import os
import random

from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.timezone import local_days_ago
from app.models.environment import EnvironmentRecord
from app.models.inventory import InventoryRecord
from app.models.product import ProductCategory


logger = logging.getLogger(__name__)

# Minimum merchant sample size to publish an insight
MIN_MERCHANT_SAMPLE = 3

# ── Differential Privacy (experience cloud) ─────────────────────
# Epsilon controls the privacy budget: smaller = stronger privacy.
PRIVACY_EPSILON = float(getattr(settings, "privacy_epsilon", 1.0))
PRIVACY_QUERY_BUDGET = int(os.getenv("PRIVACY_QUERY_BUDGET", "100"))

# In-memory per-key query budget (resets on restart; demo-grade).
_query_counts: dict = {}


def _laplace_noise(sensitivity: float, epsilon: float) -> float:
    """Sample Laplace(0, sensitivity/epsilon) noise."""
    if epsilon <= 0:
        return 0.0
    scale = sensitivity / epsilon
    u = random.random() - 0.5
    if u == 0:
        return 0.0
    return -scale * math.copysign(1.0, u) * math.log(1.0 - 2.0 * abs(u))


def _bucket_merchants(n: int) -> str:
    """Bucket merchant counts to prevent re-identification via rare counts."""
    if n < 3:
        return "<3"
    if n < 5:
        return "3-5"
    if n < 10:
        return "5-10"
    if n < 20:
        return "10-20"
    return "20+"


def _check_budget(key: str) -> bool:
    """Gate total information leakage by capping queries per key."""
    _query_counts[key] = _query_counts.get(key, 0) + 1
    return _query_counts[key] <= PRIVACY_QUERY_BUDGET


def _dp_value(value, sensitivity: float = 1.0, epsilon: float | None = None) -> float | None:
    """Add Laplace noise to a numeric aggregate."""
    if value is None:
        return None
    eps = PRIVACY_EPSILON if epsilon is None else epsilon
    return round(float(value) + _laplace_noise(sensitivity, eps), 2)


def _apply_privacy(insight: dict, value_keys: list, sensitivity: float = 1.0) -> dict:
    """Apply DP to an insight: noise numeric values, bucket merchant count, log."""
    for k in value_keys:
        if insight.get(k) is not None:
            insight[k] = _dp_value(insight[k], sensitivity)
    n = insight.get("merchant_sample")
    if n is not None:
        insight["merchant_bucket"] = _bucket_merchants(int(n))
        insight["merchant_sample"] = insight["merchant_bucket"]  # never leak exact count
    insight["privacy"] = {
        "epsilon": PRIVACY_EPSILON,
        "mechanism": "laplace",
        "min_sample": MIN_MERCHANT_SAMPLE,
    }
    return insight


async def get_weather_impact_rules(
    db: AsyncSession,
    product_name: str | None = None,
) -> list[dict]:
    """Aggregate weather impact patterns across all merchants.

    Returns insights like:
    "气温 >30°C 时, 西瓜销量平均 +32% (基于 12 家商户数据)"
    """
    # This is a statistical query that correlates sales with temperature
    # For MVP, we return predefined rules from config.
    # In production, this runs on real aggregated data.

    # Attempt to compute from actual data
    try:
        thirty_days_ago = local_days_ago(30)

        # Join inventory with environment to correlate
        corr_query = (
            select(
                ProductCategory.name,
                func.avg(
                    case(
                        (EnvironmentRecord.temp_high > 28, func.abs(InventoryRecord.quantity)),
                    )
                ).label("hot_day_avg"),
                func.avg(
                    case(
                        (EnvironmentRecord.temp_high <= 28, func.abs(InventoryRecord.quantity)),
                    )
                ).label("normal_day_avg"),
                func.count(func.distinct(InventoryRecord.merchant_id)).label("merchant_count"),
            )
            .join(ProductCategory, InventoryRecord.product_id == ProductCategory.id)
            .join(
                EnvironmentRecord,
                func.date(InventoryRecord.event_time) == EnvironmentRecord.date,
            )
            .where(
                InventoryRecord.event_type == "sale",
                InventoryRecord.event_time >= thirty_days_ago,
            )
            .group_by(ProductCategory.name)
            .having(func.count(func.distinct(InventoryRecord.merchant_id)) >= MIN_MERCHANT_SAMPLE)
        )

        if product_name:
            corr_query = corr_query.where(ProductCategory.name == product_name)

        result = await db.execute(corr_query)
        rows = result.all()

        insights = []
        for row in rows:
            hot = float(row.hot_day_avg or 0)
            normal = float(row.normal_day_avg or 0)
            if normal > 0 and hot > 0:
                ratio = round((hot - normal) / normal * 100, 0)
                if abs(ratio) > 5:  # Only report significant impacts
                    insights.append(
                        {
                            "product": row.name,
                            "condition": "高温 (>28°C)",
                            "impact_pct": ratio,
                            "direction": "increase" if ratio > 0 else "decrease",
                            "merchant_sample": row.merchant_count,
                            "message": (
                                f"气温 >28°C 时, {row.name}销量"
                                f"{'增加' if ratio > 0 else '减少'}{abs(ratio):.0f}% "
                                f"(基于{row.merchant_count}家商户)"
                            ),
                        }
                    )

        if insights:
            if not _check_budget("weather_impact_rules"):
                logger.warning("experience_cloud: query budget exceeded for weather rules")
                return []
            for ins in insights:
                _apply_privacy(ins, ["impact_pct"], sensitivity=5.0)
                ins["message"] = (
                    f"气温 >28°C 时, {ins['product']}销量"
                    f"{'增加' if ins['direction'] == 'increase' else '减少'}"
                    f"{abs(ins['impact_pct']):.0f}% (基于{ins['merchant_bucket']}家商户匿名聚合)"
                )
                logger.info(
                    "experience_cloud: published weather rule product=%s bucket=%s",
                    ins["product"],
                    ins["merchant_bucket"],
                )
            return insights
    except Exception:
        logger.warning("Experience cloud query failed, falling back to rules", exc_info=True)

    # Fallback: return predefined rules from env_coefficients.json
    return _fallback_rules(product_name)


async def get_category_benchmarks(db: AsyncSession) -> list[dict]:
    """Get benchmarking data: average daily sales volume by product category."""
    thirty_days_ago = local_days_ago(30)

    try:
        query = (
            select(
                ProductCategory.name,
                ProductCategory.category_group,
                func.avg(func.abs(InventoryRecord.quantity)).label("avg_daily_qty"),
                func.count(func.distinct(InventoryRecord.merchant_id)).label("merchant_count"),
            )
            .join(ProductCategory, InventoryRecord.product_id == ProductCategory.id)
            .where(
                InventoryRecord.event_type == "sale",
                InventoryRecord.event_time >= thirty_days_ago,
            )
            .group_by(ProductCategory.name, ProductCategory.category_group)
            .having(func.count(func.distinct(InventoryRecord.merchant_id)) >= MIN_MERCHANT_SAMPLE)
        )

        result = await db.execute(query)
        rows = result.all()

        rows_out = []
        for row in rows:
            if not _check_budget("category_benchmarks"):
                logger.warning("experience_cloud: query budget exceeded for benchmarks")
                break
            d = {
                "product": row.name,
                "category": row.category_group,
                "avg_daily_sales": round(float(row.avg_daily_qty or 0), 1),
                "merchant_sample": row.merchant_count,
            }
            _apply_privacy(d, ["avg_daily_sales"], sensitivity=2.0)
            rows_out.append(d)
        return rows_out
    except Exception:
        return []


async def get_top_products(db: AsyncSession, limit: int = 5) -> list[dict]:
    """Get most frequently traded products across all merchants."""
    thirty_days_ago = local_days_ago(30)

    try:
        query = (
            select(
                ProductCategory.name,
                func.sum(func.abs(InventoryRecord.quantity)).label("total_volume"),
                func.count(func.distinct(InventoryRecord.merchant_id)).label("merchant_count"),
            )
            .join(ProductCategory, InventoryRecord.product_id == ProductCategory.id)
            .where(
                InventoryRecord.event_type == "sale",
                InventoryRecord.event_time >= thirty_days_ago,
            )
            .group_by(ProductCategory.name)
            .order_by(func.sum(func.abs(InventoryRecord.quantity)).desc())
            .limit(limit)
        )

        result = await db.execute(query)
        rows = result.all()

        rows_out = []
        for row in rows:
            if not _check_budget("top_products"):
                logger.warning("experience_cloud: query budget exceeded for top products")
                break
            d = {
                "product": row.name,
                "total_volume": round(float(row.total_volume or 0), 1),
                "merchant_sample": row.merchant_count,
            }
            _apply_privacy(d, ["total_volume"], sensitivity=2.0)
            rows_out.append(d)
        return rows_out
    except Exception:
        return []


def _fallback_rules(product_name: str | None = None) -> list[dict]:
    """Predefined environmental impact rules (from domain knowledge)."""
    all_rules = [
        {
            "product": "西瓜",
            "condition": "高温 (>30°C)",
            "impact_pct": 35,
            "direction": "increase",
            "merchant_sample": 20,
            "message": "高温天气西瓜销量平均增加35% (基于文献数据)",
        },
        {
            "product": "豆腐",
            "condition": "高温 (>30°C)",
            "impact_pct": -15,
            "direction": "decrease",
            "merchant_sample": 18,
            "message": "高温天气豆制品销量减少15% (基于行业经验)",
        },
        {
            "product": "白菜",
            "condition": "降雨 (>50%)",
            "impact_pct": -20,
            "direction": "decrease",
            "merchant_sample": 15,
            "message": "大雨天客流减少，叶菜销量下降约20%",
        },
    ]
    if product_name:
        return [r for r in all_rules if r["product"] == product_name]
    return all_rules
