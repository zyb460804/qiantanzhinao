"""services/lifecycle.py calc_batch_status 的时区健壮性单元测试。

约定（V2-C2 utc_now 收口后）：DB 全部时间列为 naive UTC，utc_now() 也返回
naive UTC。calc_batch_status 的 purchase_date 契约 = naive UTC（列读回值，
SQLite/PG 均无 tzinfo），naive 减 naive 直接可算，不再做 tzinfo 补齐。

历史回归（HIGH）：aware utc_now() 减 naive purchase_date 曾抛 TypeError，
导致 /inventory/alerts 与 /twin/inventory-mirror 必 500。中间曾用
purchase_date.replace(tzinfo=UTC) 补齐（aware-aware），utc_now() 改回 naive
后该补齐反而会造成 naive 体系下的混比，已随收口移除。
"""

from datetime import UTC, datetime, timedelta

import pytest

from app.services import lifecycle as lifecycle_module
from app.services.lifecycle import calc_batch_status


def test_calc_batch_status_accepts_naive_purchase_date():
    """现实用例：DB 列读回的 naive UTC purchase_date，直接与 naive utc_now 相减。"""
    column_value = datetime.now(UTC).replace(tzinfo=None) - timedelta(hours=40)
    status = calc_batch_status("白菜", column_value, remaining_qty=8.0, purchase_qty=10.0)
    assert status["status"] == "attention"  # 叶菜类 36-54h


def test_calc_batch_status_stage_boundaries_deterministic(monkeypatch):
    """冻结时钟后阶段边界确定：40h → attention，60h → expiring（含折扣）。"""
    frozen = datetime(2026, 8, 15, 12, 0, 0)  # naive UTC，与 utc_now() 契约一致
    monkeypatch.setattr(lifecycle_module, "utc_now", lambda: frozen)

    at_40h = calc_batch_status("白菜", frozen - timedelta(hours=40), 8.0, 10.0)
    assert at_40h["status"] == "attention"

    at_60h = calc_batch_status("白菜", frozen - timedelta(hours=60), 8.0, 10.0)
    assert at_60h["status"] == "expiring"  # 叶菜类 54-72h
    assert at_60h["discount"] == 0.3


def test_calc_batch_status_rejects_aware_purchase_date():
    """契约锁定：aware 输入即调用方违约，必须 TypeError 而非静默错算。

    naive 体系下不做 replace(tzinfo=UTC) 补齐——上游（inventory/twin）传的
    都是列读回的 naive 值；若这里对 aware 静默容错，会掩盖写入端混入 aware
    的真问题（生产 PG 下正是 500 的根源）。
    """
    aware = datetime.now(UTC) - timedelta(hours=60)
    with pytest.raises(TypeError):
        calc_batch_status("白菜", aware, remaining_qty=8.0, purchase_qty=10.0)


def test_calc_batch_status_unknown_product_short_circuits():
    """未知品类不进入时间运算，返回 unknown。"""
    status = calc_batch_status("进口车厘子", datetime.now(), 1.0, 1.0)
    assert status["status"] == "unknown"
    assert status["hours_remaining"] is None
