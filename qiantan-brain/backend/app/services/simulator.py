"""
Decision simulation sandbox engine.
Supports What-if analysis: "If I buy X quantity at Y cost and sell at Z price..."
"""

import json
from pathlib import Path


_RULES_DIR = Path(__file__).parent.parent / "rules"


def _load_lifecycle_rules() -> dict:
    config_path = _RULES_DIR / "lifecycle_rules.json"
    if config_path.exists():
        with open(config_path, encoding="utf-8") as f:
            return json.load(f)
    return {}


def _get_product_info(product_name: str) -> dict:
    """Get product lifecycle info from rules."""
    rules = _load_lifecycle_rules()
    for group_name, group_data in rules.get("categories", {}).items():
        if product_name in group_data.get("products", []):
            return {
                "group": group_name,
                "shelf_life_hours": group_data.get("shelf_life_hours", 72),
                "waste_rate_base": group_data.get("waste_rate_base", 0.15),
            }
    return {
        "group": "other",
        "shelf_life_hours": 72,
        "waste_rate_base": 0.15,
    }


def simulate_what_if(
    purchase_qty: float,
    unit_cost: float,
    unit_price: float,
    product_name: str,
    estimated_sales_base: float,
    avg_historical_price: float | None = None,
) -> dict:
    """
    Run a What-if simulation for purchasing decisions.

    Args:
        purchase_qty: Planned purchase quantity.
        unit_cost: Purchase cost per unit.
        unit_price: Planned selling price per unit.
        product_name: Product category name.
        estimated_sales_base: Baseline predicted sales from env engine.
        avg_historical_price: Historical average selling price for elasticity.

    Returns:
        Dict with simulation output and comparison.
    """
    product_info = _get_product_info(product_name)

    # Step 2: Price elasticity adjustment
    if avg_historical_price and avg_historical_price > 0:
        price_ratio = unit_price / avg_historical_price
        if price_ratio < 0.9:  # Discount
            sales_mult = 1 + (1 - price_ratio) * 1.5
        elif price_ratio > 1.1:  # Markup
            sales_mult = max(0.7, 1 - (price_ratio - 1) * 1.0)
        else:
            sales_mult = 1.0
    else:
        sales_mult = 1.0

    est_sales = min(estimated_sales_base * sales_mult, purchase_qty)
    est_sales = round(est_sales, 1)

    # Step 3: Waste calculation
    waste_prob = 0.95 if product_info["shelf_life_hours"] <= 24 else 0.85
    waste_qty = max(0, purchase_qty - est_sales) * waste_prob
    waste_qty = round(waste_qty, 1)

    # Step 4: Profit calculation
    revenue = est_sales * unit_price
    cost = purchase_qty * unit_cost
    waste_loss = waste_qty * unit_cost * 0.5
    net_profit = revenue - cost - waste_loss
    margin_rate = round(net_profit / cost, 4) if cost > 0 else 0.0
    waste_rate = round(waste_qty / purchase_qty, 2) if purchase_qty > 0 else 0.0

    output = {
        "estimated_sales": est_sales,
        "estimated_revenue": round(revenue, 2),
        "total_cost": round(cost, 2),
        "waste_qty": waste_qty,
        "waste_loss": round(waste_loss, 2),
        "net_profit": round(net_profit, 2),
        "margin_rate": margin_rate,
        "waste_rate": waste_rate,
    }

    # Step 5: Compare with baseline (buying exactly est_sales_base)
    baseline_qty = estimated_sales_base
    baseline_est_sales = min(estimated_sales_base * sales_mult, baseline_qty)
    baseline_waste = max(0, baseline_qty - baseline_est_sales) * waste_prob
    baseline_profit = (
        baseline_est_sales * unit_price
        - baseline_qty * unit_cost
        - baseline_waste * unit_cost * 0.5
    )

    improvement = round(net_profit - baseline_profit, 2)
    if improvement > 0.5:
        verdict = "有利：模拟方案优于基准方案"
    elif improvement < -0.5:
        verdict = "不利：基准方案更优"
    else:
        verdict = "持平：两种方案收益接近"

    # Generate recommendation text
    if waste_rate > 0.3:
        rec = (
            f"预计净收益{net_profit:.0f}元，但损耗率{waste_rate * 100:.0f}%偏高，"
            "建议控制进货量以降低损耗风险"
        )
    elif waste_rate > 0.15:
        rec = f"预计净收益{net_profit:.0f}元，损耗率{waste_rate * 100:.0f}%在可接受范围内"
    else:
        rec = f"预计净收益{net_profit:.0f}元，损耗控制良好"

    return {
        "input": {
            "purchase_qty": purchase_qty,
            "unit_cost": unit_cost,
            "unit_price": unit_price,
        },
        "output": output,
        "comparison": {
            "baseline_net_profit": round(baseline_profit, 2),
            "improvement": improvement,
            "verdict": verdict,
            "recommendation": rec,
        },
    }
