"""库存异常检测引擎 — 基于多算法集成的时序异常检测。

参考项目:
  - PyOD (yzhao062/pyod): https://github.com/yzhao062/pyod
    → 60+检测器, 统一 fit/predict API, 集成学习(ADEngine)
  - Alibi-Detect (SeldonIO): https://github.com/SeldonIO/alibi-detect
    → Prophet检测器 / Spectral Residual / Seq2Seq
  - LinkedIn Luminol: https://github.com/linkedin/luminol
    → 轻量级时序异常检测 + 相关性分析
  - PyODDS: https://github.com/datamllab/pyodds
    → 端到端异常检测系统 (含数据库支持)

检测器列表:
  1. Z-Score: 经典统计方法 (适合正态分布)
  2. Modified Z-Score: 使用MAD代替标准差 (对离群值鲁棒)
  3. IQR: 四分位距法 (非参数, 适合偏态分布)
  4. Moving Average Deviation: 滑动窗口偏离检测
  5. Seasonal Decomposition: 周模式分解 + 残差检测
  6. Ensemble: 投票/加权融合多检测器结果

异常类型 (针对库存场景):
  - SPIKE: 突发性大量进货/出货 (可能数据异常或紧急补货)
  - DROP: 销量骤降 (天气/竞争对手/数据漏记)
  - TREND_BREAK: 趋势突变 (品类更换/经营策略变化)
  - STOCKOUT_RISK: 异常低库存信号
  - OVERSTOCK: 异常高库存信号
  - ZERO_SALES: 连续零销量 (可能忘记录入)
  - DATA_ERROR: 明显的数据录入错误 (数量级异常)
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date
from enum import Enum


# ── 异常类型枚举 ──────────────────────────────────────────────────
class AnomalyType(str, Enum):
    SPIKE = "spike"  # 突发峰值
    DROP = "drop"  # 骤降
    TREND_BREAK = "trend_break"  # 趋势突变
    STOCKOUT_RISK = "stockout_risk"  # 缺货风险
    OVERSTOCK = "overstock"  # 库存积压
    ZERO_SALES = "zero_sales"  # 连续零销
    DATA_ERROR = "data_error"  # 数据异常
    PATTERN_SHIFT = "pattern_shift"  # 模式漂移


class Severity(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


# ── 数据类 ────────────────────────────────────────────────────────
@dataclass
class AnomalySignal:
    """单个异常信号。"""

    date: str
    product_name: str
    anomaly_type: AnomalyType
    severity: Severity
    actual_value: float
    expected_value: float
    deviation: float  # 偏离比例
    detector: str  # 检测到该异常的检测器
    details: str
    suggestion: str = ""


@dataclass
class DetectionConfig:
    """检测器配置参数。"""

    zscore_threshold: float = 3.0  # Z-Score阈值
    modified_zscore_threshold: float = 3.5  # Modified Z-Score阈值
    iqr_multiplier: float = 1.5  # IQR乘数
    moving_avg_window: int = 7  # 移动平均窗口
    seasonal_period: int = 7  # 季节性周期(天)
    min_data_points: int = 5  # 最少需要的数据点
    ensemble_vote_threshold: int = 2  # 集成投票阈值(至少N个检测器同意)


@dataclass
class AnomalyReport:
    """异常检测报告。"""

    total_signals: int
    by_type: dict[str, int]
    by_severity: dict[str, int]
    signals: list[AnomalySignal]
    summary: str


# ── 核心检测引擎 ──────────────────────────────────────────────────
class AnomalyDetector:
    """库存异常检测引擎 — 多算法集成。

    使用方式:
        detector = AnomalyDetector(config=DetectionConfig())
        report = detector.detect(history, current_value, product_name)
    """

    def __init__(self, config: DetectionConfig | None = None):
        self.config = config or DetectionConfig()

    # ── 主入口 ──────────────────────────────────────────────────

    def detect(
        self,
        history: list[float],
        current_value: float,
        product_name: str = "未知商品",
        current_date: str | None = None,
    ) -> list[AnomalySignal]:
        """检测当前值是否异常。

        Args:
            history: 历史数据 (最近N天的销量/库存/进货量)
            current_value: 当前值
            product_name: 商品名
            current_date: 当前日期字符串

        Returns:
            异常信号列表 (可能多个检测器同时检测到)
        """
        if current_date is None:
            current_date = date.today().isoformat()

        n = len(history)
        if n < self.config.min_data_points:
            return []  # 数据不足

        signals: list[AnomalySignal] = []

        # 运行所有检测器
        for detector_name, detector_fn in self._detectors():
            result = detector_fn(history, current_value)
            if result is not None:
                anomaly_type, severity, deviation, details = result
                signals.append(
                    AnomalySignal(
                        date=current_date,
                        product_name=product_name,
                        anomaly_type=anomaly_type,
                        severity=severity,
                        actual_value=current_value,
                        expected_value=self._expected(history),
                        deviation=deviation,
                        detector=detector_name,
                        details=details,
                        suggestion=self._suggest(anomaly_type, severity),
                    )
                )

        # 集成投票过滤 — 至少 N 个检测器同意
        if self.config.ensemble_vote_threshold > 1:
            type_counts: dict[str, int] = {}
            for s in signals:
                type_counts[s.anomaly_type.value] = type_counts.get(s.anomaly_type.value, 0) + 1

            # 只保留有多检测器共识的信号
            signals = [
                s
                for s in signals
                if type_counts.get(s.anomaly_type.value, 0) >= self.config.ensemble_vote_threshold
            ]

        return signals

    def detect_batch(
        self,
        series: list[dict],  # [{"date": "2025-01-01", "qty": 10.0, "product": "白菜"}, ...]
    ) -> list[AnomalySignal]:
        """批量历史检测 — 扫描整个时间序列找出所有异常点。"""
        all_signals: list[AnomalySignal] = []

        for i, point in enumerate(series):
            if i < self.config.min_data_points:
                continue

            history = [s["qty"] for s in series[max(0, i - 30) : i]]
            current = point["qty"]
            product = point.get("product", "未知")
            date_str = point.get("date", "")

            signals = self.detect(history, current, product, date_str)
            all_signals.extend(signals)

        return all_signals

    # ── 检测器注册 ──────────────────────────────────────────────

    def _detectors(self):
        """返回所有检测器的 (名称, 函数) 对。"""
        return [
            ("zscore", self._zscore_detect),
            ("modified_zscore", self._modified_zscore_detect),
            ("iqr", self._iqr_detect),
            ("moving_avg_deviation", self._moving_avg_detect),
            ("seasonal", self._seasonal_detect),
            ("zero_sales", self._zero_sales_detect),
            ("data_error", self._data_error_detect),
        ]

    # ── 检测器1: Z-Score ───────────────────────────────────────

    def _zscore_detect(
        self, history: list[float], current: float
    ) -> tuple[AnomalyType, Severity, float, str] | None:
        """经典Z-Score方法。

        Z = (x - μ) / σ
        |Z| > threshold → 异常
        """
        n = len(history)
        mean = sum(history) / n
        variance = sum((x - mean) ** 2 for x in history) / n
        std = math.sqrt(variance) if variance > 0 else 1.0

        if std < 0.001:
            return None  # 数据几乎不变, 不检测

        z = (current - mean) / std

        if abs(z) < self.config.zscore_threshold:
            return None

        deviation = abs(current - mean) / max(abs(mean), 0.01)

        if z > 0:
            a_type = AnomalyType.SPIKE
            details = f"Z-Score={round(z, 2)} (μ={round(mean, 1)}, σ={round(std, 1)}) — 远高于均值"
        else:
            a_type = AnomalyType.DROP
            details = f"Z-Score={round(z, 2)} (μ={round(mean, 1)}, σ={round(std, 1)}) — 远低于均值"

        severity = self._zscore_severity(abs(z))
        return a_type, severity, round(deviation, 2), details

    # ── 检测器2: Modified Z-Score (MAD) ────────────────────────

    def _modified_zscore_detect(
        self, history: list[float], current: float
    ) -> tuple[AnomalyType, Severity, float, str] | None:
        """Modified Z-Score — 用MAD代替标准差, 对离群值更鲁棒。

        M_i = 0.6745 * (x_i - median) / MAD
        """
        sorted_h = sorted(history)
        n = len(sorted_h)
        median = sorted_h[n // 2] if n % 2 == 1 else (sorted_h[n // 2 - 1] + sorted_h[n // 2]) / 2

        # MAD: Median Absolute Deviation
        abs_devs = sorted([abs(x - median) for x in history])
        mad = abs_devs[n // 2] if n % 2 == 1 else (abs_devs[n // 2 - 1] + abs_devs[n // 2]) / 2

        if mad < 0.001:
            return None

        m_z = 0.6745 * (current - median) / mad

        if abs(m_z) < self.config.modified_zscore_threshold:
            return None

        deviation = abs(current - median) / max(abs(median), 0.01)

        mz_r = round(m_z, 2)
        med_r = round(median, 1)
        mad_r = round(mad, 1)
        if m_z > 0:
            a_type = AnomalyType.SPIKE
            details = f"Modified Z={mz_r} (median={med_r}, MAD={mad_r}) — 远高于中位数"
        else:
            a_type = AnomalyType.DROP
            details = f"Modified Z={mz_r} (median={med_r}, MAD={mad_r}) — 远低于中位数"

        severity = self._zscore_severity(abs(m_z))
        return a_type, severity, round(deviation, 2), details

    # ── 检测器3: IQR (四分位距) ─────────────────────────────────

    def _iqr_detect(
        self, history: list[float], current: float
    ) -> tuple[AnomalyType, Severity, float, str] | None:
        """IQR方法 — 非参数, 适合偏态分布。

        下界 = Q1 - k * IQR
        上界 = Q3 + k * IQR
        """
        sorted_h = sorted(history)
        n = len(sorted_h)

        q1_idx = n // 4
        q3_idx = (3 * n) // 4
        q1 = sorted_h[q1_idx]
        q3 = sorted_h[q3_idx]
        iqr = q3 - q1

        if iqr < 0.001:
            return None

        lower = q1 - self.config.iqr_multiplier * iqr
        upper = q3 + self.config.iqr_multiplier * iqr

        if lower <= current <= upper:
            return None

        median = sorted_h[n // 2]
        deviation = abs(current - median) / max(abs(median), 0.01)

        cur_r = round(current, 1)
        q1_r = round(q1, 1)
        q3_r = round(q3, 1)
        if current > upper:
            a_type = AnomalyType.SPIKE
            details = f"IQR: {cur_r} > 上界{round(upper, 1)} (Q1={q1_r}, Q3={q3_r})"
        else:
            a_type = AnomalyType.DROP
            details = f"IQR: {cur_r} < 下界{round(lower, 1)} (Q1={q1_r}, Q3={q3_r})"

        # severity based on how far out of bounds
        dist_ratio = max(
            (current - upper) / max(iqr, 0.01)
            if current > upper
            else (lower - current) / max(iqr, 0.01),
            0,
        )
        severity = (
            Severity.CRITICAL
            if dist_ratio > 4
            else Severity.HIGH
            if dist_ratio > 2
            else Severity.MEDIUM
            if dist_ratio > 1
            else Severity.LOW
        )

        return a_type, severity, round(deviation, 2), details

    # ── 检测器4: 移动平均偏离 ───────────────────────────────────

    def _moving_avg_detect(
        self, history: list[float], current: float
    ) -> tuple[AnomalyType, Severity, float, str] | None:
        """移动平均偏离检测 — 当前值 vs 近期移动平均。"""
        window = min(self.config.moving_avg_window, len(history))
        recent = history[-window:]

        ma = sum(recent) / len(recent)

        if ma < 0.001:
            return None

        # 计算近期标准差
        if len(recent) >= 3:
            variance = sum((x - ma) ** 2 for x in recent) / len(recent)
            std = math.sqrt(variance) if variance > 0 else ma * 0.1
        else:
            std = ma * 0.2

        if std < 0.001:
            return None

        deviation_ratio = (current - ma) / std

        if abs(deviation_ratio) < 2.5:
            return None

        deviation_pct = abs(current - ma) / ma

        cur_r = round(current, 1)
        ma_r = round(ma, 1)
        dev_r = round(deviation_ratio, 1)
        if current > ma:
            a_type = AnomalyType.SPIKE
            details = f"移动平均偏离: {cur_r} vs MA{window}={ma_r}, {dev_r}σ"
        else:
            a_type = AnomalyType.DROP
            details = f"移动平均偏离: {cur_r} vs MA{window}={ma_r}, {dev_r}σ"

        severity = self._zscore_severity(abs(deviation_ratio))
        return a_type, severity, round(deviation_pct, 2), details

    # ── 检测器5: 季节性分解 ─────────────────────────────────────

    def _seasonal_detect(
        self, history: list[float], current: float
    ) -> tuple[AnomalyType, Severity, float, str] | None:
        """简单季节性分解 — 提取周模式后检测残差异常。

        方法:
          1. 计算每个星期几的平均值 (周季节性)
          2. 从当前值中减去季节性分量
          3. 对残差应用Z-Score检测
        """
        period = self.config.seasonal_period  # 默认7 (周)
        n = len(history)

        if n < period * 2:
            return None  # 至少需要2个完整周期

        # 计算每个周期位置的平均值
        day_avgs = [0.0] * period
        day_counts = [0] * period
        for i, val in enumerate(history):
            pos = i % period
            day_avgs[pos] += val
            day_counts[pos] += 1

        for i in range(period):
            if day_counts[i] > 0:
                day_avgs[i] /= day_counts[i]

        # 总体均值
        grand_mean = sum(history) / n

        # 当前值对应的周期位置
        # 假设 history 最后一天是昨天, current 是今天
        # 今天在周期中的位置 = (len(history)) % period
        current_pos = n % period
        expected = grand_mean + (day_avgs[current_pos] - grand_mean)

        if expected < 0.001:
            return None

        # 残差分析
        # 计算历史残差的标准差
        residuals = []
        for i, val in enumerate(history):
            pos = i % period
            expected_i = grand_mean + (day_avgs[pos] - grand_mean)
            residuals.append(val - expected_i)

        if len(residuals) < 3:
            return None

        residual_mean = sum(residuals) / len(residuals)
        residual_var = sum((r - residual_mean) ** 2 for r in residuals) / len(residuals)
        residual_std = math.sqrt(residual_var) if residual_var > 0 else 1.0

        current_residual = current - expected
        residual_z = current_residual / residual_std if residual_std > 0 else 0

        if abs(residual_z) < 2.5:
            return None

        deviation = abs(current - expected) / max(abs(expected), 0.01)

        if current_residual > 0:
            a_type = AnomalyType.PATTERN_SHIFT
            details = (
                f"季节性偏离: 实际{round(current, 1)} vs 预期{round(expected, 1)}, "
                f"残差Z={round(residual_z, 2)}"
            )
        else:
            a_type = AnomalyType.PATTERN_SHIFT
            details = (
                f"季节性偏离: 实际{round(current, 1)} vs 预期{round(expected, 1)}, "
                f"残差Z={round(residual_z, 2)}"
            )

        severity = self._zscore_severity(abs(residual_z))
        return a_type, severity, round(deviation, 2), details

    # ── 检测器6: 连续零销量 ─────────────────────────────────────

    def _zero_sales_detect(
        self, history: list[float], current: float
    ) -> tuple[AnomalyType, Severity, float, str] | None:
        """检测连续零销量 — 可能忘记记录或严重经营问题。"""
        # 只看最近一段
        recent = history[-7:] if len(history) >= 7 else history

        # 当前值也是0
        if current > 0:
            return None

        # 统计连续0的天数
        consecutive_zeros = 0
        for val in reversed(recent):
            if val <= 0:
                consecutive_zeros += 1
            else:
                break

        total_zeros = consecutive_zeros + 1  # +1 for current

        if total_zeros < 3:
            return None

        a_type = AnomalyType.ZERO_SALES
        severity = (
            Severity.CRITICAL
            if total_zeros >= 7
            else Severity.HIGH
            if total_zeros >= 5
            else Severity.MEDIUM
        )
        deviation = 1.0  # 100%低于预期

        details = f"连续{total_zeros}天无销量 — 请检查是否忘记记账"

        return a_type, severity, deviation, details

    # ── 检测器7: 数据录入错误 ────────────────────────────────────

    def _data_error_detect(
        self, history: list[float], current: float
    ) -> tuple[AnomalyType, Severity, float, str] | None:
        """检测明显的数据录入错误 — 数量级异常。"""
        n = len(history)
        if n < 5:
            return None

        mean = sum(history) / n
        if mean < 0.001:
            return None

        # 量级差异检测: 如果当前值是历史均值的10倍以上或1/10以下
        ratio = current / mean

        if 0.1 <= ratio <= 10.0:
            return None

        # 特殊处理: 如果 current=0 但历史均值>0, 不在此检测(由zero_sales处理)
        if current == 0 and mean > 0:
            return None

        a_type = AnomalyType.DATA_ERROR
        severity = Severity.HIGH
        deviation = ratio

        cur_r = round(current, 1)
        mean_r = round(mean, 1)
        if ratio > 10:
            ratio_r = round(ratio, 0)
            details = f"数量级异常: 当前值({cur_r})是均值({mean_r})的{ratio_r}倍 — 可能多录了0"
        else:
            pct_r = round(ratio * 100, 0)
            details = f"数量级异常: 当前值({cur_r})仅为均值({mean_r})的{pct_r}% — 可能漏录了0"

        return a_type, severity, round(deviation, 2), details

    # ── 辅助方法 ──────────────────────────────────────────────────

    def _expected(self, history: list[float]) -> float:
        """计算期望值 (历史均值)。"""
        if not history:
            return 0.0
        return sum(history) / len(history)

    def _zscore_severity(self, abs_z: float) -> Severity:
        """Z-Score → 严重程度映射。"""
        if abs_z >= 5.0:
            return Severity.CRITICAL
        elif abs_z >= 4.0:
            return Severity.HIGH
        elif abs_z >= 3.0:
            return Severity.MEDIUM
        return Severity.LOW

    def _suggest(self, a_type: AnomalyType, severity: Severity) -> str:
        """根据异常类型和严重程度生成建议。"""
        suggestions = {
            (AnomalyType.SPIKE, Severity.CRITICAL): "突发大量进出, 请核实数据准确性并追溯原因",
            (AnomalyType.SPIKE, Severity.HIGH): "销量/进货异常偏高, 建议核实是否有促销/补货活动",
            (AnomalyType.DROP, Severity.CRITICAL): "销量/库存骤降, 请检查是否漏记录或存在经营异常",
            (AnomalyType.DROP, Severity.HIGH): "销量明显下降, 关注竞争或天气因素",
            (
                AnomalyType.ZERO_SALES,
                Severity.CRITICAL,
            ): "连续多日无销售记录, 请立即核实是否忘记记账",
            (AnomalyType.ZERO_SALES, Severity.HIGH): "连续无销售, 建议检查经营状况和记录习惯",
            (AnomalyType.DATA_ERROR, Severity.HIGH): "疑似数据录入错误, 请核对原始记录",
            (
                AnomalyType.PATTERN_SHIFT,
                Severity.CRITICAL,
            ): "销售模式发生重大变化, 检查品类/价格/竞争变化",
            (AnomalyType.STOCKOUT_RISK, Severity.HIGH): "库存异常偏低, 有断货风险, 请尽快补货",
            (AnomalyType.OVERSTOCK, Severity.HIGH): "库存异常偏高, 有积压风险, 考虑促销出清",
            (AnomalyType.TREND_BREAK, Severity.HIGH): "长期趋势突变, 建议审视经营策略调整的影响",
        }

        # 优先匹配精确类型+严重度
        key = (a_type, severity)
        if key in suggestions:
            return suggestions[key]

        # 回退: 匹配类型 + 任意严重度
        for (t, _s), sug in suggestions.items():
            if t == a_type:
                return sug

        # 通用建议
        if severity in (Severity.CRITICAL, Severity.HIGH):
            return "检测到异常数据, 建议核实并关注后续变化"
        return "轻微异常, 可观察后续趋势"

    # ── 高级: 库存专项检测 ──────────────────────────────────────

    def check_stockout_risk(
        self,
        current_inventory: float,
        daily_sales_history: list[float],
        lead_time_days: float = 1.0,
    ) -> AnomalySignal | None:
        """检测库存缺货风险。

        Args:
            current_inventory: 当前库存
            daily_sales_history: 近期日销量
            lead_time_days: 补货提前期(天)
        """
        if not daily_sales_history:
            return None

        avg_daily_sales = sum(daily_sales_history) / len(daily_sales_history)
        if avg_daily_sales <= 0:
            return None

        days_remaining = current_inventory / avg_daily_sales

        if days_remaining > lead_time_days * 1.5:
            return None  # 库存充足

        severity = (
            Severity.CRITICAL
            if days_remaining <= lead_time_days * 0.5
            else Severity.HIGH
            if days_remaining <= lead_time_days
            else Severity.MEDIUM
        )

        return AnomalySignal(
            date=date.today().isoformat(),
            product_name="",
            anomaly_type=AnomalyType.STOCKOUT_RISK,
            severity=severity,
            actual_value=current_inventory,
            expected_value=avg_daily_sales * lead_time_days * 1.5,
            deviation=round(1.0 - days_remaining / (lead_time_days * 1.5), 2),
            detector="stockout_check",
            details=f"库存仅够{days_remaining:.1f}天 (提前期{lead_time_days}天)",
            suggestion="库存偏低, 有断货风险, 建议尽快下单补货"
            if severity != Severity.MEDIUM
            else "库存略低, 建议关注并准备补货",
        )

    def check_overstock(
        self,
        current_inventory: float,
        daily_sales_history: list[float],
        max_days_cover: float = 7.0,
    ) -> AnomalySignal | None:
        """检测库存积压风险。"""
        if not daily_sales_history:
            return None

        avg_daily_sales = sum(daily_sales_history) / len(daily_sales_history)
        if avg_daily_sales <= 0:
            return None

        days_remaining = current_inventory / avg_daily_sales

        if days_remaining <= max_days_cover:
            return None

        severity = (
            Severity.CRITICAL
            if days_remaining > max_days_cover * 3
            else Severity.HIGH
            if days_remaining > max_days_cover * 2
            else Severity.MEDIUM
        )

        return AnomalySignal(
            date=date.today().isoformat(),
            product_name="",
            anomaly_type=AnomalyType.OVERSTOCK,
            severity=severity,
            actual_value=current_inventory,
            expected_value=avg_daily_sales * max_days_cover,
            deviation=round(days_remaining / max_days_cover - 1.0, 2),
            detector="overstock_check",
            details=f"库存可售{days_remaining:.1f}天 (建议上限{max_days_cover}天)",
            suggestion="库存积压严重, 建议减少采购或促销出清"
            if severity != Severity.MEDIUM
            else "库存偏高, 建议控制下次采购量",
        )

    # ── 完整检测报告 ────────────────────────────────────────────

    def full_report(
        self,
        history: list[float],
        current_value: float,
        product_name: str = "未知商品",
        current_inventory: float | None = None,
        lead_time_days: float = 1.0,
    ) -> AnomalyReport:
        """生成完整异常检测报告 (包含库存专项检查)。"""
        today_str = date.today().isoformat()
        signals = self.detect(history, current_value, product_name, today_str)

        # 库存专项
        if current_inventory is not None and history:
            stockout = self.check_stockout_risk(current_inventory, history, lead_time_days)
            if stockout:
                stockout.product_name = product_name
                signals.append(stockout)

            overstock = self.check_overstock(current_inventory, history)
            if overstock:
                overstock.product_name = product_name
                signals.append(overstock)

        # 统计
        by_type: dict[str, int] = {}
        by_severity: dict[str, int] = {}
        for s in signals:
            by_type[s.anomaly_type.value] = by_type.get(s.anomaly_type.value, 0) + 1
            by_severity[s.severity.value] = by_severity.get(s.severity.value, 0) + 1

        # 摘要
        critical_high = by_severity.get("critical", 0) + by_severity.get("high", 0)
        if critical_high > 0:
            summary = f"检测到{critical_high}个高优先级异常信号, 建议立即关注"
        elif signals:
            summary = f"检测到{len(signals)}个轻微异常信号"
        else:
            summary = "未检测到异常, 数据正常"

        return AnomalyReport(
            total_signals=len(signals),
            by_type=by_type,
            by_severity=by_severity,
            signals=signals,
            summary=summary,
        )


# ── 便捷函数 ──────────────────────────────────────────────────────
def quick_check(
    history: list[float],
    current_value: float,
    product_name: str = "未知商品",
) -> AnomalyReport:
    """快速异常检测 — 一行调用。"""
    detector = AnomalyDetector()
    return detector.full_report(history, current_value, product_name)
