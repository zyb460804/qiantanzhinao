"""
经营行为模型 — Merchant Behavior Learning Engine.
Tracks adoption patterns, learns preferences, adapts recommendations over time.

Phase 1: Rule-based preference tracking with feedback loop.
Phase 2: ML-driven personalization (when enough data accumulates).
"""

import logging

from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.timezone import utc_days_ago
from app.models.preference import MerchantPreference
from app.models.recommendation import Recommendation


logger = logging.getLogger(__name__)

# ── Preference Profiles ─────────────────────────────────────────────────

PROFILES = {
    "conservative": {
        "label": "保守型",
        "adopt_rate_threshold": 0.85,  # Only adopts high-confidence recs
        "quantity_multiplier": 0.85,  # Buys 85% of recommended qty
        "description": "倾向于少量多频次采购",
    },
    "balanced": {
        "label": "均衡型",
        "adopt_rate_threshold": 0.65,
        "quantity_multiplier": 1.00,
        "description": "大部分采纳建议，偶尔调整",
    },
    "aggressive": {
        "label": "进取型",
        "adopt_rate_threshold": 0.50,
        "quantity_multiplier": 1.20,  # Buys 20% more than recommended
        "description": "倾向于一次多进，愿意承担风险",
    },
}


def classify_purchase_style(adoption_rate: float, avg_deviation: float) -> str:
    """Classify merchant purchase style from behavioral data.

    adoption_rate: fraction of recommendations adopted (0-1)
    avg_deviation: average % difference between recommended and actual qty
                   positive = bought more, negative = bought less
    """
    if adoption_rate > 0.8 and avg_deviation < -0.1:
        return "conservative"
    elif adoption_rate < 0.5 or avg_deviation > 0.15:
        return "aggressive"
    return "balanced"


async def record_adoption(
    db: AsyncSession,
    merchant_id,
    recommendation_id,
    was_adopted: bool,
    actual_quantity: float | None = None,
) -> dict:
    """Record whether a merchant adopted a recommendation and with what deviation."""

    # 1. Update the recommendation record
    query = select(Recommendation).where(Recommendation.id == recommendation_id)
    result = await db.execute(query)
    rec = result.scalar_one_or_none()
    if rec:
        rec.was_adopted = was_adopted
        if actual_quantity is not None and rec.recommended_qty and rec.recommended_qty > 0:
            rec.actual_deviation = round(
                (actual_quantity - rec.recommended_qty) / rec.recommended_qty, 3
            )
        await db.commit()

    # 2. Update merchant preference profile
    pref_query = select(MerchantPreference).where(MerchantPreference.merchant_id == merchant_id)
    pref_result = await db.execute(pref_query)
    pref = pref_result.scalar_one_or_none()

    if not pref:
        pref = MerchantPreference(
            merchant_id=merchant_id,
            risk_profile="neutral",
            purchase_style="balanced",
        )
        db.add(pref)

    # Recompute adoption rate from last 30 days
    thirty_days_ago = utc_days_ago(30)
    stats_query = select(
        func.count(Recommendation.id).label("total"),
        func.sum(case((Recommendation.was_adopted, 1), else_=0)).label("adopted"),
        func.avg(
            case(
                (Recommendation.actual_deviation.isnot(None), Recommendation.actual_deviation),
                else_=0,
            )
        ).label("avg_dev"),
    ).where(
        Recommendation.merchant_id == merchant_id,
        Recommendation.created_at >= thirty_days_ago,
    )
    stats_result = await db.execute(stats_query)
    stats = stats_result.one()

    total = stats.total or 0
    adopted = stats.adopted or 0
    avg_dev = float(stats.avg_dev or 0)

    if total >= 5:  # Only reclassify with enough data
        adoption_rate = adopted / total
        pref.purchase_style = classify_purchase_style(adoption_rate, avg_dev)
        pref.avg_adoption_rate = round(adoption_rate, 3)

    pref.total_voice_logs = (pref.total_voice_logs or 0) + 1
    if not was_adopted:
        pref.total_corrections = (pref.total_corrections or 0) + 1

    await db.commit()

    profile = PROFILES.get(pref.purchase_style, PROFILES["balanced"])
    return {
        "purchase_style": pref.purchase_style,
        "profile_label": profile["label"],
        "recommended_multiplier": profile["quantity_multiplier"],
        "total_decisions_recorded": total + 1,
    }


async def get_merchant_profile(db: AsyncSession, merchant_id) -> dict:
    """Get merchant's behavioral profile for personalization."""
    query = select(MerchantPreference).where(MerchantPreference.merchant_id == merchant_id)
    result = await db.execute(query)
    pref = result.scalar_one_or_none()

    if not pref:
        return {
            "purchase_style": "balanced",
            "profile_label": "均衡型（默认）",
            "quantity_multiplier": 1.0,
            "total_decisions": 0,
            "adoption_rate": None,
        }

    profile = PROFILES.get(pref.purchase_style, PROFILES["balanced"])
    return {
        "purchase_style": pref.purchase_style,
        "profile_label": profile["label"],
        "quantity_multiplier": profile["quantity_multiplier"],
        "total_decisions": (pref.total_voice_logs or 0),
        "adoption_rate": float(pref.avg_adoption_rate) if pref.avg_adoption_rate else None,
        "risk_profile": pref.risk_profile,
    }


def personalize_recommendation(raw_recommended_qty: float, profile: dict) -> float:
    """Apply personalization multiplier to raw recommendation.

    Conservative merchants get a lower recommendation (safer),
    aggressive merchants get a higher one.
    """
    mult = profile.get("quantity_multiplier", 1.0)
    return round(raw_recommended_qty * mult, 1)
