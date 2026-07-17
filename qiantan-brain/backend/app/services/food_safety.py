"""食品安全合规追踪 — 简化版HACCP关键控制点监控。

参考项目:
  - zavora-ai/mcp-qms: https://github.com/zavora-ai/mcp-qms
    → 完整QMS: HACCP CCP监控 + NCR自动生成 + CAPA闭环
    → 31个MCP工具: 批次/QC/供应商/投诉全覆盖
  - zavora-ai/mcp-lims: https://github.com/zavora-ai/mcp-lims
    → LIMS: 样本链/检测规格/OOS检测/ALCOA+审计追踪
    → 27个MCP工具: HACCP CCP + 仪器校准 + 食品安全事件

核心设计 (适配摊贩场景):
  - 关键控制点 (CCP): 温度/时间/清洁度/来源可追溯
  - 简化版NCR (不合格报告): CCP超标自动生成
  - 检查清单: 每日/每周必查项目
  - 温度日志: 冷链/热柜/环境温度
  - 保质期追踪: 进货时间 + 预计过期 + 过期预警
  - 评分卡: 食品安全综合评分 (0-100)

摊贩CCP:
  CCP-1: 冷藏温度 (肉类/水产 < 4°C, 熟食 < 5°C)
  CCP-2: 热柜温度 (熟食 > 60°C)
  CCP-3: 加工时间 (熟食出锅到售完 < 4小时)
  CCP-4: 清洁消毒 (每日收摊后)
  CCP-5: 来源可溯 (供应商证照齐全)
  CCP-6: 交叉污染 (生熟分开)
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from enum import Enum
from typing import Optional


# ── 枚举定义 ──────────────────────────────────────────────────────
class CCPStatus(str, Enum):
    OK = "ok"              # 合格
    WARNING = "warning"    # 接近限值
    VIOLATION = "violation"  # 超标
    NOT_APPLICABLE = "na"  # 不适用


class NCRSeverity(str, Enum):
    MINOR = "minor"        # 轻微不符合
    MAJOR = "major"        # 重大不符合
    CRITICAL = "critical"  # 严重不符合


class InspectionResult(str, Enum):
    PASS = "pass"
    FAIL = "fail"
    CONDITIONAL_PASS = "conditional_pass"


# ── 数据类 ────────────────────────────────────────────────────────

@dataclass
class CCPDefinition:
    """关键控制点定义。"""
    code: str                     # CCP-1
    name: str                     # 冷藏温度
    description: str
    category: str                 # temperature / time / hygiene / traceability
    critical_limit: str           # "< 4°C" 或 "> 60°C"
    warning_limit: str | None     # 预警限值
    check_frequency: str          # "每2小时" / "每日" / "每批"
    corrective_action: str        # 超标时的纠正措施
    applicable_categories: list[str]  # 适用品类


@dataclass
class CCPReading:
    """CCP检测读数。"""
    ccp_code: str
    value: float
    unit: str                     # °C / 小时 / 次
    status: CCPStatus
    checked_at: str
    checked_by: str | None = None
    notes: str | None = None


@dataclass
class NCRRecord:
    """不合格报告 (Non-Conformance Report)。"""
    id: str
    ccp_code: str
    severity: NCRSeverity
    description: str
    detected_at: str
    root_cause: str | None = None
    correction: str | None = None
    capa_required: bool = False
    capa_status: str | None = None  # open / in_progress / verified / closed
    resolved_at: str | None = None


@dataclass
class DailyChecklist:
    """每日食品安全检查清单。"""
    date: str
    items: list[dict]  # [{"item": "洗手消毒", "done": True, "notes": ""}, ...]
    overall_result: InspectionResult
    inspector: str | None = None
    notes: str | None = None


@dataclass
class FoodSafetyScorecard:
    """食品安全综合评分卡。"""
    date: str
    overall_score: float           # 0-100
    grade: str                     # A/B/C/D/F
    ccp_compliance: dict[str, CCPStatus]  # 各CCP状态
    temperature_score: float       # 温度管理 0-25
    hygiene_score: float           # 卫生管理 0-25
    traceability_score: float      # 来源可溯 0-20
    expiry_score: float            # 保质期管理 0-15
    documentation_score: float     # 记录完整性 0-15
    open_ncrs: int                 # 未关闭NCR数
    recommendations: list[str]
    risk_level: str                # low / medium / high / critical


# ── CCP 定义表 ────────────────────────────────────────────────────
DEFAULT_CCPS: list[CCPDefinition] = [
    CCPDefinition(
        code="CCP-1",
        name="冷藏温度",
        description="冷藏设备的温度必须保持在安全范围内",
        category="temperature",
        critical_limit="< 4°C (肉类/水产) / < 8°C (蔬菜)",
        warning_limit="> 3°C (肉类) / > 6°C (蔬菜)",
        check_frequency="每4小时",
        corrective_action="立即将食品转移至备用冷藏设备, 检查设备故障, 丢弃已变质食品",
        applicable_categories=["meat", "seafood", "dairy", "vegetable", "leafy_green"],
    ),
    CCPDefinition(
        code="CCP-2",
        name="热柜温度",
        description="热柜/保温设备的温度必须保持在安全温度以上",
        category="temperature",
        critical_limit="> 60°C",
        warning_limit="< 65°C",
        check_frequency="每2小时",
        corrective_action="检查加热设备, 低于60°C超过2小时的食品必须丢弃",
        applicable_categories=["cooked_food"],
    ),
    CCPDefinition(
        code="CCP-3",
        name="加工时间控制",
        description="熟食从出锅到售完不得超过安全时间限制",
        category="time",
        critical_limit="< 4小时 (室温) / < 2小时 (高温>32°C)",
        warning_limit="> 3小时",
        check_frequency="每批",
        corrective_action="超时未售完的熟食必须丢弃, 不得继续销售",
        applicable_categories=["cooked_food"],
    ),
    CCPDefinition(
        code="CCP-4",
        name="清洁消毒",
        description="接触食品的表面和工具必须定期清洁消毒",
        category="hygiene",
        critical_limit="每日收摊后完成",
        warning_limit=None,
        check_frequency="每日",
        corrective_action="立即清洁消毒, 检查清洁用品是否充足",
        applicable_categories=["vegetable", "leafy_green", "fruit", "meat", "seafood", "cooked_food", "dairy", "dry_goods"],
    ),
    CCPDefinition(
        code="CCP-5",
        name="来源可追溯",
        description="所有食品必须来自证照齐全的供应商, 记录可追溯",
        category="traceability",
        critical_limit="供应商证照齐全 + 进货记录完整",
        warning_limit=None,
        check_frequency="每批",
        corrective_action="暂停从无证供应商进货, 补全进货记录",
        applicable_categories=["vegetable", "leafy_green", "fruit", "meat", "seafood", "cooked_food", "dairy", "dry_goods"],
    ),
    CCPDefinition(
        code="CCP-6",
        name="交叉污染防控",
        description="生熟食品分开存放/加工, 防止交叉污染",
        category="hygiene",
        critical_limit="生熟完全分离 + 专用工具",
        warning_limit=None,
        check_frequency="每日",
        corrective_action="立即分开存放, 丢弃可能交叉污染的食品, 消毒工具",
        applicable_categories=["meat", "seafood", "cooked_food"],
    ),
]


# ── 核心引擎 ──────────────────────────────────────────────────────
class FoodSafetyEngine:
    """食品安全合规引擎。

    使用方式:
        engine = FoodSafetyEngine()
        scorecard = engine.evaluate(ccp_readings, checklist)
    """

    # 品类温度阈值
    TEMP_THRESHOLDS = {
        "meat": {"cold_max": 4.0, "cold_warn": 3.0},
        "seafood": {"cold_max": 4.0, "cold_warn": 3.0},
        "dairy": {"cold_max": 4.0, "cold_warn": 3.0},
        "cooked_food": {"hot_min": 60.0, "hot_warn": 65.0, "cold_max": 5.0, "cold_warn": 4.0},
        "vegetable": {"cold_max": 8.0, "cold_warn": 6.0},
        "leafy_green": {"cold_max": 8.0, "cold_warn": 6.0},
        "fruit": {"cold_max": 10.0, "cold_warn": 8.0},
        "dry_goods": {},
    }

    # 品类保质期 (小时)
    CATEGORY_SHELF_LIFE = {
        "vegetable": 72,
        "leafy_green": 36,
        "fruit": 96,
        "meat": 48,
        "seafood": 24,
        "cooked_food": 12,
        "dairy": 120,
        "dry_goods": 2160,
    }

    def __init__(self, ccps: list[CCPDefinition] | None = None):
        self.ccps = ccps or DEFAULT_CCPS
        self._ccp_map = {c.code: c for c in self.ccps}

    # ── 温度合规检查 ──────────────────────────────────────────

    def check_temperature(
        self,
        temperature_c: float,
        category: str,
        storage_type: str = "cold",  # cold / hot / ambient
    ) -> CCPReading:
        """检查温度是否在安全范围内。

        Args:
            temperature_c: 实测温度(°C)
            category: 品类
            storage_type: 存储类型 (冷藏/热柜/常温)
        """
        thresholds = self.TEMP_THRESHOLDS.get(category, {})

        if storage_type == "cold":
            max_temp = thresholds.get("cold_max")
            warn_temp = thresholds.get("cold_warn")

            if max_temp is None:
                return CCPReading(
                    ccp_code="CCP-1",
                    value=temperature_c,
                    unit="°C",
                    status=CCPStatus.NOT_APPLICABLE,
                    checked_at=datetime.now().isoformat(),
                    notes=f"品类'{category}'无需冷藏监控",
                )

            if temperature_c > max_temp:
                status = CCPStatus.VIOLATION
            elif warn_temp and temperature_c > warn_temp:
                status = CCPStatus.WARNING
            else:
                status = CCPStatus.OK

            return CCPReading(
                ccp_code="CCP-1",
                value=temperature_c,
                unit="°C",
                status=status,
                checked_at=datetime.now().isoformat(),
            )

        elif storage_type == "hot":
            min_temp = thresholds.get("hot_min")
            warn_temp = thresholds.get("hot_warn")

            if min_temp is None:
                return CCPReading(
                    ccp_code="CCP-2",
                    value=temperature_c,
                    unit="°C",
                    status=CCPStatus.NOT_APPLICABLE,
                    checked_at=datetime.now().isoformat(),
                    notes=f"品类'{category}'无需热柜监控",
                )

            if temperature_c < min_temp:
                status = CCPStatus.VIOLATION
            elif warn_temp and temperature_c < warn_temp:
                status = CCPStatus.WARNING
            else:
                status = CCPStatus.OK

            return CCPReading(
                ccp_code="CCP-2",
                value=temperature_c,
                unit="°C",
                status=status,
                checked_at=datetime.now().isoformat(),
            )

        return CCPReading(
            ccp_code="CCP-1",
            value=temperature_c,
            unit="°C",
            status=CCPStatus.OK,
            checked_at=datetime.now().isoformat(),
            notes=f"常温存储, 无需温度监控",
        )

    def check_time_limit(
        self,
        hours_since_cooked: float,
        ambient_temp_c: float = 25.0,
    ) -> CCPReading:
        """检查熟食加工时间是否超限。

        Args:
            hours_since_cooked: 出锅后经过的小时数
            ambient_temp_c: 环境温度
        """
        # 安全时间随温度变化
        if ambient_temp_c > 32:
            safe_hours = 2.0
            warn_hours = 1.5
        else:
            safe_hours = 4.0
            warn_hours = 3.0

        if hours_since_cooked > safe_hours:
            status = CCPStatus.VIOLATION
        elif hours_since_cooked > warn_hours:
            status = CCPStatus.WARNING
        else:
            status = CCPStatus.OK

        return CCPReading(
            ccp_code="CCP-3",
            value=hours_since_cooked,
            unit="小时",
            status=status,
            checked_at=datetime.now().isoformat(),
        )

    # ── 保质期追踪 ──────────────────────────────────────────────

    def check_expiry(
        self,
        arrival_time: datetime,
        category: str,
        current_time: datetime | None = None,
    ) -> dict:
        """检查食品是否过期或临期。

        Returns:
            dict with status, hours_remaining, quality_factor, recommendation
        """
        now = current_time or datetime.now()
        shelf_life = self.CATEGORY_SHELF_LIFE.get(category, 72)
        elapsed = (now - arrival_time).total_seconds() / 3600.0
        remaining = max(0.0, shelf_life - elapsed)
        remaining_pct = remaining / shelf_life if shelf_life > 0 else 0.0

        if remaining_pct <= 0:
            status = "expired"
            recommendation = "已过期, 必须立即下架丢弃"
        elif remaining_pct <= 0.1:
            status = "critical"
            recommendation = "仅剩不到10%保质期, 建议立即促销出清或丢弃"
        elif remaining_pct <= 0.25:
            status = "warning"
            recommendation = f"剩余{remaining:.0f}小时, 建议降价促销优先出清"
        elif remaining_pct <= 0.5:
            status = "notice"
            recommendation = f"剩余{remaining:.0f}小时, 注意优先销售"
        else:
            status = "ok"
            recommendation = ""

        # 质量因子 (指数衰减)
        half_life = shelf_life / 3.0
        lam = math.log(2) / half_life if half_life > 0 else 0
        quality = math.exp(-lam * elapsed) if lam > 0 else 1.0

        return {
            "category": category,
            "shelf_life_hours": shelf_life,
            "hours_elapsed": round(elapsed, 1),
            "hours_remaining": round(remaining, 1),
            "remaining_pct": round(remaining_pct * 100, 1),
            "status": status,
            "quality_factor": round(max(0.0, quality), 2),
            "recommendation": recommendation,
            "arrival_time": arrival_time.isoformat(),
        }

    # ── NCR 自动生成 ────────────────────────────────────────────

    def generate_ncr(
        self,
        reading: CCPReading,
        ncr_id: str | None = None,
    ) -> NCRRecord | None:
        """从CCP超标读数自动生成不合格报告。"""
        if reading.status != CCPStatus.VIOLATION:
            return None

        ccp = self._ccp_map.get(reading.ccp_code)
        if ccp is None:
            return None

        # 严重程度判定
        if reading.ccp_code in ("CCP-1", "CCP-2"):
            # 温度超标 — 按超标程度
            if reading.unit == "°C":
                if reading.ccp_code == "CCP-1":
                    # 冷柜: 超标越多越严重
                    # 肉类阈值4°C
                    if reading.value > 10:
                        severity = NCRSeverity.CRITICAL
                    elif reading.value > 7:
                        severity = NCRSeverity.MAJOR
                    else:
                        severity = NCRSeverity.MINOR
                else:
                    # 热柜: 低于阈值越多越严重
                    if reading.value < 45:
                        severity = NCRSeverity.CRITICAL
                    elif reading.value < 55:
                        severity = NCRSeverity.MAJOR
                    else:
                        severity = NCRSeverity.MINOR
            else:
                severity = NCRSeverity.MAJOR
        elif reading.ccp_code == "CCP-3":
            # 时间超标 — 重大或严重
            severity = NCRSeverity.CRITICAL if reading.value > 6 else NCRSeverity.MAJOR
        elif reading.ccp_code == "CCP-6":
            severity = NCRSeverity.MAJOR
        else:
            severity = NCRSeverity.MINOR

        ncr = NCRRecord(
            id=ncr_id or f"NCR-{datetime.now().strftime('%Y%m%d%H%M%S')}",
            ccp_code=reading.ccp_code,
            severity=severity,
            description=f"{ccp.name}超标: {reading.value}{reading.unit} (限值: {ccp.critical_limit})",
            detected_at=reading.checked_at,
            root_cause=None,
            correction=ccp.corrective_action,
            capa_required=severity in (NCRSeverity.MAJOR, NCRSeverity.CRITICAL),
            capa_status="open" if severity in (NCRSeverity.MAJOR, NCRSeverity.CRITICAL) else None,
        )

        return ncr

    # ── 综合评分卡 ──────────────────────────────────────────────

    def evaluate(
        self,
        ccp_readings: list[CCPReading],
        daily_checklist: DailyChecklist | None = None,
        expiry_items: list[dict] | None = None,
        supplier_cert_count: int = 0,
        total_supplier_count: int = 0,
    ) -> FoodSafetyScorecard:
        """生成综合食品安全评分卡。"""
        today_str = date.today().isoformat()

        # 1. 温度管理评分 (0-25分)
        temp_score = self._score_temperature(ccp_readings)

        # 2. 卫生管理评分 (0-25分)
        hygiene_score = self._score_hygiene(daily_checklist, ccp_readings)

        # 3. 来源可溯评分 (0-20分)
        trace_score = self._score_traceability(supplier_cert_count, total_supplier_count)

        # 4. 保质期管理评分 (0-15分)
        expiry_score = self._score_expiry(expiry_items)

        # 5. 记录完整性评分 (0-15分)
        doc_score = self._score_documentation(ccp_readings, daily_checklist)

        overall = temp_score + hygiene_score + trace_score + expiry_score + doc_score

        # 等级
        if overall >= 90:
            grade = "A"
            risk = "low"
        elif overall >= 75:
            grade = "B"
            risk = "low"
        elif overall >= 60:
            grade = "C"
            risk = "medium"
        elif overall >= 40:
            grade = "D"
            risk = "high"
        else:
            grade = "F"
            risk = "critical"

        # CCP合规状态
        ccp_status = {}
        for r in ccp_readings:
            ccp_status[r.ccp_code] = r.status

        # 建议
        recommendations = []
        if temp_score < 20:
            recommendations.append("温度管理存在不足, 建议增加检查频次并检修设备")
        if hygiene_score < 20:
            recommendations.append("卫生管理需加强, 建议制定每日清洁流程并严格执行")
        if trace_score < 15:
            recommendations.append("供应商证照不全, 建议尽快补齐所有供应商资质")
        if expiry_score < 10:
            recommendations.append("存在临期/过期食品风险, 建议加强保质期管理和促销出清")

        # 统计未关闭NCR
        open_ncrs = sum(
            1 for r in ccp_readings
            if r.status == CCPStatus.VIOLATION
        )

        if not recommendations:
            recommendations.append("食品安全管理良好, 保持当前做法")

        return FoodSafetyScorecard(
            date=today_str,
            overall_score=round(overall, 1),
            grade=grade,
            ccp_compliance=ccp_status,
            temperature_score=round(temp_score, 1),
            hygiene_score=round(hygiene_score, 1),
            traceability_score=round(trace_score, 1),
            expiry_score=round(expiry_score, 1),
            documentation_score=round(doc_score, 1),
            open_ncrs=open_ncrs,
            recommendations=recommendations,
            risk_level=risk,
        )

    def _score_temperature(self, readings: list[CCPReading]) -> float:
        """温度管理评分。"""
        temp_readings = [r for r in readings if r.ccp_code in ("CCP-1", "CCP-2")]
        if not temp_readings:
            return 15.0  # 无温度读数, 给基础分

        total = len(temp_readings)
        violations = sum(1 for r in temp_readings if r.status == CCPStatus.VIOLATION)
        warnings = sum(1 for r in temp_readings if r.status == CCPStatus.WARNING)
        na_count = sum(1 for r in temp_readings if r.status == CCPStatus.NOT_APPLICABLE)

        effective = total - na_count
        if effective == 0:
            return 25.0

        # 每个violation扣10分, 每个warning扣3分
        penalty = violations * 10 + warnings * 3
        score = max(0.0, 25.0 - penalty * (25.0 / max(effective * 10, 1)))

        return min(25.0, score)

    def _score_hygiene(
        self,
        checklist: DailyChecklist | None,
        readings: list[CCPReading],
    ) -> float:
        """卫生管理评分。"""
        score = 15.0  # 基础分

        # 检查清单完成度
        if checklist is not None:
            if checklist.items:
                done = sum(1 for item in checklist.items if item.get("done", False))
                completion = done / len(checklist.items)
                score += completion * 5.0

            if checklist.overall_result == InspectionResult.PASS:
                score += 5.0
            elif checklist.overall_result == InspectionResult.CONDITIONAL_PASS:
                score += 2.0

        # 交叉污染 CCP
        ccp6 = [r for r in readings if r.ccp_code == "CCP-6"]
        if ccp6:
            if any(r.status == CCPStatus.VIOLATION for r in ccp6):
                score -= 10.0
            elif any(r.status == CCPStatus.WARNING for r in ccp6):
                score -= 3.0

        # 清洁 CCP
        ccp4 = [r for r in readings if r.ccp_code == "CCP-4"]
        if ccp4 and any(r.status == CCPStatus.VIOLATION for r in ccp4):
            score -= 5.0

        return max(0.0, min(25.0, score))

    def _score_traceability(
        self, cert_count: int, total_count: int
    ) -> float:
        """来源可追溯评分。"""
        if total_count == 0:
            return 10.0

        ratio = cert_count / total_count
        return round(ratio * 20.0, 1)

    def _score_expiry(self, expiry_items: list[dict] | None) -> float:
        """保质期管理评分。"""
        if not expiry_items:
            return 10.0  # 无数据, 给基础分

        total = len(expiry_items)
        expired = sum(1 for e in expiry_items if e.get("status") == "expired")
        critical = sum(1 for e in expiry_items if e.get("status") == "critical")
        warning = sum(1 for e in expiry_items if e.get("status") == "warning")

        # 每个过期扣5分, 严重临期扣3分, 警告扣1分
        penalty = expired * 5 + critical * 3 + warning * 1
        score = max(0.0, 15.0 - penalty)

        return min(15.0, score)

    def _score_documentation(
        self,
        readings: list[CCPReading],
        checklist: DailyChecklist | None,
    ) -> float:
        """记录完整性评分。"""
        score = 10.0  # 基础分

        # 有CCP读数 → +3分
        if readings:
            score += 3.0

        # 有检查清单 → +2分
        if checklist is not None and checklist.items:
            score += 2.0

        return min(15.0, score)

    # ── 每日检查清单生成 ────────────────────────────────────────

    def generate_daily_checklist(
        self, categories: list[str] | None = None
    ) -> DailyChecklist:
        """生成每日食品安全检查清单。"""
        items = []

        # 通用检查项
        items.append({
            "item": "洗手消毒 — 上岗前用洗手液彻底洗手",
            "category": "hygiene",
            "done": False,
            "notes": "",
        })
        items.append({
            "item": "工作服/围裙清洁 — 穿戴干净工作服",
            "category": "hygiene",
            "done": False,
            "notes": "",
        })
        items.append({
            "item": "台面/工具清洁 — 接触食品的表面和刀具/砧板已消毒",
            "category": "hygiene",
            "done": False,
            "notes": "",
        })
        items.append({
            "item": "垃圾清理 — 垃圾桶已清空并更换垃圾袋",
            "category": "hygiene",
            "done": False,
            "notes": "",
        })

        # 温度相关
        cats = categories or []
        has_cold = any(c in ("meat", "seafood", "dairy", "cooked_food") for c in cats)
        has_hot = "cooked_food" in cats

        if has_cold:
            items.append({
                "item": "冷藏设备温度检查 — 确认温度 < 4°C",
                "category": "temperature",
                "done": False,
                "notes": "",
            })

        if has_hot:
            items.append({
                "item": "热柜温度检查 — 确认温度 > 60°C",
                "category": "temperature",
                "done": False,
                "notes": "",
            })

        # 交叉污染
        if any(c in ("meat", "seafood") for c in cats) and "cooked_food" in cats:
            items.append({
                "item": "生熟分开 — 确认生肉/水产与熟食分区存放, 使用不同工具",
                "category": "hygiene",
                "done": False,
                "notes": "",
            })

        # 来源追溯
        items.append({
            "item": "进货记录 — 今日进货已记录来源和数量",
            "category": "traceability",
            "done": False,
            "notes": "",
        })

        # 保质期
        items.append({
            "item": "保质期检查 — 巡视所有商品, 临期品移至前排/促销区",
            "category": "expiry",
            "done": False,
            "notes": "",
        })

        return DailyChecklist(
            date=date.today().isoformat(),
            items=items,
            overall_result=InspectionResult.PASS,  # 默认待填写
        )


# ── 便捷函数 ──────────────────────────────────────────────────────
def quick_food_safety_check(
    cold_temp: float | None = None,
    hot_temp: float | None = None,
    category: str = "vegetable",
    checklist_done_ratio: float = 1.0,
) -> FoodSafetyScorecard:
    """快速食品安全检查。"""
    engine = FoodSafetyEngine()
    readings: list[CCPReading] = []

    if cold_temp is not None:
        readings.append(engine.check_temperature(cold_temp, category, "cold"))
    if hot_temp is not None:
        readings.append(engine.check_temperature(hot_temp, category, "hot"))

    # 模拟检查清单
    items = [
        {"item": "卫生检查", "done": True, "notes": ""},
        {"item": "温度检查", "done": checklist_done_ratio >= 0.5, "notes": ""},
        {"item": "保质期检查", "done": checklist_done_ratio >= 0.8, "notes": ""},
    ]
    checklist = DailyChecklist(
        date=date.today().isoformat(),
        items=items,
        overall_result=(
            InspectionResult.PASS if checklist_done_ratio >= 0.8
            else InspectionResult.CONDITIONAL_PASS
        ),
    )

    return engine.evaluate(readings, checklist)
