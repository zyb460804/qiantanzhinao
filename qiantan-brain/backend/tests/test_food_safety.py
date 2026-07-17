"""Tests for food safety compliance engine."""
import math
from datetime import datetime, timedelta

import pytest

from app.services.food_safety import (
    CCPDefinition,
    CCPReading,
    CCPStatus,
    DailyChecklist,
    DEFAULT_CCPS,
    FoodSafetyEngine,
    FoodSafetyScorecard,
    InspectionResult,
    NCRRecord,
    NCRSeverity,
    quick_food_safety_check,
)


class TestCCPDefinitions:
    """CCP定义测试。"""

    def test_default_ccps_loaded(self):
        """默认CCP已加载。"""
        assert len(DEFAULT_CCPS) == 6
        codes = {c.code for c in DEFAULT_CCPS}
        assert codes == {"CCP-1", "CCP-2", "CCP-3", "CCP-4", "CCP-5", "CCP-6"}

    def test_each_ccp_has_required_fields(self):
        """每个CCP有所有必要字段。"""
        for ccp in DEFAULT_CCPS:
            assert ccp.code
            assert ccp.name
            assert ccp.critical_limit
            assert ccp.check_frequency
            assert ccp.corrective_action
            assert ccp.applicable_categories


class TestTemperatureCheck:
    """温度合规检查测试。"""

    def test_cold_storage_ok(self):
        """冷藏温度正常。"""
        engine = FoodSafetyEngine()
        reading = engine.check_temperature(2.0, "meat", "cold")
        assert reading.status == CCPStatus.OK
        assert reading.ccp_code == "CCP-1"

    def test_cold_storage_warning(self):
        """冷藏温度偏高警告。"""
        engine = FoodSafetyEngine()
        reading = engine.check_temperature(3.5, "meat", "cold")
        assert reading.status == CCPStatus.WARNING

    def test_cold_storage_violation(self):
        """冷藏温度超标。"""
        engine = FoodSafetyEngine()
        reading = engine.check_temperature(8.0, "meat", "cold")
        assert reading.status == CCPStatus.VIOLATION

    def test_hot_storage_ok(self):
        """热柜温度正常。"""
        engine = FoodSafetyEngine()
        reading = engine.check_temperature(70.0, "cooked_food", "hot")
        assert reading.status == CCPStatus.OK

    def test_hot_storage_violation(self):
        """热柜温度过低。"""
        engine = FoodSafetyEngine()
        reading = engine.check_temperature(40.0, "cooked_food", "hot")
        assert reading.status == CCPStatus.VIOLATION

    def test_category_not_applicable(self):
        """干货不需要温度监控。"""
        engine = FoodSafetyEngine()
        reading = engine.check_temperature(25.0, "dry_goods", "cold")
        assert reading.status == CCPStatus.NOT_APPLICABLE

    def test_vegetable_higher_threshold(self):
        """蔬菜冷藏阈值比肉类高。"""
        engine = FoodSafetyEngine()
        # 6°C for vegetable is OK (threshold < 8°C)
        reading = engine.check_temperature(6.0, "vegetable", "cold")
        assert reading.status == CCPStatus.OK
        # 6°C for meat is VIOLATION (threshold < 4°C)
        reading2 = engine.check_temperature(6.0, "meat", "cold")
        assert reading2.status == CCPStatus.VIOLATION


class TestTimeLimitCheck:
    """加工时间控制测试。"""

    def test_time_within_limit(self):
        """时间在安全范围内。"""
        engine = FoodSafetyEngine()
        reading = engine.check_time_limit(2.0, ambient_temp_c=25.0)
        assert reading.status == CCPStatus.OK

    def test_time_over_limit(self):
        """时间超标。"""
        engine = FoodSafetyEngine()
        reading = engine.check_time_limit(5.0, ambient_temp_c=25.0)
        assert reading.status == CCPStatus.VIOLATION

    def test_time_hot_weather_stricter(self):
        """高温天气时限更严。"""
        engine = FoodSafetyEngine()
        # 33°C: safe=2h, warn=1.5h
        reading = engine.check_time_limit(3.0, ambient_temp_c=35.0)
        assert reading.status == CCPStatus.VIOLATION

    def test_time_warning(self):
        """接近限值触发警告。"""
        engine = FoodSafetyEngine()
        reading = engine.check_time_limit(3.5, ambient_temp_c=25.0)
        assert reading.status == CCPStatus.WARNING


class TestExpiryCheck:
    """保质期追踪测试。"""

    def test_fresh_item_ok(self):
        """新到货检查正常。"""
        engine = FoodSafetyEngine()
        result = engine.check_expiry(
            arrival_time=datetime.now() - timedelta(hours=1),
            category="vegetable",
        )
        assert result["status"] == "ok"
        assert result["quality_factor"] > 0.9

    def test_near_expiry_warning(self):
        """临期商品警告。"""
        engine = FoodSafetyEngine()
        shelf_life = FoodSafetyEngine.CATEGORY_SHELF_LIFE["vegetable"]  # 72h
        arrival = datetime.now() - timedelta(hours=shelf_life * 0.8)
        result = engine.check_expiry(arrival_time=arrival, category="vegetable")
        assert result["status"] in ("warning", "critical")

    def test_expired_item(self):
        """过期商品。"""
        engine = FoodSafetyEngine()
        shelf_life = FoodSafetyEngine.CATEGORY_SHELF_LIFE["seafood"]  # 24h
        arrival = datetime.now() - timedelta(hours=shelf_life + 1)
        result = engine.check_expiry(arrival_time=arrival, category="seafood")
        assert result["status"] == "expired"
        assert "丢弃" in result["recommendation"]

    def test_shorter_shelf_life_categories(self):
        """短保质期品类。"""
        engine = FoodSafetyEngine()
        assert FoodSafetyEngine.CATEGORY_SHELF_LIFE["seafood"] < FoodSafetyEngine.CATEGORY_SHELF_LIFE["fruit"]
        assert FoodSafetyEngine.CATEGORY_SHELF_LIFE["cooked_food"] < FoodSafetyEngine.CATEGORY_SHELF_LIFE["vegetable"]


class TestNCRGeneration:
    """NCR自动生成测试。"""

    def test_violation_generates_ncr(self):
        """超标生成NCR。"""
        engine = FoodSafetyEngine()
        reading = CCPReading(
            ccp_code="CCP-1",
            value=12.0,
            unit="°C",
            status=CCPStatus.VIOLATION,
            checked_at=datetime.now().isoformat(),
        )
        ncr = engine.generate_ncr(reading)
        assert ncr is not None
        assert ncr.ccp_code == "CCP-1"
        assert ncr.severity in (NCRSeverity.CRITICAL, NCRSeverity.MAJOR, NCRSeverity.MINOR)

    def test_cold_temp_critical_ncr(self):
        """冷藏温度严重超标 → CRITICAL NCR。"""
        engine = FoodSafetyEngine()
        reading = CCPReading(
            ccp_code="CCP-1",
            value=15.0,  # 远高于4°C
            unit="°C",
            status=CCPStatus.VIOLATION,
            checked_at=datetime.now().isoformat(),
        )
        ncr = engine.generate_ncr(reading)
        assert ncr is not None
        assert ncr.severity == NCRSeverity.CRITICAL

    def test_hot_temp_critical_ncr(self):
        """热柜严重低于阈值 → CRITICAL NCR。"""
        engine = FoodSafetyEngine()
        reading = CCPReading(
            ccp_code="CCP-2",
            value=30.0,  # 远低于60°C
            unit="°C",
            status=CCPStatus.VIOLATION,
            checked_at=datetime.now().isoformat(),
        )
        ncr = engine.generate_ncr(reading)
        assert ncr is not None
        assert ncr.severity == NCRSeverity.CRITICAL

    def test_time_limit_critical_ncr(self):
        """加工时间严重超标 → CRITICAL NCR。"""
        engine = FoodSafetyEngine()
        reading = CCPReading(
            ccp_code="CCP-3",
            value=8.0,  # 8小时, 远超4小时
            unit="小时",
            status=CCPStatus.VIOLATION,
            checked_at=datetime.now().isoformat(),
        )
        ncr = engine.generate_ncr(reading)
        assert ncr is not None
        assert ncr.severity == NCRSeverity.CRITICAL
        assert ncr.capa_required

    def test_warning_no_ncr(self):
        """警告级别不生成NCR。"""
        engine = FoodSafetyEngine()
        reading = CCPReading(
            ccp_code="CCP-1",
            value=3.5,
            unit="°C",
            status=CCPStatus.WARNING,
            checked_at=datetime.now().isoformat(),
        )
        ncr = engine.generate_ncr(reading)
        assert ncr is None

    def test_ncr_has_corrective_action(self):
        """NCR包含纠正措施。"""
        engine = FoodSafetyEngine()
        reading = CCPReading(
            ccp_code="CCP-1",
            value=10.0,
            unit="°C",
            status=CCPStatus.VIOLATION,
            checked_at=datetime.now().isoformat(),
        )
        ncr = engine.generate_ncr(reading)
        assert ncr is not None
        assert ncr.correction is not None


class TestFoodSafetyScorecard:
    """综合评分卡测试。"""

    def _make_all_ok_readings(self) -> list[CCPReading]:
        """创建全部合格的CCP读数。"""
        engine = FoodSafetyEngine()
        now = datetime.now().isoformat()
        return [
            CCPReading("CCP-1", 2.0, "°C", CCPStatus.OK, now),
            CCPReading("CCP-2", 70.0, "°C", CCPStatus.OK, now),
            CCPReading("CCP-4", 0, "次", CCPStatus.OK, now),
            CCPReading("CCP-5", 1, "次", CCPStatus.OK, now),
            CCPReading("CCP-6", 0, "次", CCPStatus.OK, now),
        ]

    def _make_pass_checklist(self) -> DailyChecklist:
        """创建通过的检查清单。"""
        return DailyChecklist(
            date="2025-01-15",
            items=[
                {"item": "洗手消毒", "done": True, "notes": ""},
                {"item": "台面清洁", "done": True, "notes": ""},
                {"item": "温度检查", "done": True, "notes": "2°C"},
            ],
            overall_result=InspectionResult.PASS,
        )

    def test_perfect_scorecard(self):
        """完美合规得高分。"""
        engine = FoodSafetyEngine()
        scorecard = engine.evaluate(
            ccp_readings=self._make_all_ok_readings(),
            daily_checklist=self._make_pass_checklist(),
            supplier_cert_count=5,
            total_supplier_count=5,
        )
        assert scorecard.overall_score >= 85
        assert scorecard.grade in ("A", "B")
        assert scorecard.risk_level == "low"
        assert scorecard.open_ncrs == 0

    def test_poor_compliance_low_score(self):
        """不合规得低分。"""
        engine = FoodSafetyEngine()
        violations = [
            CCPReading("CCP-1", 15.0, "°C", CCPStatus.VIOLATION, ""),
            CCPReading("CCP-2", 30.0, "°C", CCPStatus.VIOLATION, ""),
            CCPReading("CCP-3", 6.0, "小时", CCPStatus.VIOLATION, ""),
            CCPReading("CCP-6", 1, "次", CCPStatus.VIOLATION, ""),
        ]
        scorecard = engine.evaluate(
            ccp_readings=violations,
            daily_checklist=None,
            supplier_cert_count=0,
            total_supplier_count=5,
        )
        assert scorecard.overall_score < 50
        assert scorecard.open_ncrs > 0
        assert scorecard.risk_level in ("high", "critical")

    def test_scorecard_has_all_subscores(self):
        """评分卡包含所有子评分。"""
        engine = FoodSafetyEngine()
        scorecard = engine.evaluate(
            ccp_readings=self._make_all_ok_readings(),
        )
        assert scorecard.temperature_score >= 0
        assert scorecard.hygiene_score >= 0
        assert scorecard.traceability_score >= 0
        assert scorecard.expiry_score >= 0
        assert scorecard.documentation_score >= 0
        total = sum([
            scorecard.temperature_score,
            scorecard.hygiene_score,
            scorecard.traceability_score,
            scorecard.expiry_score,
            scorecard.documentation_score,
        ])
        assert abs(total - scorecard.overall_score) < 1.0

    def test_scorecard_with_expiry_items(self):
        """有过期商品影响评分。"""
        engine = FoodSafetyEngine()
        expiry_items = [
            {"status": "expired", "product": "鱼"},
            {"status": "critical", "product": "肉"},
            {"status": "ok", "product": "白菜"},
        ]
        scorecard = engine.evaluate(
            ccp_readings=self._make_all_ok_readings(),
            expiry_items=expiry_items,
        )
        assert scorecard.expiry_score < 12  # 有expired+critical, 扣分

    def test_missing_supplier_certs(self):
        """供应商证照不全扣分。"""
        engine = FoodSafetyEngine()
        scorecard = engine.evaluate(
            ccp_readings=self._make_all_ok_readings(),
            supplier_cert_count=1,
            total_supplier_count=10,
        )
        assert scorecard.traceability_score < 10


class TestDailyChecklist:
    """每日检查清单测试。"""

    def test_generate_checklist_basic(self):
        """生成基础检查清单。"""
        engine = FoodSafetyEngine()
        checklist = engine.generate_daily_checklist()
        assert len(checklist.items) > 0
        for item in checklist.items:
            assert "item" in item
            assert "done" in item
            assert item["done"] is False  # 默认未完成

    def test_generate_checklist_with_categories(self):
        """根据品类生成不同的检查项。"""
        engine = FoodSafetyEngine()
        basic = engine.generate_daily_checklist(categories=["vegetable"])
        with_meat = engine.generate_daily_checklist(
            categories=["meat", "seafood", "cooked_food"]
        )
        # 含肉类应有更多检查项 (冷藏+热柜+交叉污染)
        assert len(with_meat.items) > len(basic.items)

    def test_checklist_has_cross_contamination_for_mixed(self):
        """生熟混卖时包含交叉污染检查。"""
        engine = FoodSafetyEngine()
        checklist = engine.generate_daily_checklist(
            categories=["meat", "cooked_food"]
        )
        items_text = " ".join(item["item"] for item in checklist.items)
        assert "生熟分开" in items_text


class TestConvenienceFunctions:
    """便捷函数测试。"""

    def test_quick_check_perfect(self):
        """快速检查 — 完美场景。"""
        scorecard = quick_food_safety_check(
            cold_temp=2.0,
            hot_temp=70.0,
            category="meat",
            checklist_done_ratio=1.0,
        )
        assert isinstance(scorecard, FoodSafetyScorecard)
        assert scorecard.overall_score > 70

    def test_quick_check_poor(self):
        """快速检查 — 有问题。"""
        scorecard = quick_food_safety_check(
            cold_temp=15.0,
            category="seafood",
            checklist_done_ratio=0.3,
        )
        assert scorecard.overall_score < 60


class TestEdgeCases:
    """边界情况。"""

    def test_unknown_category_temperature(self):
        """未知品类温度检查。"""
        engine = FoodSafetyEngine()
        reading = engine.check_temperature(5.0, "unknown_category", "cold")
        # 应不崩溃, 给默认处理
        assert reading is not None

    def test_zero_shelf_life_category(self):
        """自定义品类保质期。"""
        engine = FoodSafetyEngine()
        # dry_goods 有2160小时保质期
        result = engine.check_expiry(
            arrival_time=datetime.now() - timedelta(hours=100),
            category="dry_goods",
        )
        assert result["status"] == "ok"  # 100小时对干货不算什么

    def test_empty_readings_scorecard(self):
        """无CCP读数也可评分。"""
        engine = FoodSafetyEngine()
        scorecard = engine.evaluate(ccp_readings=[])
        assert scorecard.overall_score >= 0
        assert scorecard.grade is not None

    def test_generate_ncr_unknown_ccp(self):
        """未知CCP不崩溃。"""
        engine = FoodSafetyEngine()
        reading = CCPReading(
            ccp_code="CCP-999",
            value=0,
            unit="°C",
            status=CCPStatus.VIOLATION,
            checked_at="",
        )
        ncr = engine.generate_ncr(reading)
        assert ncr is None
