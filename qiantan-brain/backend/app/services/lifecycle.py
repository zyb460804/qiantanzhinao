"""
Product lifecycle management service.
Tracks batch freshness and generates expiry alerts.
"""

import json
from datetime import datetime
from pathlib import Path

from app.core.timezone import utc_now


_RULES_DIR = Path(__file__).parent.parent / "rules"


def _load_lifecycle_rules() -> dict:
    config_path = _RULES_DIR / "lifecycle_rules.json"
    if config_path.exists():
        with open(config_path, encoding="utf-8") as f:
            return json.load(f)
    return {}


def get_product_lifecycle(product_name: str) -> dict | None:
    """Get lifecycle rules for a product."""
    rules = _load_lifecycle_rules()
    for group_data in rules.get("categories", {}).values():
        if product_name in group_data.get("products", []):
            return group_data
    return None


def calc_batch_status(
    product_name: str,
    purchase_date: datetime,
    remaining_qty: float,
    purchase_qty: float,
) -> dict:
    """
    Calculate the current lifecycle status of a batch.

    Returns dict with status, color, discount suggestion, and hours remaining.
    """
    lifecycle = get_product_lifecycle(product_name)
    if not lifecycle:
        return {
            "status": "unknown",
            "color": "gray",
            "discount": 0,
            "hours_remaining": None,
            "message": "未知品类",
        }

    hours_elapsed = (utc_now() - purchase_date).total_seconds() / 3600
    stages = lifecycle.get("lifecycle_stages", {})

    for stage_name, stage_data in stages.items():
        hours_from = stage_data.get("hours_from", 0)
        hours_to = stage_data.get("hours_to", -1)

        if hours_to == -1:  # Last stage (spoiled)
            if hours_elapsed >= hours_from:
                return {
                    "status": stage_name,
                    "color": stage_data["color"],
                    "discount": stage_data.get("discount", 0),
                    "hours_remaining": -1,
                    "message": stage_data.get("action", "建议废弃处理"),
                }
        elif hours_from <= hours_elapsed < hours_to:
            hours_remaining = hours_to - hours_elapsed
            remaining_ratio = remaining_qty / purchase_qty if purchase_qty > 0 else 0

            return {
                "status": stage_name,
                "color": stage_data["color"],
                "discount": stage_data.get("discount", 0),
                "hours_remaining": round(hours_remaining, 1),
                "remaining_ratio": round(remaining_ratio, 2),
                "message": _generate_stage_message(
                    stage_name, hours_remaining, remaining_ratio, stage_data
                ),
            }

    return {"status": "fresh", "color": "green", "discount": 0, "hours_remaining": 72}


def _generate_stage_message(
    stage: str, hours_remaining: float, remaining_ratio: float, stage_data: dict
) -> str:
    """Generate a human-readable message for the lifecycle stage."""
    hours = round(hours_remaining, 0)
    pct = round(remaining_ratio * 100)

    if stage == "attention":
        return f"建议优先销售，剩余约{hours}小时，当前库存{pct}%"
    elif stage == "expiring":
        discount = stage_data.get("discount", 0)
        return f"⚠️ 临期！建议{discount * 100:.0f}%折扣促销，剩余约{hours}小时"
    return ""
