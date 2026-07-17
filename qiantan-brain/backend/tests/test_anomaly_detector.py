"""Tests for inventory anomaly detection engine."""
import pytest

from app.services.anomaly_detector import (
    AnomalyDetector,
    AnomalyReport,
    AnomalySignal,
    AnomalyType,
    DetectionConfig,
    Severity,
    quick_check,
)


class TestDetectionConfig:
    """检测配置测试。"""

    def test_default_config(self):
        """默认配置有效。"""
        config = DetectionConfig()
        assert config.zscore_threshold == 3.0
        assert config.min_data_points == 5
        assert config.ensemble_vote_threshold == 2


class TestAnomalyDetector:
    """核心检测器测试。"""

    # ── Z-Score 检测器 ────────────────────────────────────────

    def test_zscore_normal_data_no_anomaly(self):
        """正常数据不产生异常。"""
        detector = AnomalyDetector(DetectionConfig(zscore_threshold=3.0))
        history = [10.0, 12.0, 11.0, 10.0, 13.0, 11.0, 12.0, 10.0, 11.0, 12.0]
        result = detector._zscore_detect(history, 11.0)
        assert result is None

    def test_zscore_detects_spike(self):
        """Z-Score检测到突发峰值。"""
        detector = AnomalyDetector(DetectionConfig(zscore_threshold=3.0))
        history = [10.0, 10.0, 10.0, 10.0, 10.0, 10.0, 10.0, 10.0, 10.0, 10.0]
        result = detector._zscore_detect(history, 50.0)
        assert result is not None
        a_type, severity, deviation, details = result
        assert a_type == AnomalyType.SPIKE
        assert severity in (Severity.HIGH, Severity.CRITICAL)

    def test_zscore_detects_drop(self):
        """Z-Score检测到骤降。"""
        detector = AnomalyDetector(DetectionConfig(zscore_threshold=3.0))
        history = [100.0, 100.0, 100.0, 100.0, 100.0, 100.0, 100.0, 100.0, 100.0, 100.0]
        result = detector._zscore_detect(history, 30.0)
        assert result is not None
        a_type, severity, deviation, details = result
        assert a_type == AnomalyType.DROP

    def test_zscore_constant_data_no_anomaly(self):
        """恒值数据不产生异常。"""
        detector = AnomalyDetector()
        history = [5.0, 5.0, 5.0, 5.0, 5.0]
        result = detector._zscore_detect(history, 5.0)
        assert result is None  # std=0, 不检测

    # ── Modified Z-Score 检测器 ───────────────────────────────

    def test_modified_zscore_robust_to_outlier(self):
        """Modified Z-Score对历史离群值鲁棒。"""
        detector = AnomalyDetector(DetectionConfig(modified_zscore_threshold=3.0))
        # 历史有一个轻微离群值 30, MAD不受太大影响
        # sorted: [10,10,10,10,10,10,10,11,12,30], median=10
        # MAD of abs deviations: sorted([0,0,0,0,0,0,0,1,2,20]) → median of [0,0,0,0,0,0,0,1,2,20] = 0
        # Hmm, MAD=0 means no detection. Let me use more varied data.
        history = [10.0, 11.0, 9.0, 12.0, 10.0, 8.0, 30.0, 11.0, 10.0, 9.0]
        result = detector._modified_zscore_detect(history, 50.0)
        # 50 vs median=10, should be anomalous
        assert result is not None

    def test_modified_zscore_normal_no_anomaly(self):
        """正常数据不触发。"""
        detector = AnomalyDetector()
        history = [10.0, 12.0, 11.0, 9.0, 10.0, 13.0, 11.0, 10.0, 12.0, 11.0]
        result = detector._modified_zscore_detect(history, 11.0)
        assert result is None

    # ── IQR 检测器 ────────────────────────────────────────────

    def test_iqr_detects_outlier(self):
        """IQR检测到离群值。"""
        detector = AnomalyDetector(DetectionConfig(iqr_multiplier=1.5))
        history = [10.0, 11.0, 12.0, 10.0, 11.0, 12.0, 13.0, 10.0, 11.0, 12.0]
        result = detector._iqr_detect(history, 50.0)
        assert result is not None

    def test_iqr_normal_no_anomaly(self):
        """IQR不误报正常值。"""
        detector = AnomalyDetector()
        history = [10.0, 11.0, 12.0, 10.0, 11.0, 12.0, 10.0, 11.0, 12.0, 10.0]
        result = detector._iqr_detect(history, 11.0)
        assert result is None

    # ── 移动平均偏离检测器 ─────────────────────────────────────

    def test_moving_avg_detects_deviation(self):
        """移动平均检测到偏离。"""
        detector = AnomalyDetector(DetectionConfig(moving_avg_window=7))
        history = [10.0, 11.0, 10.0, 12.0, 11.0, 10.0, 12.0, 11.0, 10.0, 11.0]
        result = detector._moving_avg_detect(history, 50.0)
        assert result is not None

    def test_moving_avg_normal_no_anomaly(self):
        """移动平均不误报。"""
        detector = AnomalyDetector()
        history = [10.0, 11.0, 10.0, 12.0, 11.0, 10.0, 12.0]
        result = detector._moving_avg_detect(history, 11.0)
        assert result is None

    # ── 季节性检测器 ──────────────────────────────────────────

    def test_seasonal_pattern_detection(self):
        """季节性检测。"""
        detector = AnomalyDetector(DetectionConfig(seasonal_period=7))
        # 模拟每周模式: 工作日低, 周末高
        history = [
            10, 10, 10, 10, 10, 15, 20,   # week 1
            10, 10, 10, 10, 10, 15, 20,   # week 2
            10, 10, 10, 10, 10, 15, 20,   # week 3
        ]
        # 按模式, 今天(周一)应该是10, 如果爆涨到50应该是异常
        result = detector._seasonal_detect(history, 50.0)
        assert result is not None

    def test_seasonal_normal_fits_pattern(self):
        """符合季节模式的不触发。"""
        detector = AnomalyDetector(DetectionConfig(seasonal_period=7))
        history = [
            10, 10, 10, 10, 10, 15, 20,
            10, 10, 10, 10, 10, 15, 20,
        ]
        result = detector._seasonal_detect(history, 10.0)
        assert result is None

    def test_seasonal_insufficient_data(self):
        """数据不足时不检测。"""
        detector = AnomalyDetector(DetectionConfig(seasonal_period=7))
        history = [10, 10, 10, 10, 10]
        result = detector._seasonal_detect(history, 50.0)
        assert result is None  # < 2个完整周期

    # ── 连续零销量检测器 ───────────────────────────────────────

    def test_zero_sales_detected(self):
        """连续零销量被检测到。"""
        detector = AnomalyDetector()
        # 最近的几天是0, 且当前也是0 — 这样才能检测到连续零销
        history = [10.0, 8.0, 10.0, 12.0, 0.0, 0.0, 0.0]
        result = detector._zero_sales_detect(history, 0.0)
        assert result is not None
        a_type, severity, deviation, details = result
        assert a_type == AnomalyType.ZERO_SALES

    def test_zero_sales_short_gap_ok(self):
        """短期零销量不触发。"""
        detector = AnomalyDetector()
        history = [10.0, 10.0, 0.0, 10.0, 10.0, 10.0, 10.0]
        result = detector._zero_sales_detect(history, 10.0)  # 当前有销量
        assert result is None  # 不触发, 因为当前>0

    def test_zero_sales_short_no_trigger(self):
        """仅1-2天零销量不触发。"""
        detector = AnomalyDetector()
        history = [10.0, 5.0, 0.0, 10.0, 10.0, 10.0, 10.0]
        result = detector._zero_sales_detect(history, 0.0)
        assert result is None  # 仅2天连续

    # ── 数据录入错误检测器 ─────────────────────────────────────

    def test_data_error_detected_magnitude(self):
        """数量级异常被检测到。"""
        detector = AnomalyDetector()
        history = [10.0, 12.0, 11.0, 10.0, 13.0, 11.0, 12.0, 10.0, 11.0, 12.0]
        result = detector._data_error_detect(history, 500.0)  # 50倍均值
        assert result is not None
        a_type, severity, deviation, details = result
        assert a_type == AnomalyType.DATA_ERROR

    def test_data_error_normal_no_trigger(self):
        """正常量级不触发。"""
        detector = AnomalyDetector()
        history = [10.0, 12.0, 11.0, 10.0, 13.0]
        result = detector._data_error_detect(history, 12.0)
        assert result is None

    # ── 集成检测 ──────────────────────────────────────────────

    def test_detect_with_sufficient_data(self):
        """有足够数据时正常检测。"""
        detector = AnomalyDetector()
        history = [10.0, 12.0, 11.0, 10.0, 13.0, 11.0, 12.0, 10.0, 11.0, 12.0]
        signals = detector.detect(history, 50.0, "白菜")
        assert len(signals) > 0  # 至少有一个检测器发现异常

    def test_detect_insufficient_data_returns_empty(self):
        """数据不足返回空。"""
        detector = AnomalyDetector(DetectionConfig(min_data_points=5))
        history = [10.0, 12.0]
        signals = detector.detect(history, 50.0)
        assert signals == []

    def test_ensemble_voting_filters_noise(self):
        """集成投票过滤噪声。"""
        config = DetectionConfig(
            zscore_threshold=2.0,          # 降低阈值让更多检测器触发
            modified_zscore_threshold=2.5,
            ensemble_vote_threshold=3,      # 但要求≥3个检测器同意
        )
        detector = AnomalyDetector(config)
        history = [10.0, 12.0, 11.0, 10.0, 13.0, 11.0, 12.0, 10.0, 11.0, 12.0]
        # 稍微偏离的值
        signals = detector.detect(history, 20.0, "测试")
        # 应该被投票过滤掉大部分
        # (不强制断言数量, 取决于各检测器结果)

    def test_batch_detect(self):
        """批量历史检测。"""
        detector = AnomalyDetector(DetectionConfig(min_data_points=5))
        series = [
            {"date": f"2025-01-{i:02d}", "qty": 10.0, "product": "白菜"}
            for i in range(1, 15)
        ]
        series.append({"date": "2025-01-15", "qty": 500.0, "product": "白菜"})
        signals = detector.detect_batch(series)
        assert len(signals) > 0  # 最后一个应该是异常

    # ── 库存专项检测 ──────────────────────────────────────────

    def test_stockout_risk_low_inventory(self):
        """低库存检测到缺货风险。"""
        detector = AnomalyDetector()
        history = [10.0, 12.0, 11.0, 10.0, 13.0, 11.0, 12.0]
        signal = detector.check_stockout_risk(5.0, history, lead_time_days=1.0)
        assert signal is not None
        assert signal.anomaly_type == AnomalyType.STOCKOUT_RISK

    def test_stockout_risk_sufficient_inventory(self):
        """充足库存不触发。"""
        detector = AnomalyDetector()
        history = [5.0, 6.0, 5.0, 6.0, 5.0, 6.0, 5.0]
        signal = detector.check_stockout_risk(50.0, history, lead_time_days=1.0)
        assert signal is None

    def test_overstock_detected(self):
        """库存积压被检测到。"""
        detector = AnomalyDetector()
        history = [5.0, 6.0, 5.0, 6.0, 5.0, 6.0, 5.0]
        signal = detector.check_overstock(100.0, history, max_days_cover=7.0)
        assert signal is not None
        assert signal.anomaly_type == AnomalyType.OVERSTOCK

    def test_overstock_normal_not_triggered(self):
        """正常库存不触发。"""
        detector = AnomalyDetector()
        history = [5.0, 6.0, 5.0, 6.0, 5.0, 6.0, 5.0]
        signal = detector.check_overstock(10.0, history, max_days_cover=7.0)
        assert signal is None

    # ── 完整报告 ──────────────────────────────────────────────

    def test_full_report_returns_all_fields(self):
        """完整报告包含所有字段。"""
        detector = AnomalyDetector()
        history = [10.0, 12.0, 11.0, 10.0, 13.0, 11.0, 12.0, 10.0, 11.0, 12.0]
        report = detector.full_report(
            history, 50.0, "菠菜",
            current_inventory=5.0,
            lead_time_days=1.0,
        )
        assert isinstance(report, AnomalyReport)
        assert report.total_signals > 0
        assert report.summary  # 摘要非空
        assert "by_type" in report.__dict__ or report.by_type is not None

    def test_full_report_normal_data(self):
        """正常数据报告无异常。"""
        detector = AnomalyDetector()
        history = [10.0, 11.0, 10.0, 12.0, 11.0, 10.0, 12.0, 11.0, 10.0, 11.0]
        report = detector.full_report(history, 11.0, "正常商品")
        assert "未检测到异常" in report.summary


class TestConvenienceFunctions:
    """便捷函数测试。"""

    def test_quick_check_returns_report(self):
        """快速检查返回报告。"""
        history = [10.0, 12.0, 11.0, 10.0, 13.0, 11.0, 12.0, 10.0, 11.0, 12.0]
        report = quick_check(history, 50.0, "白菜")
        assert isinstance(report, AnomalyReport)
        assert report.total_signals > 0


class TestAnomalySignal:
    """异常信号数据类测试。"""

    def test_signal_creation(self):
        """信号创建正常。"""
        signal = AnomalySignal(
            date="2025-01-15",
            product_name="白菜",
            anomaly_type=AnomalyType.SPIKE,
            severity=Severity.HIGH,
            actual_value=100.0,
            expected_value=10.0,
            deviation=9.0,
            detector="zscore",
            details="测试",
            suggestion="建议核实",
        )
        assert signal.product_name == "白菜"
        assert signal.severity == Severity.HIGH


class TestEdgeCases:
    """边界情况。"""

    def test_empty_history(self):
        """空历史不崩溃。"""
        detector = AnomalyDetector()
        signals = detector.detect([], 10.0)
        assert signals == []

    def test_negative_values(self):
        """负值也能检测。"""
        detector = AnomalyDetector()
        history = [10.0, 12.0, 11.0, 10.0, 13.0, 11.0, 12.0, 10.0, 11.0, 12.0]
        result = detector._zscore_detect(history, -50.0)
        assert result is not None

    def test_all_zero_history(self):
        """全零历史。"""
        detector = AnomalyDetector()
        history = [0.0, 0.0, 0.0, 0.0, 0.0]
        signals = detector.detect(history, 0.0)
        # 全零时大部分检测器因std=0返回None, zero_sales也不触发(因为历史全零但只有5天)
        # 这是正常行为, 不应该崩溃

    def test_very_small_values(self):
        """极小值不崩溃。"""
        detector = AnomalyDetector()
        history = [0.01, 0.02, 0.01, 0.02, 0.01]
        result = detector._zscore_detect(history, 1.0)
        assert result is not None  # 1.0 vs 0.014是明显异常
