"""结构化语音解析测试 — 覆盖战略文档中的真实经营语句。"""

import pytest

from app.services.voice_parser_v2 import (
    BusinessEvent,
    CatalogResolver,
    parse_voice_text_v2,
)


class FakeResolver(CatalogResolver):
    """测试用：假装 DB 里 番茄 的 SKU=sku-tomato，标准单位 斤。"""

    def resolve_alias(self, alias):
        if alias == "番茄":
            return ("sku-tomato", "斤")
        return None


def test_purchase_with_supplier_package_and_net_weight():
    # 战略文档原句：从老王那里进了三筐西红柿，一共142斤，花了310块。
    ev: BusinessEvent = parse_voice_text_v2(
        "从老王那里进了三筐西红柿，一共142斤，花了310块",
        resolver=FakeResolver(),
        merchant_id="m1",
    )
    assert ev.event_type == "purchase"
    assert ev.supplier == "老王"
    assert ev.package_count == 3 and ev.package_unit == "筐"
    assert ev.net_qty == 142 and ev.net_unit == "斤"
    assert ev.total_amount == 310
    # 单斤成本 = 310 / 142 ≈ 2.18
    assert ev.unit_cost == pytest.approx(2.18, abs=0.01)
    assert ev.quantity_canonical == 142
    assert ev.sku_id == "sku-tomato"
    # 供应商/商品/数量/成本都齐了，无需确认
    assert ev.needs_confirmation is False


def test_sale_on_credit_detects_supplier_and_event():
    # 张记饭店今天拿了80块菜，先记账。
    ev = parse_voice_text_v2("张记饭店今天拿了80块菜，先记账", merchant_id="m1")
    assert ev.event_type == "sale"
    assert ev.supplier == "张记"
    assert ev.total_amount == 80
    # 「菜」不在别名表 → 缺商品 → 待确认
    assert "product" in ev.missing_fields
    assert ev.needs_confirmation is True


def test_unknown_event_must_confirm():
    ev = parse_voice_text_v2("今天生意不错", merchant_id="m1")
    assert ev.event_type == "unknown"
    assert ev.needs_confirmation is True


def test_waste_event():
    ev = parse_voice_text_v2("西红柿坏了5斤", resolver=FakeResolver(), merchant_id="m1")
    assert ev.event_type == "waste"
    assert ev.quantity_canonical == 5


def test_idempotency_key_is_deterministic():
    a = parse_voice_text_v2("从老王那里进了三筐西红柿，一共142斤，花了310块")
    b = parse_voice_text_v2("从老王那里进了三筐西红柿，一共142斤，花了310块")
    assert a.idempotency_key == b.idempotency_key


def test_idempotency_key_passthrough():
    ev = parse_voice_text_v2("测试", idempotency_key="client-abc-123")
    assert ev.idempotency_key == "client-abc-123"
