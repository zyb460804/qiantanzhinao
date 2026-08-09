"""Anomaly detector router tests — 对抗性测试：锁定 check/scan 端点真实行为。

设计原则（第一性原理 + 对抗性）：
  1. 每个测试必须验证【语义】而非【结构】—— 断言具体的 type/severity/value，
     使任何错误实现都无法伪装成绿色假象。
  2. 覆盖每种检测器（ZERO_SALES / DATA_ERROR / SPIKE）的真实触发路径，
     用真正能触发该检测器的输入，而非"看起来在测它"的输入。
  3. scan 端点验证从真实 InventoryRecord 聚合 → 补齐缺失日期 → 检出的完整链路。

历史教训：anomaly_detector.py 曾序列化字段与 AnomalySignal 数据类不一致
（description/value/threshold 不存在），检测到异常即 500。早期测试只断言
"key 存在"或 isinstance(list)（恒真条件），即使字段名写错或检测器被删除也照绿
—— 典型的绿色假象。本文件用语义断言锁死。
"""

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from tests.conftest import TEST_MERCHANT_ID  # noqa: F401

from app.models.inventory import InventoryRecord


class TestAnomalyCheck:
    """POST /api/v1/anomalies/check — 算法演示端点。"""

    async def test_check_detects_zero_sales_with_semantic_assertions(self, client):
        """连续零销 → 必须检出 ZERO_SALES 信号，且字段语义正确。

        对抗点：history 末尾必须连续 ≤0，否则 _zero_sales_detect 在首个正值处
        break、consecutive_zeros=0，信号不触发。早期测试用 [12,10,11,13,9]
        （末位 9）根本不触发此检测器，却声称在测零销。本测试末段连续 0 + current=0。
        """
        res = await client.post(
            "/api/v1/anomalies/check",
            json={
                "history": [12, 10, 11, 13, 0, 0],  # 末两位连续 0
                "current_value": 0,  # 当前也为 0 → total_zeros=3 ≥ 3
                "product_name": "白菜",
            },
        )
        assert res.status_code == 200
        body = res.json()
        assert body["code"] == 0

        data = body["data"]
        assert data["total_signals"] > 0
        assert "zero_sales" in data["by_type"]

        zero_sigs = [s for s in data["signals"] if s["type"] == "zero_sales"]
        assert len(zero_sigs) >= 1

        sig = zero_sigs[0]
        # 语义断言（非恒真）：锁定字段值而非仅 key 存在
        assert sig["actual_value"] == 0
        assert sig["product_name"] == "白菜"
        assert sig["severity"] in ("medium", "high", "critical")
        assert "连续" in sig["details"]
        assert "忘记记账" in sig["details"]
        assert sig["detector"] == "zero_sales"
        assert sig["deviation"] == 1.0

    async def test_check_detects_data_error_magnitude(self, client):
        """数量级录入错误（current 远超历史 10 倍）→ 检出 DATA_ERROR。"""
        res = await client.post(
            "/api/v1/anomalies/check",
            json={
                "history": [10, 11, 9, 12, 10],  # 均值 ≈ 10.4
                "current_value": 500,  # 约 48 倍 → ratio > 10
                "product_name": "土豆",
            },
        )
        assert res.status_code == 200
        data = res.json()["data"]
        assert "data_error" in data["by_type"]

        err_sigs = [s for s in data["signals"] if s["type"] == "data_error"]
        assert len(err_sigs) == 1
        assert err_sigs[0]["actual_value"] == 500
        assert "数量级" in err_sigs[0]["details"]

    async def test_check_detects_spike(self, client):
        """突发峰值 → 检出 SPIKE（zscore/modified_zscore/iqr/moving_avg 共识）。"""
        res = await client.post(
            "/api/v1/anomalies/check",
            json={
                "history": [10, 11, 9, 10, 12, 11, 10],  # 稳定在 ~10
                "current_value": 50,  # 远高于均值
                "product_name": "豆腐",
            },
        )
        assert res.status_code == 200
        data = res.json()["data"]
        assert data["total_signals"] > 0
        assert "spike" in data["by_type"]

        spike = next(s for s in data["signals"] if s["type"] == "spike")
        assert spike["actual_value"] == 50
        assert spike["deviation"] > 1  # 偏离比例 > 100%

    async def test_check_no_anomaly_returns_empty(self, client):
        """稳定序列 + 正常当前值 → total_signals 必须为 0，signals 必须为空。

        对抗点：早期测试只断言 isinstance(signals, list) —— 恒真条件，即使把
        检测器改成"永远返回假信号"也照绿。本测试锁死空结果。
        """
        res = await client.post(
            "/api/v1/anomalies/check",
            json={
                "history": [10, 11, 9, 10, 12],
                "current_value": 11,  # 在正常范围内
                "product_name": "土豆",
            },
        )
        assert res.status_code == 200
        data = res.json()["data"]
        assert data["total_signals"] == 0
        assert data["signals"] == []
        assert data["by_type"] == {}
        assert "正常" in data["summary"]

    async def test_check_short_history_returns_no_signals(self, client):
        """数据点不足（< min_data_points=5）→ 返回空，不崩。

        对抗点：Pydantic 仅要求 min_length=1，但检测器要求 ≥5 点。确保短序列
        走"数据不足"分支而非抛异常。
        """
        res = await client.post(
            "/api/v1/anomalies/check",
            json={
                "history": [10],  # 仅 1 个点
                "current_value": 999,  # 极端值也不应触发
                "product_name": "新商品",
            },
        )
        assert res.status_code == 200
        data = res.json()["data"]
        assert data["total_signals"] == 0

    async def test_check_requires_auth(self, auth_client):
        """未认证 → 401。"""
        res = await auth_client.post(
            "/api/v1/anomalies/check",
            json={"history": [1, 2, 3], "current_value": 0},
        )
        assert res.status_code == 401


class TestAnomalyScan:
    """GET /api/v1/anomalies/scan — 扫描商户全部 SKU 销量异常。"""

    async def test_scan_empty_merchant_returns_zero(self, client):
        """无销量数据 → scanned==0, anomalies==[]。

        对抗点：早期测试只断言 key 存在 —— 路由返回 {"scanned":"WRONG"} 也照绿。
        本测试锁死具体值。
        """
        res = await client.get("/api/v1/anomalies/scan?days=30")
        assert res.status_code == 200
        data = res.json()["data"]
        assert data["scanned"] == 0
        assert data["anomalies"] == []

    async def test_scan_detects_zero_sales_from_real_records(self, client, db_session):
        """scan 从真实 InventoryRecord 聚合：连续零销商品必须被检出。

        端到端链路：DB sale 记录 → 按天聚合 → 补齐缺失日期(0) → 检测器。
        商品A 连续 7 天有销量（正常基准，不应有异常）；
        商品B 仅前 3 天有 sale 记录，后 4 天无 → 补 0 后序列末段连续零 →
        _zero_sales_detect 触发。
        """
        mid = uuid.UUID(TEST_MERCHANT_ID)
        now = datetime.now(UTC).replace(tzinfo=None)
        async with db_session() as session:
            records = []
            # 商品1（白菜 id=1）：连续 7 天每天 sale 10（稳定基准）
            for i in range(7):
                records.append(
                    InventoryRecord(
                        merchant_id=mid,
                        product_id=1,
                        quantity=Decimal("10"),
                        unit="斤",
                        event_type="sale",
                        event_time=now - timedelta(days=6 - i),
                        source="manual",
                    )
                )
            # 商品2（土豆 id=2）：仅前 3 天有 sale，后 4 天无记录 → 补 0
            for i in range(3):
                records.append(
                    InventoryRecord(
                        merchant_id=mid,
                        product_id=2,
                        quantity=Decimal("10"),
                        unit="斤",
                        event_type="sale",
                        event_time=now - timedelta(days=6 - i),
                        source="manual",
                    )
                )
            session.add_all(records)
            await session.commit()

        res = await client.get("/api/v1/anomalies/scan?days=30")
        assert res.status_code == 200
        data = res.json()["data"]
        assert data["scanned"] >= 1

        # 土豆必须出现在异常列表（连续零销）
        names = [a["product_name"] for a in data["anomalies"]]
        assert "土豆" in names
        potato = next(a for a in data["anomalies"] if a["product_name"] == "土豆")
        assert potato["current_value"] == 0
        assert "zero_sales" in potato["by_type"]
        assert potato["total_signals"] >= 1

        # 白菜稳定序列 → 不应在异常列表（锁定不误报）
        bai_cai = [a for a in data["anomalies"] if a["product_name"] == "白菜"]
        assert bai_cai == []

    async def test_scan_isolates_merchants(self, client, db_session):
        """商户A 的 sale 记录不会被商户B 扫描到（租户隔离）。

        对抗点：若 scan 的聚合查询漏了 merchant_id 过滤，B 会看到 A 的数据。
        本测试给 A seed 数据，以 B 身份扫描 → 必须 scanned==0。
        """
        mid_a = uuid.UUID(TEST_MERCHANT_ID)
        mid_b = uuid.UUID("00000000-0000-0000-0000-000000000002")
        now = datetime.now(UTC).replace(tzinfo=None)
        async with db_session() as session:
            session.add(
                InventoryRecord(
                    merchant_id=mid_a,
                    product_id=1,
                    quantity=Decimal("10"),
                    unit="斤",
                    event_type="sale",
                    event_time=now - timedelta(days=1),
                    source="manual",
                )
            )
            await session.commit()

        # 以 B 身份扫描 → 应无数据
        res = await client.get(
            "/api/v1/anomalies/scan?days=30",
            headers={"X-Test-Merchant-Id": str(mid_b)},
        )
        assert res.status_code == 200
        data = res.json()["data"]
        assert data["scanned"] == 0

    async def test_scan_requires_auth(self, auth_client):
        """未认证 → 401。"""
        res = await auth_client.get("/api/v1/anomalies/scan?days=30")
        assert res.status_code == 401

    async def test_scan_rejects_out_of_range_days(self, client):
        """days 超出 [7, 120] → 422（Query ge/le 约束强制）。"""
        too_small = await client.get("/api/v1/anomalies/scan?days=3")
        assert too_small.status_code == 422

        too_large = await client.get("/api/v1/anomalies/scan?days=999")
        assert too_large.status_code == 422
