"""
Unit tests for the experience-cloud differential-privacy layer.

Covers merchant-count bucketing (re-identification defence),
DP metadata attachment, and that Laplace noise is correctly
centred (mean ~0) with the expected scale.
"""

import math

from app.services import experience_cloud as ec


def test_bucket_merchants():
    """Rare and common counts are bucketed, never leaked exactly."""
    assert ec._bucket_merchants(2) == "<3"
    assert ec._bucket_merchants(3) == "3-5"
    assert ec._bucket_merchants(7) == "5-10"
    assert ec._bucket_merchants(15) == "10-20"
    assert ec._bucket_merchants(50) == "20+"


def test_apply_privacy_adds_metadata():
    """DP helper attaches bucket + privacy metadata and noises the value."""
    ins = {
        "product": "西瓜",
        "impact_pct": 35.0,
        "direction": "increase",
        "merchant_sample": 12,
    }
    out = ec._apply_privacy(ins, ["impact_pct"], sensitivity=5.0)

    # exact merchant count must be replaced by a bucket
    assert "merchant_bucket" in out
    assert out["merchant_sample"] == out["merchant_bucket"]
    assert out["merchant_bucket"] == "10-20"

    # privacy metadata present and references the configured epsilon
    assert out["privacy"]["epsilon"] == ec.PRIVACY_EPSILON
    assert out["privacy"]["mechanism"] == "laplace"
    assert isinstance(out["impact_pct"], float)


def test_laplace_noise_centred_and_scaled():
    """Laplace(0, sensitivity/epsilon) noise: mean ~0, std ~ scale."""
    vals = [ec._laplace_noise(1.0, 1.0) for _ in range(3000)]
    mean = sum(vals) / len(vals)
    assert abs(mean) < 0.2  # centred at 0

    variance = sum((v - mean) ** 2 for v in vals) / len(vals)
    std = math.sqrt(variance)
    # scale = 1/1 = 1 -> theoretical std = sqrt(2) ≈ 1.41
    assert 0.5 < std < 3.0


def test_query_budget_gate():
    """Per-key query budget caps total leakage."""
    key = "test_budget_gate_key"
    ec._query_counts[key] = 0
    ec.PRIVACY_QUERY_BUDGET = 3
    try:
        assert ec._check_budget(key) is True
        assert ec._check_budget(key) is True
        assert ec._check_budget(key) is True
        assert ec._check_budget(key) is False  # exceeded
    finally:
        ec.PRIVACY_QUERY_BUDGET = int(__import__("os").getenv("PRIVACY_QUERY_BUDGET", "100"))
