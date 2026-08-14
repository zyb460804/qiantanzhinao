"""services/lifecycle.py calc_batch_status 的时区健壮性单元测试。

回归（HIGH）：BatchLifecycle.purchase_date 是 naive DateTime，从 SQLite
读回无 tzinfo；calc_batch_status 曾直接 ``utc_now() - purchase_date``
（aware 减 naive）抛 TypeError，导致 /inventory/alerts 与
/twin/inventory-mirror 必 500。

约定（app/core/timezone.py）：服务端时间戳统一 UTC，naive 输入按 UTC 解释。
"""

from datetime import UTC, datetime, timedelta

from app.services.lifecycle import calc_batch_status


def test_calc_batch_status_accepts_naive_purchase_date():
    """naive purchase_date（SQLite 读回形态）按 UTC 解释，正常判定阶段。"""
    naive = datetime.now(UTC).replace(tzinfo=None) - timedelta(hours=40)
    status = calc_batch_status("白菜", naive, remaining_qty=8.0, purchase_qty=10.0)
    assert status["status"] == "attention"  # 叶菜类 36-54h


def test_calc_batch_status_accepts_aware_purchase_date():
    """aware purchase_date 与 utc_now() 直接可减，结果一致。"""
    aware = datetime.now(UTC) - timedelta(hours=60)
    status = calc_batch_status("白菜", aware, remaining_qty=8.0, purchase_qty=10.0)
    assert status["status"] == "expiring"  # 叶菜类 54-72h
    assert status["discount"] == 0.3


def test_calc_batch_status_naive_and_aware_agree():
    """同一时刻的 naive / aware 输入应判定到同一阶段。"""
    elapsed_hours = 5  # 豆腐（豆制品）fresh 档 0-12h
    base = datetime.now(UTC) - timedelta(hours=elapsed_hours)
    naive_status = calc_batch_status("豆腐", base.replace(tzinfo=None), 5.0, 10.0)
    aware_status = calc_batch_status("豆腐", base, 5.0, 10.0)
    assert naive_status["status"] == aware_status["status"] == "fresh"


def test_calc_batch_status_unknown_product_short_circuits():
    """未知品类不进入时间运算，返回 unknown。"""
    status = calc_batch_status("进口车厘子", datetime.now(), 1.0, 1.0)
    assert status["status"] == "unknown"
    assert status["hours_remaining"] is None
