"""单位换算服务测试 — 验证账本口径统一逻辑（不依赖数据库）。"""

import pytest

from app.services.unit_service import convert, is_package_unit


def fake_lookup(merchant_id, from_unit, to_unit, sku_id=None):
    """测试用换算因子：一筐 = 45 斤。"""
    if from_unit == "筐" and to_unit == "斤":
        return 45.0
    return None


def test_weight_to_weight_builtin():
    assert convert(2, "公斤", "斤") == 4.0
    assert convert(100, "克", "斤") == pytest.approx(0.2)
    assert convert(1, "斤", "斤") == 1.0


def test_package_requires_lookup():
    # 包装单位没有内置因子，必须走 DB lookup，否则报错
    with pytest.raises(ValueError):
        convert(3, "筐", "斤")


def test_package_with_lookup():
    assert convert(3, "筐", "斤", lookup=fake_lookup, merchant_id="m", sku_id="s") == 135.0


def test_is_package_unit():
    assert is_package_unit("筐") is True
    assert is_package_unit("斤") is False
