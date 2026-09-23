"""QA 修复 V（2026-09-22 语音链路一族）回归测试。

覆盖缺陷：
  QA-24  「进货赣南橙」动词残留 → 多字动词整体消费，product_word=赣南橙
  QA-27  独立「1斤2块」纯单价短语 → 不产出 purchase 事件，confirm 自然拒绝
  QA-19  模糊匹配劫持 → 「进货F测芦笋」不再静默改写成既有 SKU；模糊路径
         （词串延展）带 warning + confidence 打折；白菜正例不受影响
  QA-08  语音→费用 confirm 的 description 净化（复用 catalog._sanitize_display_name）
  QA-29  parse-text 500 字上限，超限 422
  QA-30  voice/upload 魔数校验：合法 WAV 通过，exe 字节改名 .wav 拒绝
"""

import uuid

import pytest
from sqlalchemy import select
from tests.conftest import TEST_MERCHANT_ID

from app.models.catalog import ProductSKU
from app.models.expense import Expense
from app.services.voice_parser import parse_voice_events, parse_voice_text


pytestmark = pytest.mark.asyncio

PRODUCT_NAMES = [
    "白菜",
    "土豆",
    "苹果",
    "猪肉",
    "西瓜",
    "豆腐",
    "韭菜",
    "红薯",
    "橙子",
]

MERCHANT_ID = uuid.UUID(TEST_MERCHANT_ID)


# ---------------------------------------------------------------------------
# QA-24：多字动词整体消费，不残留尾字进入品名
# ---------------------------------------------------------------------------


class TestQa24VerbResidue:
    def test_jinnan_orange_no_residue(self):
        """「进货赣南橙1斤4块」实测：product_word=赣南橙（此前=货赣南橙）。"""
        r = parse_voice_text("进货赣南橙1斤4块", PRODUCT_NAMES)
        assert r["product"] is None  # 词表无赣南橙，也不得被「橙子」模糊劫持
        assert r["product_word"] == "赣南橙"
        assert r["quantity"] == 1.0
        assert r["total_amount"] == 4.0
        assert r["event_type"] == "purchase"

    @pytest.mark.parametrize(
        "text,word",
        [
            ("购进赣南橙20斤", "赣南橙"),
            ("买进赣南橙30斤60块", "赣南橙"),
            ("进了赣南橙2斤", "赣南橙"),
        ],
    )
    def test_multi_char_verbs_consumed(self, text, word):
        """进货/买进/购进/进了 等多字动词整体消费（补全动词表）。"""
        assert parse_voice_text(text, PRODUCT_NAMES)["product_word"] == word

    def test_existing_verb_prefix_cases_unchanged(self):
        """既有动词剥离用例回归：进货葱→葱、火龙果原词不变。"""
        assert parse_voice_text("进货葱20斤", PRODUCT_NAMES)["product_word"] == "葱"
        assert parse_voice_text("卖了3斤火龙果10块", PRODUCT_NAMES)["product_word"] == "火龙果"

    def test_buy_in_jiucai_via_list(self):
        """「买进韭菜30斤60块」→ 韭菜（词表精确命中，QA-19 收紧不得影响）。"""
        r = parse_voice_text("买进韭菜30斤60块", PRODUCT_NAMES)
        assert r["product"] == "韭菜"
        assert r["warning"] is None
        assert r["quantity"] == 30.0
        assert r["total_amount"] == 60.0


# ---------------------------------------------------------------------------
# QA-27：独立「1斤2块」纯单价短语不产出业务事件
# ---------------------------------------------------------------------------


class TestQa27BarePricePhrase:
    def test_bare_price_phrase_produces_no_event(self):
        """「1斤2块」→ 无 purchase 事件（此前误记 1 斤 2 元进货）。"""
        assert parse_voice_events("1斤2块", PRODUCT_NAMES) == []

    def test_bare_price_phrase_placeholder_shape(self):
        """兼容入口返回 unknown 占位（confirm 白名单拒绝）。"""
        r = parse_voice_text("1斤2块", PRODUCT_NAMES)
        assert r["event_type"] == "unknown"
        assert r["product"] is None
        assert r["confidence"] == 0.0

    def test_product_unit_price_still_books(self):
        """历史修复回归：「白菜1斤2块」单价仍挂在商品上，正常产出进货。"""
        r = parse_voice_text("白菜1斤2块", PRODUCT_NAMES)
        assert r["event_type"] == "purchase"
        assert r["product"] == "白菜"
        assert r["quantity"] == 1.0
        assert r["total_amount"] == 2.0
        assert r["unit_cost"] == 2.0

    def test_verb_present_not_suppressed(self):
        """带交易动词的纯数量报价（「进了50斤，三毛钱一斤」）不 suppress。"""
        r = parse_voice_text("进了50斤，三毛钱一斤", PRODUCT_NAMES)
        assert r["event_type"] == "purchase"

    async def test_parse_text_returns_no_events(self, client):
        """API：parse-text「1斤2块」→ events 为空、parsed 为 None。"""
        resp = await client.post(
            "/api/v1/voice/parse-text", json={"text": "1斤2块", "merchant_id": TEST_MERCHANT_ID}
        )
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["events"] == []
        assert data["parsed"] is None

    async def test_confirm_rejects_empty_parse(self, client):
        """API：空解析的 voice_log confirm 自然拒绝 400，不落账。"""
        resp = await client.post(
            "/api/v1/voice/parse-text", json={"text": "1斤2块", "merchant_id": TEST_MERCHANT_ID}
        )
        log_id = resp.json()["data"]["voice_log_id"]
        confirm = await client.post("/api/v1/voice/confirm", json={"voice_log_id": log_id})
        assert confirm.status_code == 400


# ---------------------------------------------------------------------------
# QA-19：模糊匹配劫持收紧
# ---------------------------------------------------------------------------


class TestQa19FuzzyHijack:
    def test_suffix_extension_is_fuzzy_with_warning(self):
        """「进货土豆丝」不再静默变「土豆」：带 warning + 低 confidence。"""
        r = parse_voice_text("进货土豆丝10斤30块", PRODUCT_NAMES)
        assert r["product"] == "土豆"
        assert r["product_word"] == "土豆丝"  # 用户原词保留
        assert r["warning"] is not None and "土豆" in r["warning"]
        assert r["confidence"] <= 0.65

    def test_exact_match_has_no_warning(self):
        """白菜正例：精确命中无警告、置信度不受打折影响。"""
        r = parse_voice_text("进了白菜50斤，三毛钱一斤", PRODUCT_NAMES)
        assert r["product"] == "白菜"
        assert r["warning"] is None
        assert r["confidence"] > 0.8

    def test_adjacent_products_not_flagged(self):
        """品名后跟动词/连接词（西瓜进了/白菜花了）仍是精确匹配。"""
        events = parse_voice_events("西瓜进了20斤卖了15斤赚了50", PRODUCT_NAMES)
        assert [e["product"] for e in events] == ["西瓜", "西瓜"]
        assert all(e["warning"] is None for e in events)

    async def test_parse_not_hijacked_by_prefix_sku(self, client, db_session):
        """「进货F测芦笋」不得被既有 SKU「F测-百万菜」静默改写（QA-19 实测反例）。"""
        async with db_session() as session:
            sku = ProductSKU(
                merchant_id=MERCHANT_ID,
                name="F测-百万菜",
                canonical_unit="斤",
                shelf_life_hours=96,
            )
            session.add(sku)
            await session.commit()

        resp = await client.post(
            "/api/v1/voice/parse-text",
            json={"text": "进货F测芦笋10斤3块", "merchant_id": TEST_MERCHANT_ID},
        )
        assert resp.status_code == 200
        parsed = resp.json()["data"]["parsed"]
        assert parsed["product"] is None
        assert parsed["product_word"] == "F测芦笋"  # 用户原词保留待确认
        assert parsed["warning"] is None

        # confirm 报错保留原词，不会把账记到「F测-百万菜」头上
        log_id = parsed["voice_log_id"]
        confirm = await client.post("/api/v1/voice/confirm", json={"voice_log_id": log_id})
        assert confirm.status_code == 400
        assert "F测芦笋" in confirm.json()["detail"]

    async def test_exact_sku_still_matches(self, client, db_session):
        """SKU 正例回归：说全 SKU 名仍精确命中（SKU 优先回填不受影响）。"""
        async with db_session() as session:
            session.add(
                ProductSKU(
                    merchant_id=MERCHANT_ID,
                    name="F测-百万菜",
                    canonical_unit="斤",
                    shelf_life_hours=96,
                )
            )
            await session.commit()

        resp = await client.post(
            "/api/v1/voice/parse-text",
            json={"text": "进了F测-百万菜10斤30块", "merchant_id": TEST_MERCHANT_ID},
        )
        parsed = resp.json()["data"]["parsed"]
        assert parsed["product"] == "F测-百万菜"
        assert parsed["warning"] is None


# ---------------------------------------------------------------------------
# QA-08：语音→费用 description 净化
# ---------------------------------------------------------------------------


class TestQa08ExpenseDescriptionSanitized:
    async def test_confirm_strips_script_from_description(self, client, db_session):
        """库内曾有 `<script>alert('v')</script>摊位费花了20块` 原样落库。"""
        resp = await client.post(
            "/api/v1/voice/parse-text",
            json={
                "text": "<script>alert('v')</script>摊位费花了20块",
                "merchant_id": TEST_MERCHANT_ID,
            },
        )
        assert resp.status_code == 200
        parsed = resp.json()["data"]["parsed"]
        assert parsed["event_type"] == "expense"

        log_id = parsed["voice_log_id"]
        confirm = await client.post("/api/v1/voice/confirm", json={"voice_log_id": log_id})
        assert confirm.status_code == 200

        async with db_session() as session:
            expense = (await session.execute(select(Expense))).scalar_one()
            desc = expense.description or ""
            assert "<" not in desc and ">" not in desc
            assert "'" not in desc and '"' not in desc
            assert "摊位费" in desc and "20" in desc


# ---------------------------------------------------------------------------
# QA-29：parse-text 500 字上限
# ---------------------------------------------------------------------------


class TestQa29TextLengthCap:
    async def test_501_chars_rejected_422(self, client):
        resp = await client.post(
            "/api/v1/voice/parse-text",
            json={"text": "买" * 501, "merchant_id": TEST_MERCHANT_ID},
        )
        assert resp.status_code == 422
        assert "500" in resp.json()["detail"]

    async def test_500_chars_accepted(self, client):
        resp = await client.post(
            "/api/v1/voice/parse-text",
            json={"text": "白菜" * 250, "merchant_id": TEST_MERCHANT_ID},
        )
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# QA-30：voice/upload 魔数校验
# ---------------------------------------------------------------------------


class TestQa30UploadMagicBytes:
    async def _patch_audio_dir(self, monkeypatch, tmp_path):
        from app.config import settings

        monkeypatch.setattr(settings, "audio_dir", str(tmp_path / "audio"), raising=False)

    async def test_valid_wav_header_passes(self, client, monkeypatch, tmp_path):
        """合法 WAV 头（RIFF....WAVE）照常落盘。"""
        await self._patch_audio_dir(monkeypatch, tmp_path)
        resp = await client.post(
            "/api/v1/voice/upload",
            files={
                "audio": (
                    "rec.wav",
                    b"RIFF\x18\x00\x00\x00WAVEfmt " + b"\x00" * 16,
                    "audio/wav",
                )
            },
            data={"dialect": "mandarin"},
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["data"]["voice_log_id"]

    async def test_exe_bytes_renamed_wav_rejected(self, client, monkeypatch, tmp_path):
        """exe 字节（MZ 头）改名 .wav → 400，不落盘。"""
        await self._patch_audio_dir(monkeypatch, tmp_path)
        resp = await client.post(
            "/api/v1/voice/upload",
            files={"audio": ("evil.wav", b"MZ\x90\x00" + b"\x00" * 64, "audio/wav")},
            data={"dialect": "mandarin"},
        )
        assert resp.status_code == 400
        assert "音频文件格式不正确" in resp.json()["detail"]

    async def test_amr_header_passes(self, client, monkeypatch, tmp_path):
        """AMR 头（#!AMR）按其扩展名放行。"""
        await self._patch_audio_dir(monkeypatch, tmp_path)
        resp = await client.post(
            "/api/v1/voice/upload",
            files={"audio": ("rec.amr", b"#!AMR\n" + b"\x00" * 16, "audio/amr")},
            data={"dialect": "cantonese"},
        )
        assert resp.status_code == 200, resp.text
