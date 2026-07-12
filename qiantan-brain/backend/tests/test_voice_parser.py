"""Unit tests for voice semantic parser."""

import sys
from pathlib import Path


# Add backend to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from app.services.voice_parser import parse_voice_text


PRODUCT_NAMES = [
    "白菜",
    "菠菜",
    "生菜",
    "青菜",
    "韭菜",
    "土豆",
    "萝卜",
    "胡萝卜",
    "红薯",
    "洋葱",
    "豆腐",
    "豆皮",
    "豆干",
    "黄瓜",
    "番茄",
    "辣椒",
    "西瓜",
    "苹果",
    "香蕉",
    "橙子",
    "葡萄",
    "猪肉",
    "牛肉",
    "鸡肉",
]


class TestVoiceParser:
    """Test the voice text parsing engine."""

    def test_parse_purchase_basic(self):
        """Basic purchase: '今天进了白菜50斤，三毛钱一斤'"""
        result = parse_voice_text("今天进了白菜50斤，三毛钱一斤", PRODUCT_NAMES)
        assert result["event_type"] == "purchase"
        assert result["product"] == "白菜"
        assert result["quantity"] == 50.0
        assert result["unit"] == "斤"
        assert result["unit_cost"] is not None

    def test_parse_purchase_with_spoken_numbers(self):
        """Spoken Chinese numbers: '进了土豆三十斤，一块二一斤'"""
        result = parse_voice_text("进了土豆三十斤，一块二一斤", PRODUCT_NAMES)
        assert result["event_type"] == "purchase"
        assert result["product"] == "土豆"

    def test_parse_sale(self):
        """Sale: '卖了西瓜20斤，两块钱一斤，一共卖了40块'"""
        result = parse_voice_text("卖了西瓜20斤，两块钱一斤，一共卖了40块", PRODUCT_NAMES)
        assert result["event_type"] == "sale"
        assert result["product"] == "西瓜"

    def test_parse_waste(self):
        """Waste disposal: '扔了烂白菜3斤'"""
        result = parse_voice_text("扔了烂白菜3斤", PRODUCT_NAMES)
        assert result["event_type"] == "waste"
        assert result["product"] == "白菜"

    def test_parse_unknown_defaults_to_purchase(self):
        """Unknown event type defaults to purchase."""
        result = parse_voice_text("白菜50斤", PRODUCT_NAMES)
        assert result["event_type"] == "purchase"

    def test_confidence_high_on_complete(self):
        """High confidence when all fields present."""
        result = parse_voice_text("进了白菜50斤，一共花了15块", PRODUCT_NAMES)
        assert result["confidence"] >= 0.7

    def test_confidence_low_on_missing_product(self):
        """Low confidence when product not recognized."""
        result = parse_voice_text("进了50斤，三毛钱一斤", PRODUCT_NAMES)
        assert result["product"] is None
        assert result["confidence"] <= 0.90

    def test_missing_fields_tracked(self):
        """Missing fields are listed."""
        result = parse_voice_text("进了50斤", PRODUCT_NAMES)
        assert "product" in result["missing_fields"]


if __name__ == "__main__":
    # Quick manual test run
    test = TestVoiceParser()
    test.test_parse_purchase_basic()
    test.test_parse_sale()
    test.test_parse_waste()
    test.test_parse_unknown_defaults_to_purchase()
    test.test_confidence_high_on_complete()
    test.test_confidence_low_on_missing_product()
    test.test_missing_fields_tracked()
    print("All voice parser tests passed!")
