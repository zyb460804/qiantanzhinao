"""复查（Agent M 第三轮）修复回归 —— recheck-A-findings RA-01/04/05/09/12/13/14。

覆盖缺陷：
  RA-01  voice/correct 纠错自由文本（product/party_name/unit）原样落库回显
         XSS payload → 与 asr_text 同规则净化（edit 冲正路径同款覆盖）
  RA-04  语音赊账销售被日结当现金 → 渠道赊账净额剥到 credit_amount，
         diff = total_sales − payments − credit 恒等保持
  RA-05  语音赊账链冲销行（voice_ledger :void:/edit 序号幂等键）计入
         customer_repay → 按幂等键结构化排除，void 后 net_cash_flow 不幻影
  RA-09  日结 waste_cost 未滤 is_voided → void 报损后回落，与日报同口径
  RA-12  /voice/logs 负 limit 泄全量 → limit≤0 空列表（QA2-13 同语义）
  RA-13  全角尖括号/零宽字符绕净化 → NFKC 归一化 + 零宽/双向控制字符剥离
  RA-14  1000000.004 两端口边界不一致 → catalog 先量化后校验，与 pos 同规则
  QA2-13 补口：catalog/suppliers 负 limit → 空 items（三域语义对齐）
"""

import uuid
from decimal import Decimal

import pytest
from sqlalchemy import select
from tests.conftest import TEST_MERCHANT_ID

from app.core.timezone import cst_today
from app.models.catalog import ProductSKU
from app.routers.catalog import _sanitize_display_name
from app.services.batch import create_batch

pytestmark = pytest.mark.asyncio

MERCHANT_ID = uuid.UUID(TEST_MERCHANT_ID)


# ---------------------------------------------------------------------------
# 造数工具
# ---------------------------------------------------------------------------


async def _seed_costed_stock(db_session, quantity: Decimal = Decimal("10"), unit_cost: Decimal = Decimal("2.5")):
    """建一个带成本的批次（白菜/product_id=1），供销售/报损消耗与成本化。"""
    async with db_session() as session:
        await create_batch(
            session,
            MERCHANT_ID,
            1,
            "白菜",
            f"白菜-ra-m-{uuid.uuid4().hex[:6]}",
            quantity,
            unit_cost=unit_cost,
        )
        await session.commit()


async def _parse_and_confirm(client, text: str) -> dict:
    resp = await client.post("/api/v1/voice/parse-text", json={"text": text})
    assert resp.status_code == 200, resp.text
    parsed = resp.json()["data"]["parsed"]
    assert parsed is not None, f"解析无事件: {text}"
    confirm = await client.post(
        "/api/v1/voice/confirm", json={"voice_log_id": parsed["voice_log_id"]}
    )
    assert confirm.status_code == 200, confirm.text
    return {"parsed": parsed, "confirm": confirm.json()["data"]}


async def _settlement(client) -> dict:
    resp = await client.get(f"/api/v1/pos/daily-settlement/{cst_today().isoformat()}")
    assert resp.status_code == 200, resp.text
    return resp.json()["data"]


# ---------------------------------------------------------------------------
# RA-01：correct/edit 纠错自由文本净化
# ---------------------------------------------------------------------------


class TestRa01CorrectSanitized:
    async def test_correct_product_payload_sanitized(self, client, db_session):
        """复查原探针：correct product 塞 <img onerror> payload → 净化后落库回显。"""
        await _seed_costed_stock(db_session)
        resp = await client.post("/api/v1/voice/parse-text", json={"text": "进货白菜10斤20块"})
        assert resp.status_code == 200, resp.text
        parsed = resp.json()["data"]["parsed"]
        assert parsed is not None

        correct = await client.post(
            "/api/v1/voice/correct",
            json={
                "voice_log_id": parsed["voice_log_id"],
                "corrections": {"product": "<img src=x onerror=alert(1)>白菜"},
            },
        )
        assert correct.status_code == 200, correct.text
        got = correct.json()["data"]["parsed"]["product"]
        # 与 asr_text 同规则：危险字符剥离、内容保留
        assert "<" not in got and ">" not in got and "'" not in got and '"' not in got
        assert got.endswith("白菜")

        logs = await client.get("/api/v1/voice/logs")
        row = next(r for r in logs.json()["data"] if r["id"] == parsed["voice_log_id"])
        stored = row["parsed_event"]["product"]
        assert "<" not in stored and ">" not in stored and stored.endswith("白菜")

    async def test_correct_pure_payload_product_rejected(self, client, db_session):
        """纠错后商品名为空（全角尖括号被 NFKC 折叠剥净）→ 422，不落空名。"""
        await _seed_costed_stock(db_session)
        resp = await client.post("/api/v1/voice/parse-text", json={"text": "进货白菜10斤20块"})
        parsed = resp.json()["data"]["parsed"]
        correct = await client.post(
            "/api/v1/voice/correct",
            json={
                "voice_log_id": parsed["voice_log_id"],
                "corrections": {"product": "＜＞＇＂"},
            },
        )
        assert correct.status_code == 422
        # 落库行未被污染
        logs = await client.get("/api/v1/voice/logs")
        row = next(r for r in logs.json()["data"] if r["id"] == parsed["voice_log_id"])
        assert "<" not in (row["parsed_event"] or {}).get("product", "")

    async def test_correct_party_name_payload_sanitized(self, client, db_session):
        """party_name 直通客户往来账展示 → 同规则净化。"""
        await _seed_costed_stock(db_session)
        resp = await client.post("/api/v1/voice/parse-text", json={"text": "卖了白菜2斤6块"})
        parsed = resp.json()["data"]["parsed"]
        correct = await client.post(
            "/api/v1/voice/correct",
            json={
                "voice_log_id": parsed["voice_log_id"],
                "corrections": {"party_name": "<svg onload=alert(1)>RA客户", "is_credit": True},
            },
        )
        assert correct.status_code == 200, correct.text
        got = correct.json()["data"]["parsed"]["party_name"]
        assert "<" not in got and ">" not in got
        assert got.endswith("RA客户")

    async def test_edit_product_payload_sanitized(self, client, db_session):
        """edit 冲正路径的 product/unit 同款净化（写回 parsed_event 前剥）。"""
        await _seed_costed_stock(db_session)
        resp = await client.post("/api/v1/voice/parse-text", json={"text": "进货白菜10斤20块"})
        parsed = resp.json()["data"]["parsed"]
        confirm = await client.post(
            "/api/v1/voice/confirm", json={"voice_log_id": parsed["voice_log_id"]}
        )
        assert confirm.status_code == 200, confirm.text
        edit = await client.put(
            f"/api/v1/voice/{parsed['voice_log_id']}/edit",
            json={"product": "＜＞白\u200b菜＂", "quantity": 5, "unit": "斤"},
        )
        assert edit.status_code == 200, edit.text

        logs = await client.get("/api/v1/voice/logs")
        row = next(r for r in logs.json()["data"] if r["id"] == parsed["voice_log_id"])
        assert row["parsed_event"]["product"] == "白菜"
        assert "\u200b" not in row["parsed_event"]["unit"]


# ---------------------------------------------------------------------------
# RA-04 + RA-05：语音赊账链的日结口径
# ---------------------------------------------------------------------------


class TestRa04Ra05VoiceCreditSettlement:
    async def _credit_voice_sale(self, client) -> dict:
        """parse → correct（赊账 + 对手方）→ confirm，返回 parsed。"""
        resp = await client.post("/api/v1/voice/parse-text", json={"text": "卖了白菜2斤6块"})
        assert resp.status_code == 200, resp.text
        parsed = resp.json()["data"]["parsed"]
        assert parsed is not None
        correct = await client.post(
            "/api/v1/voice/correct",
            json={
                "voice_log_id": parsed["voice_log_id"],
                "corrections": {"party_name": "RA探针客户", "is_credit": True},
            },
        )
        assert correct.status_code == 200, correct.text
        confirm = await client.post(
            "/api/v1/voice/confirm", json={"voice_log_id": parsed["voice_log_id"]}
        )
        assert confirm.status_code == 200, confirm.text
        return parsed

    async def test_credit_voice_sale_counts_as_credit_not_cash(self, client, db_session):
        """赊账语音单：credit_amount +6 而非 cash/payments，diff 恒等归零。"""
        await _seed_costed_stock(db_session)
        await self._credit_voice_sale(client)

        data = await _settlement(client)
        assert data["total_sales"] == 6.0
        # 赊账未收现款：不进 cash/payments，进 credit_amount
        assert data["cash_amount"] == 0.0
        assert data["total_payments"] == 0.0
        assert data["credit_amount"] == 6.0
        assert data["diff_amount"] == 0.0
        assert data["net_cash_flow"] == 0.0

    async def test_repay_then_void_no_double_count_no_phantom(self, client, db_session):
        """赊账 → 手动回款 → void：回款只计一次，冲销行不入 customer_repay。"""
        await _seed_costed_stock(db_session)
        parsed = await self._credit_voice_sale(client)

        # 手动回款 6（真实现金）
        repay = await client.post(
            "/api/v1/ops/customers/repay",
            json={"customer_name": "RA探针客户", "amount": 6, "idempotency_key": "ra05-repay-1"},
        )
        assert repay.status_code == 200, repay.text
        data = await _settlement(client)
        assert data["customer_repay"] == 6.0
        assert data["net_cash_flow"] == 6.0  # 物理 6，无双计

        # void 语音单：应收冲平，冲销行（语音冲销 voice:…）按幂等键排除
        void = await client.post(
            f"/api/v1/voice/{parsed['voice_log_id']}/void", json={"reason": "RA-05 撤销"}
        )
        assert void.status_code == 200, void.text
        data = await _settlement(client)
        assert data["total_sales"] == 0.0
        assert data["credit_amount"] == 0.0, "撤销后渠道赊账净额必须归零"
        assert data["customer_repay"] == 6.0, "冲销行不计 customer_repay（真实现金只计一次）"
        assert data["net_cash_flow"] == 6.0  # 物理回款仍在，无幻影
        assert data["diff_amount"] == 0.0


# ---------------------------------------------------------------------------
# RA-09：waste_cost 滤 is_voided
# ---------------------------------------------------------------------------


class TestRa09WasteCostVoided:
    async def test_voided_waste_drops_from_settlement(self, client, db_session):
        """成本化报损 → void → 日结 waste_cost 回落（与日报 waste_amount 一致）。"""
        await _seed_costed_stock(db_session, quantity=Decimal("10"), unit_cost=Decimal("2.5"))
        waste = await client.post(
            "/api/v1/ops/waste",
            json={"product_id": 1, "quantity": 2, "unit": "斤", "reason": "腐烂"},
        )
        assert waste.status_code == 200, waste.text
        record_id = waste.json()["data"]["record_id"]

        data = await _settlement(client)
        assert data["waste_cost"] == 5.0  # 2 斤 × 2.5 元

        void = await client.post(
            f"/api/v1/inventory/{record_id}/void", json={"reason": "RA-09 撤销报损"}
        )
        assert void.status_code == 200, void.text

        data = await _settlement(client)
        assert data["waste_cost"] == 0.0, "撤销的报损行不计 waste_cost"

        daily = await client.get("/api/v1/reports/daily")
        assert daily.status_code == 200
        assert daily.json()["data"]["waste_amount"] == 0.0, "日结与日报口径一致"


# ---------------------------------------------------------------------------
# RA-14：单价上限两端口一致（先量化后校验）
# ---------------------------------------------------------------------------


class TestRa14PriceBoundaryParity:
    async def test_both_ports_accept_1000000_004(self, client, db_session):
        """1000000.004 量化为 1000000.00 恰等于上限：catalog 与 POS 一致放行。"""
        await _seed_costed_stock(db_session)

        catalog = await client.post(
            "/api/v1/catalog/skus",
            json={
                "name": "RA14边界菜",
                "canonical_unit": "斤",
                "default_sale_price": 1000000.004,
            },
        )
        assert catalog.status_code == 200, catalog.text
        listing = await client.get("/api/v1/catalog/skus")
        row = next(r for r in listing.json()["data"] if r["name"] == "RA14边界菜")
        assert row["default_sale_price"] == 1000000.0

        pos = await client.post(
            "/api/v1/pos/orders",
            json={
                "client_id": "ra14-boundary",
                "payment_method": "cash",
                "items": [
                    {"product_id": 1, "quantity": 1, "unit": "斤", "unit_price": 1000000.004}
                ],
            },
        )
        assert pos.status_code == 200, pos.text
        detail = await client.get(f"/api/v1/pos/orders/{pos.json()['data']['order_id']}")
        assert detail.json()["data"]["items"][0]["unit_price"] == 1000000.0

    async def test_both_ports_agree_on_999999_995(self, client, db_session):
        """999999.995 两端口一致落 999999.99（HALF_UP 量化取浮点真实值）。"""
        await _seed_costed_stock(db_session)

        catalog = await client.post(
            "/api/v1/catalog/skus",
            json={
                "name": "RA14995菜",
                "canonical_unit": "斤",
                "default_sale_price": 999999.995,
            },
        )
        assert catalog.status_code == 200, catalog.text
        listing = await client.get("/api/v1/catalog/skus")
        row = next(r for r in listing.json()["data"] if r["name"] == "RA14995菜")
        assert row["default_sale_price"] == 999999.99

        pos = await client.post(
            "/api/v1/pos/orders",
            json={
                "client_id": "ra14-995",
                "payment_method": "cash",
                "items": [
                    {"product_id": 1, "quantity": 1, "unit": "斤", "unit_price": 999999.995}
                ],
            },
        )
        assert pos.status_code == 200, pos.text
        detail = await client.get(f"/api/v1/pos/orders/{pos.json()['data']['order_id']}")
        assert detail.json()["data"]["items"][0]["unit_price"] == 999999.99

    async def test_both_ports_reject_above_cap_after_quantize(self, client, db_session):
        """量化后仍越上限（1000000.01）两端口一致 422。"""
        await _seed_costed_stock(db_session)
        catalog = await client.post(
            "/api/v1/catalog/skus",
            json={
                "name": "RA14越限菜",
                "canonical_unit": "斤",
                "default_sale_price": 1000000.01,
            },
        )
        assert catalog.status_code == 422
        pos = await client.post(
            "/api/v1/pos/orders",
            json={
                "client_id": "ra14-over",
                "payment_method": "cash",
                "items": [
                    {"product_id": 1, "quantity": 1, "unit": "斤", "unit_price": 1000000.01}
                ],
            },
        )
        assert pos.status_code == 422

    async def test_catalog_non_numeric_price_still_422(self, client):
        """QA-11 回归：非数字串 → 422 而非 500。"""
        resp = await client.post(
            "/api/v1/catalog/skus",
            json={"name": "RA14abc菜", "canonical_unit": "斤", "default_sale_price": "abc"},
        )
        assert resp.status_code == 422


# ---------------------------------------------------------------------------
# RA-13：全角/零宽字符净化
# ---------------------------------------------------------------------------


class TestRa13SanitizeCharset:
    def test_fullwidth_angle_brackets_folded_and_stripped(self):
        """NFKC：全角 ＜＞＂＇ 折叠为 ASCII 后被剥。"""
        assert _sanitize_display_name("＜svg onload=alert(1)＞土豆") == "svg onload=alert(1)土豆"
        assert _sanitize_display_name("＂abc＂＂") == "abc"
        assert "<" not in _sanitize_display_name("＜img＞白＂菜")

    def test_zero_width_and_bidi_chars_stripped(self):
        """零宽字符（\\u200b\\u200c\\u200d\\ufeff 等）与双向隔离符 \\u2066 剥离。"""
        assert _sanitize_display_name("白\u200b菜") == "白菜"
        assert _sanitize_display_name("白\u200c\u200d菜") == "白菜"
        assert _sanitize_display_name("土豆\ufeff") == "土豆"
        assert _sanitize_display_name("白菜卖2斤6块\u200b\u2066") == "白菜卖2斤6块"
        assert "\u200b" not in _sanitize_display_name("a\u200bb")
        assert "\u2066" not in _sanitize_display_name("a\u2066b")

    def test_normal_names_untouched(self):
        """正常中文名/空格压平不受影响。"""
        assert _sanitize_display_name("白菜") == "白菜"
        assert _sanitize_display_name("  白菜   土豆 ") == "白菜 土豆"

    async def test_parse_text_fullwidth_payload_sanitized(self, client):
        """复查原探针：parse-text 全角 payload + 零宽夹带 → 净化后落库回显。"""
        resp = await client.post(
            "/api/v1/voice/parse-text",
            json={"text": "＜svg onload=alert(1)＞白菜卖2斤6块\u200b\u2066"},
        )
        assert resp.status_code == 200, resp.text
        asr_text = resp.json()["data"]["asr_text"]
        assert "＜" not in asr_text and "＞" not in asr_text
        assert "<" not in asr_text and ">" not in asr_text
        assert "\u200b" not in asr_text and "\u2066" not in asr_text

    async def test_catalog_sku_name_fullwidth_and_zero_width(self, client):
        """catalog 建名同款净化（新写入生效）。"""
        resp = await client.post(
            "/api/v1/catalog/skus",
            json={"name": "＜b＞白\u200b菜＜/b＞", "canonical_unit": "斤"},
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["data"]["name"] == "b白菜/b"


# ---------------------------------------------------------------------------
# RA-12 / QA2-13 补口：负 limit 一致空列表
# ---------------------------------------------------------------------------


class TestRa12NegativeLimit:
    async def test_voice_logs_negative_and_zero_limit_empty(self, client, db_session):
        """/voice/logs limit≤0 → 空列表（此前 SQLite LIMIT -N 泄全量）。"""
        await client.post("/api/v1/voice/parse-text", json={"text": "进货白菜10斤20块"})
        for bad_limit in ("-5", "0"):
            resp = await client.get(f"/api/v1/voice/logs?limit={bad_limit}")
            assert resp.status_code == 200
            assert resp.json()["data"] == [], f"limit={bad_limit} 应为空列表"
        normal = await client.get("/api/v1/voice/logs?limit=20")
        assert len(normal.json()["data"]) >= 1

    async def test_catalog_suppliers_negative_limit_empty(self, client, db_session):
        """QA2-13 补口：/catalog/suppliers limit≤0 → 空 items（不再泄全量）。"""
        await client.post("/api/v1/catalog/suppliers", json={"name": "RA12供应商"})
        for bad_limit in ("-5", "0"):
            resp = await client.get(f"/api/v1/catalog/suppliers?limit={bad_limit}")
            assert resp.status_code == 200
            assert resp.json()["data"]["items"] == [], f"limit={bad_limit} 应为空 items"
        normal = await client.get("/api/v1/catalog/suppliers")
        assert len(normal.json()["data"]["items"]) >= 1
