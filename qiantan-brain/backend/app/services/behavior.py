"""经营行为学习引擎 — 跟踪建议采纳并动态调整商户采购画像。"""

from __future__ import annotations

import logging
import uuid
from typing import Any, TypedDict

from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.timezone import utc_days_ago
from app.models.preference import MerchantPreference
from app.models.recommendation import Recommendation


logger = logging.getLogger(__name__)


class ProfileDefinition(TypedDict):
    label: str
    adopt_rate_threshold: float
    quantity_multiplier: float
    description: str


PROFILES: dict[str, ProfileDefinition] = {
    "conservative": {
        "label": "保守型",
        "adopt_rate_threshold": 0.85,
        "quantity_multiplier": 0.85,
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
        "quantity_multiplier": 1.20,
        "description": "倾向于一次多进，愿意承担风险",
    },
}


def classify_purchase_style(adoption_rate: float, avg_deviation: float) -> str:
    """Classify merchant purchase style from adoption and quantity deviation."""
    if adoption_rate > 0.8 and avg_deviation < -0.1:
        return "conservative"
    if adoption_rate < 0.5 or avg_deviation > 0.15:
        return "aggressive"
    return "balanced"


def _purchase_style(data: dict[str, Any]) -> str:
    value = data.get("purchase_style")
    return value if isinstance(value, str) and value in PROFILES else "balanced"


def _non_negative_int(value: Any) -> int:
    try:
        return max(int(value), 0)
    except (TypeError, ValueError):
        return 0


def _optional_rate(value: Any) -> float | None:
    if value is None:
        return None
    try:
        rate = float(value)
    except (TypeError, ValueError):
        return None
    return min(max(rate, 0.0), 1.0)


async def record_adoption(
    db: AsyncSession,
    merchant_id: uuid.UUID,
    recommendation_id: uuid.UUID,
    was_adopted: bool,
    actual_quantity: float | None = None,
) -> dict[str, Any]:
    """Record feedback for one merchant-owned recommendation and refresh its profile."""
    recommendation = await db.scalar(
        select(Recommendation).where(
            Recommendation.id == recommendation_id,
            Recommendation.merchant_id == merchant_id,
        )
    )
    if recommendation is None:
        raise LookupError("建议不存在或不属于当前商户")

    recommendation.was_adopted = was_adopted
    if (
        actual_quantity is not None
        and recommendation.recommended_qty is not None
        and recommendation.recommended_qty > 0
    ):
        recommended_quantity = float(recommendation.recommended_qty)
        recommendation.actual_deviation = round(
            (actual_quantity - recommended_quantity) / recommended_quantity,
            3,
        )

    preference = await db.scalar(
        select(MerchantPreference).where(MerchantPreference.merchant_id == merchant_id)
    )
    if preference is None:
        preference = MerchantPreference(
            merchant_id=merchant_id,
            risk_profile="neutral",
            preference_data={},
        )
        db.add(preference)

    thirty_days_ago = utc_days_ago(30)
    stats_query = select(
        func.count(Recommendation.id),
        func.sum(case((Recommendation.was_adopted.is_(True), 1), else_=0)),
        func.avg(Recommendation.actual_deviation),
    ).where(
        Recommendation.merchant_id == merchant_id,
        Recommendation.created_at >= thirty_days_ago,
        Recommendation.was_adopted.is_not(None),
    )
    total_raw, adopted_raw, avg_deviation_raw = (await db.execute(stats_query)).one()

    total = int(total_raw or 0)
    adopted = int(adopted_raw or 0)
    corrections = max(total - adopted, 0)
    adoption_rate = adopted / total if total else None
    correction_rate = corrections / total if total else None
    avg_deviation = float(avg_deviation_raw or 0)

    behavior_data: dict[str, Any] = dict(preference.preference_data or {})
    purchase_style = _purchase_style(behavior_data)
    if total >= 5 and adoption_rate is not None:
        purchase_style = classify_purchase_style(adoption_rate, avg_deviation)

    behavior_data.update(
        {
            "purchase_style": purchase_style,
            "avg_adoption_rate": round(adoption_rate, 3) if adoption_rate is not None else None,
            "correction_rate": round(correction_rate, 3) if correction_rate is not None else None,
            "total_decisions": total,
            "total_corrections": corrections,
        }
    )
    preference.preference_data = behavior_data
    await db.commit()

    profile = PROFILES[purchase_style]
    return {
        "purchase_style": purchase_style,
        "profile_label": profile["label"],
        "recommended_multiplier": profile["quantity_multiplier"],
        "total_decisions_recorded": total,
    }


async def get_merchant_profile(
    db: AsyncSession,
    merchant_id: uuid.UUID,
) -> dict[str, Any]:
    """Get the persisted behavioral profile used to personalize recommendations."""
    preference = await db.scalar(
        select(MerchantPreference).where(MerchantPreference.merchant_id == merchant_id)
    )
    if preference is None:
        return {
            "purchase_style": "balanced",
            "profile_label": "均衡型（默认）",
            "quantity_multiplier": 1.0,
            "total_decisions": 0,
            "adoption_rate": None,
            "correction_rate": None,
            "risk_profile": "neutral",
        }

    behavior_data: dict[str, Any] = dict(preference.preference_data or {})
    purchase_style = _purchase_style(behavior_data)
    profile = PROFILES[purchase_style]
    return {
        "purchase_style": purchase_style,
        "profile_label": profile["label"],
        "quantity_multiplier": profile["quantity_multiplier"],
        "total_decisions": _non_negative_int(behavior_data.get("total_decisions")),
        "adoption_rate": _optional_rate(behavior_data.get("avg_adoption_rate")),
        "correction_rate": _optional_rate(behavior_data.get("correction_rate")),
        "risk_profile": preference.risk_profile,
    }


def personalize_recommendation(raw_recommended_qty: float, profile: dict[str, Any]) -> float:
    """Apply the learned quantity multiplier to a raw recommendation."""
    multiplier = float(profile.get("quantity_multiplier", 1.0))
    return round(raw_recommended_qty * multiplier, 1)
