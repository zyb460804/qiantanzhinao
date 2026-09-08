"""语音链路 P0 修复回归测试（2026-08 第五轮审计后落地）。

覆盖：
  1. 汉字数字/金额抽取（两/三/十五/八十/一万/一百零五/半斤/两斤半/花了80/15块）
  2. sale/purchase 缺金额 → missing_fields 含 amount（不再静默 0 元入账）
  3. 多意图 events 契约（events/event/parsed/warning 字段一字不差）
  4. correct 后 parsed_event 真正落库（JSON 列 flag_modified 回归）
  5. SKU 优先匹配（名称/别名/模糊包含 → sku_id + SKU 名，品类自动兜底）
  6. 未识别商品错误提示保留用户原词
  7. parse-text 空输入 422
"""

import importlib.util
import uuid

import pytest
from sqlalchemy import select
from tests.conftest import TEST_MERCHANT_ID

from app.models.catalog import ProductAlias, ProductSKU
from app.models.inventory import InventoryRecord
from app.models.product import ProductCategory
from app.services.voice_parser import parse_voice_events, parse_voice_text


pytestmark = pytest.mark.asyncio

MERCHANT_ID = uuid.UUID(TEST_MERCHANT_ID)

PRODUCT_NAMES = [
    "白菜",
    "土豆",
    "苹果",
    "猪肉",
    "西瓜",
    "豆腐",
]


# ---------------------------------------------------------------------------
# 1. 汉字数字 + 金额抽取（parser 单元）
# ---------------------------------------------------------------------------


class TestChineseNumerals:
    """讯飞 ASR 真实输出是汉字数字，必须全量识别。"""

    def test_liang_jin(self):
        """两斤 = 2"""
        r = parse_voice_text("卖了苹果两斤", PRODUCT_NAMES)
        assert r["quantity"] == 2.0
        assert r["unit"] == "斤"

    def test_san_jin(self):
        """三斤 = 3"""
        r = parse_voice_text("进了白菜三斤", PRODUCT_NAMES)
        assert r["quantity"] == 3.0

    def test_shiwu_kuai(self):
        """十五块 = 15 元（卖了3斤苹果15块的汉字形态）"""
        r = parse_voice_text("卖了苹果3斤十五块", PRODUCT_NAMES)
        assert r["event_type"] == "sale"
        assert r["quantity"] == 3.0
        assert r["total_amount"] == 15.0
        assert r["unit_price"] == 5.0
        assert "amount" not in r["missing_fields"]

    def test_bashi_kuai(self):
        """八十块 = 80 元（进了2箱苹果花了80的汉字形态）"""
        r = parse_voice_text("进了2箱苹果花了八十块", PRODUCT_NAMES)
        assert r["event_type"] == "purchase"
        assert r["quantity"] == 2.0
        assert r["unit"] == "箱"
        assert r["total_amount"] == 80.0
        assert r["unit_cost"] == 40.0

    def test_yiwan_jin(self):
        """一万斤 = 10000"""
        r = parse_voice_text("进了一万斤白菜", PRODUCT_NAMES)
        assert r["quantity"] == 10000.0

    def test_yibai_lingwu(self):
        """一百零五块 = 105 元"""
        r = parse_voice_text("卖了白菜一百零五块", PRODUCT_NAMES)
        assert r["total_amount"] == 105.0

    def test_ban_jin(self):
        """半斤 = 0.5"""
        r = parse_voice_text("卖了半斤猪肉", PRODUCT_NAMES)
        assert r["quantity"] == 0.5

    def test_liang_jin_ban(self):
        """两斤半 = 2.5"""
        r = parse_voice_text("进了土豆两斤半", PRODUCT_NAMES)
        assert r["quantity"] == 2.5

    def test_liang_jin_ban_with_amount(self):
        """两斤半 + 金额 → 单价正确（2.5 斤 30 块 = 12/斤）"""
        r = parse_voice_text("卖了猪肉两斤半三十块", PRODUCT_NAMES)
        assert r["quantity"] == 2.5
        assert r["total_amount"] == 30.0
        assert r["unit_price"] == 12.0

    def test_kuai_mao_combination(self):
        """3块5毛 = 3.5 元"""
        r = parse_voice_text("进了白菜10斤3块5毛一斤", PRODUCT_NAMES)
        assert r["quantity"] == 10.0
        assert r["unit_cost"] == 3.5


class TestAmountExtraction:
    """金额口语形态 + 缺金额显式提示。"""

    def test_pure_digit_bare_amount(self):
        """卖了3斤苹果15块（实测 P0：金额原来全丢）"""
        r = parse_voice_text("卖了3斤苹果15块", PRODUCT_NAMES)
        assert r["total_amount"] == 15.0
        assert r["unit_price"] == 5.0

    def test_hua_le_bare_digits(self):
        """进了2箱苹果花了80（裸金额，无货币单位）"""
        r = parse_voice_text("进了2箱苹果花了80", PRODUCT_NAMES)
        assert r["total_amount"] == 80.0
        assert r["quantity"] == 2.0
        assert r["unit_cost"] == 40.0

    def test_missing_amount_flagged_on_sale(self):
        """sale 缺金额 → missing_fields 含 amount"""
        r = parse_voice_text("卖了3斤苹果", PRODUCT_NAMES)
        assert r["total_amount"] is None
        assert "amount" in r["missing_fields"]

    def test_missing_amount_flagged_on_purchase(self):
        """purchase 缺金额 → missing_fields 含 amount"""
        r = parse_voice_text("进了白菜50斤", PRODUCT_NAMES)
        assert r["total_amount"] is None
        assert "amount" in r["missing_fields"]

    def test_unit_price_not_mistaken_as_total(self):
        """「两块钱一斤」是单价不是总额；总额按单价×数量推导。"""
        r = parse_voice_text("苹果两块钱一斤卖了5斤", PRODUCT_NAMES)
        assert r["product"] == "苹果"
        assert r["quantity"] == 5.0  # 单价短语里的 1斤 不算数量
        assert r["unit_price"] == 2.0
        assert r["total_amount"] == 10.0

    def test_single_event_comma_regression(self):
        """逗号补充说明仍按单事件解析（旧行为回归）。"""
        r = parse_voice_text("今天进了白菜50斤，三毛钱一斤", PRODUCT_NAMES)
        assert r["event_type"] == "purchase"
        assert r["product"] == "白菜"
        assert r["quantity"] == 50.0
        assert r["unit_cost"] == 0.3
        assert r["total_amount"] == 15.0


class TestMultiIntent:
    """多意图切分 + 数量就近绑定。"""

    def test_two_events_split_by_you(self):
        """卖了3斤猪肉又进了2斤白菜花了十块 → 2 笔，数量不错配。"""
        events = parse_voice_events("卖了3斤猪肉又进了2斤白菜花了十块", PRODUCT_NAMES)
        assert len(events) == 2

        sale, purchase = events
        assert sale["event_type"] == "sale"
        assert sale["product"] == "猪肉"
        assert sale["quantity"] == 3.0
        assert sale["total_amount"] is None
        assert "amount" in sale["missing_fields"]

        assert purchase["event_type"] == "purchase"
        assert purchase["product"] == "白菜"
        assert purchase["quantity"] == 2.0  # 3 不再错配给白菜
        assert purchase["total_amount"] == 10.0
        assert purchase["unit_cost"] == 5.0  # 不再出现 10/3 的错误单价

    def test_trailing_clause_attaches_to_previous_event(self):
        """无关键词分句并入前一笔（卖了苹果3斤，15块）。"""
        events = parse_voice_events("卖了苹果3斤，15块，又进了白菜2斤，8块", PRODUCT_NAMES)
        assert len(events) == 2
        assert events[0]["total_amount"] == 15.0
        assert events[1]["total_amount"] == 8.0
        assert events[1]["unit_cost"] == 4.0

    def test_parse_voice_text_returns_first_event(self):
        """兼容入口返回第 1 笔。"""
        first = parse_voice_text("卖了3斤猪肉又进了2斤白菜花了十块", PRODUCT_NAMES)
        assert first["event_type"] == "sale"
        assert first["product"] == "猪肉"

    def test_single_keyword_clause_is_single_event(self):
        """仅一个事件关键词分句 → 整句单事件（保护旧行为）。"""
        events = parse_voice_events("今天进了白菜50斤，三毛钱一斤", PRODUCT_NAMES)
        assert len(events) == 1
        assert events[0]["product"] == "白菜"


# ---------------------------------------------------------------------------
# 2. parse-text 输入校验 / 多意图响应契约（API）
# ---------------------------------------------------------------------------


class TestParseTextValidation:
    async def test_empty_text_rejected(self, client):
        resp = await client.post(
            "/api/v1/voice/parse-text", json={"text": "", "merchant_id": TEST_MERCHANT_ID}
        )
        assert resp.status_code == 422
        assert "请说出或输入要记的内容" in resp.json()["detail"]

    async def test_whitespace_text_rejected(self, client):
        resp = await client.post(
            "/api/v1/voice/parse-text", json={"text": "   ", "merchant_id": TEST_MERCHANT_ID}
        )
        assert resp.status_code == 422


class TestMultiIntentResponseContract:
    """events / event / parsed / warning 契约字段（小程序端按字段名消费）。"""

    async def test_events_fields(self, client):
        resp = await client.post(
            "/api/v1/voice/parse-text",
            json={
                "merchant_id": TEST_MERCHANT_ID,
                "text": "卖了3斤猪肉又进了2斤白菜花了十块",
            },
        )
        assert resp.status_code == 200
        data = resp.json()["data"]

        assert len(data["events"]) == 2
        assert data["event"] == data["parsed"]
        assert data["parsed"] == data["events"][0]
        assert data["warning"] == "检测到2笔，仅返回第1笔"

        assert data["events"][0]["event_type"] == "sale"
        assert data["events"][0]["product"] == "猪肉"
        assert data["events"][1]["event_type"] == "purchase"
        assert data["events"][1]["total_amount"] == 10.0

    async def test_single_event_has_no_warning(self, client):
        resp = await client.post(
            "/api/v1/voice/parse-text",
            json={"merchant_id": TEST_MERCHANT_ID, "text": "进了白菜50斤，三毛钱一斤"},
        )
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert len(data["events"]) == 1
        assert data["warning"] is None


# ---------------------------------------------------------------------------
# 3. correct 落库回归（JSON 列 flag_modified）
# ---------------------------------------------------------------------------


class TestCorrectPersistence:
    async def test_correct_persists_parsed_event(self, client):
        """correct 后重新查库：修正值必须已落库（修复前 UPDATE 不生成）。"""
        parse = await client.post(
            "/api/v1/voice/parse-text",
            json={"merchant_id": TEST_MERCHANT_ID, "text": "进了白菜50斤"},
        )
        log_id = parse.json()["data"]["parsed"]["voice_log_id"]

        resp = await client.post(
            "/api/v1/voice/correct",
            json={
                "voice_log_id": log_id,
                "corrections": {"quantity": 3, "total_amount": 15},
            },
        )
        assert resp.status_code == 200
        assert resp.json()["data"]["parsed"]["quantity"] == 3

        # 重新查库（新请求 = 新 session），断言 UPDATE 真正落库
        logs = await client.get(
            "/api/v1/voice/logs", params={"merchant_id": TEST_MERCHANT_ID, "page": 1, "limit": 50}
        )
        row = next(item for item in logs.json()["data"] if item["id"] == log_id)
        assert row["parsed_event"]["quantity"] == 3.0
        assert row["parsed_event"]["total_amount"] == 15.0
        assert row["parsed_event"]["missing_fields"] == []

    async def test_confirm_uses_corrected_values(self, client):
        """缺金额 → correct 补金额 → confirm 按修正后金额入账。"""
        parse = await client.post(
            "/api/v1/voice/parse-text",
            json={"merchant_id": TEST_MERCHANT_ID, "text": "进了白菜50斤"},
        )
        log_id = parse.json()["data"]["parsed"]["voice_log_id"]
        assert "amount" in parse.json()["data"]["parsed"]["missing_fields"]

        await client.post(
            "/api/v1/voice/correct",
            json={"voice_log_id": log_id, "corrections": {"total_amount": 25}},
        )
        confirm = await client.post("/api/v1/voice/confirm", json={"voice_log_id": log_id})
        assert confirm.status_code == 200
        assert float(confirm.json()["data"]["total_amount"]) == 25.0


# ---------------------------------------------------------------------------
# 4. SKU 优先匹配
# ---------------------------------------------------------------------------


async def _seed_tomato_sku(db_session, *, name: str = "西红柿") -> uuid.UUID:
    """为本商户建 SKU + 别名「洋柿子」。"""
    async with db_session() as session:
        sku = ProductSKU(
            merchant_id=MERCHANT_ID,
            name=name,
            canonical_unit="斤",
            shelf_life_hours=96,
        )
        session.add(sku)
        await session.flush()
        session.add(ProductAlias(merchant_id=MERCHANT_ID, sku_id=sku.id, alias="洋柿子"))
        await session.commit()
        return sku.id


class TestSkuPriorityMatch:
    async def test_parse_matches_sku_by_name(self, client, db_session):
        sku_id = await _seed_tomato_sku(db_session)
        resp = await client.post(
            "/api/v1/voice/parse-text",
            json={"merchant_id": TEST_MERCHANT_ID, "text": "进了西红柿20斤花了40块"},
        )
        parsed = resp.json()["data"]["parsed"]
        assert parsed["product"] == "西红柿"
        assert parsed["sku_id"] == str(sku_id)
        assert parsed["total_amount"] == 40.0

    async def test_parse_matches_sku_by_alias(self, client, db_session):
        """别名「洋柿子」命中 → product 归一为 SKU 名「西红柿」。"""
        sku_id = await _seed_tomato_sku(db_session)
        resp = await client.post(
            "/api/v1/voice/parse-text",
            json={"merchant_id": TEST_MERCHANT_ID, "text": "卖了3斤洋柿子15块"},
        )
        parsed = resp.json()["data"]["parsed"]
        assert parsed["product"] == "西红柿"
        assert parsed["sku_id"] == str(sku_id)
        assert parsed["product_word"] == "洋柿子"

    async def test_confirm_books_on_sku_and_creates_category(self, client, db_session):
        """建了「西红柿」SKU 后 confirm：账本挂 sku_id，品类自动兜底创建。"""
        sku_id = await _seed_tomato_sku(db_session)

        purchase = await client.post(
            "/api/v1/voice/parse-text",
            json={"merchant_id": TEST_MERCHANT_ID, "text": "进了西红柿20斤花了40块"},
        )
        purchase_log = purchase.json()["data"]["parsed"]["voice_log_id"]
        assert (
            await client.post("/api/v1/voice/confirm", json={"voice_log_id": purchase_log})
        ).status_code == 200

        sale = await client.post(
            "/api/v1/voice/parse-text",
            json={"merchant_id": TEST_MERCHANT_ID, "text": "卖了2斤西红柿"},
        )
        sale_log = sale.json()["data"]["parsed"]["voice_log_id"]
        await client.post(
            "/api/v1/voice/correct",
            json={"voice_log_id": sale_log, "corrections": {"total_amount": 10}},
        )
        confirmed = await client.post(
            "/api/v1/voice/confirm", json={"voice_log_id": sale_log}
        )
        assert confirmed.status_code == 200
        assert confirmed.json()["data"]["product"] == "西红柿"

        async with db_session() as session:
            records = (
                (
                    await session.execute(
                        select(InventoryRecord).where(InventoryRecord.sku_id == sku_id)
                    )
                )
                .scalars()
                .all()
            )
            assert len(records) == 2
            quantities = sorted(float(r.quantity) for r in records)
            assert quantities == [-2.0, 20.0]
            assert all(r.sku_id == sku_id for r in records)
            # 品类按 SKU 名自动兜底创建（product_id 非空约束）
            category = (
                await session.execute(
                    select(ProductCategory).where(ProductCategory.name == "西红柿")
                )
            ).scalar_one_or_none()
            assert category is not None
            assert records[0].product_id == category.id


class TestUnknownProductKeepsOriginalWord:
    async def test_parse_keeps_product_word(self, client):
        """未识别商品保留原词（不再只有 product=None）。"""
        resp = await client.post(
            "/api/v1/voice/parse-text",
            json={"merchant_id": TEST_MERCHANT_ID, "text": "卖了3斤火龙果10块"},
        )
        parsed = resp.json()["data"]["parsed"]
        assert parsed["product"] is None
        assert parsed["product_word"] == "火龙果"

    async def test_confirm_error_shows_original_word(self, client):
        parse = await client.post(
            "/api/v1/voice/parse-text",
            json={"merchant_id": TEST_MERCHANT_ID, "text": "卖了3斤火龙果10块"},
        )
        log_id = parse.json()["data"]["parsed"]["voice_log_id"]
        confirm = await client.post("/api/v1/voice/confirm", json={"voice_log_id": log_id})
        assert confirm.status_code == 400
        assert "火龙果" in confirm.json()["detail"]
        assert "未知商品" not in confirm.json()["detail"]


# ---------------------------------------------------------------------------
# 5. 单位换算接入（A1 unit_conversion 联调）
# ---------------------------------------------------------------------------

_HAS_UNIT_CONVERSION = importlib.util.find_spec("app.services.unit_conversion") is not None


class TestUnitConversion:
    async def test_base_unit_sale_books_normally(self, client, db_session):
        """SKU 基准单位（斤）卖出：换算服务在或不在都按原数量入账。"""
        sku_id = await _seed_tomato_sku(db_session)
        purchase = await client.post(
            "/api/v1/voice/parse-text",
            json={"merchant_id": TEST_MERCHANT_ID, "text": "进了西红柿20斤花了40块"},
        )
        purchase_log = purchase.json()["data"]["parsed"]["voice_log_id"]
        assert (
            await client.post("/api/v1/voice/confirm", json={"voice_log_id": purchase_log})
        ).status_code == 200

        sale = await client.post(
            "/api/v1/voice/parse-text",
            json={"merchant_id": TEST_MERCHANT_ID, "text": "卖了2斤西红柿"},
        )
        sale_log = sale.json()["data"]["parsed"]["voice_log_id"]
        await client.post(
            "/api/v1/voice/correct",
            json={"voice_log_id": sale_log, "corrections": {"total_amount": 10}},
        )
        resp = await client.post("/api/v1/voice/confirm", json={"voice_log_id": sale_log})
        assert resp.status_code == 200
        assert resp.json()["data"]["quantity"] == 2.0

        async with db_session() as session:
            record = (
                await session.execute(
                    select(InventoryRecord).where(
                        InventoryRecord.sku_id == sku_id,
                        InventoryRecord.event_type == "sale",
                    )
                )
            ).scalar_one()
            assert float(record.quantity) == -2.0
            assert record.unit == "斤"

    @pytest.mark.skipif(
        not _HAS_UNIT_CONVERSION,
        reason="A1 的 unit_conversion 服务尚未落地，联调后启用",
    )
    async def test_missing_conversion_rule_rejected_with_hint(self, client, db_session):
        """无换算规则的非基准单位卖出 → 409 引导设置换算（A1 联调用例）。"""
        await _seed_tomato_sku(db_session)
        purchase = await client.post(
            "/api/v1/voice/parse-text",
            json={"merchant_id": TEST_MERCHANT_ID, "text": "进了西红柿20斤花了40块"},
        )
        purchase_log = purchase.json()["data"]["parsed"]["voice_log_id"]
        assert (
            await client.post("/api/v1/voice/confirm", json={"voice_log_id": purchase_log})
        ).status_code == 200

        sale = await client.post(
            "/api/v1/voice/parse-text",
            json={"merchant_id": TEST_MERCHANT_ID, "text": "卖了2箱西红柿"},
        )
        sale_log = sale.json()["data"]["parsed"]["voice_log_id"]
        resp = await client.post("/api/v1/voice/confirm", json={"voice_log_id": sale_log})
        assert resp.status_code == 409
        detail = resp.json()["detail"]
        assert "库存不足" in detail
        assert "单位换算" in detail
        assert "箱" in detail
