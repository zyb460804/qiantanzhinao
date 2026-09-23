"""QA2 修复回归（Agent V 第二轮，2026-09-23 语音/POS 域）。

覆盖缺陷：
  QA2-01  voice_logs.asr_text 净化（QA-08 残留缺口）：parse-text 落库前
          剥 <> " ' `，响应与 /voice/logs 回显同为净化文本（upload 转写
          原文同一净化入口）
  QA2-02  POS/语音消耗路径启用无主批次回退（QA-04 开关补口）：语音进货
          （批次 sku_id=NULL）→ 商户事后建档 → POS 带 own_sku_id 可卖、
          语音卖出同口径可消耗
  QA2-05  语音/离线销售纳入日结：语音卖 6 元日报 +6、日结 total_sales +6
          （现金渠道并入 payments，diff 恒等归零；撤销后回零）
  QA2-11  mp3 合法 ID3 头夹带 MZ/PE 可执行字节 → 400（剥 ID3v2 标签后
          扫前 4KB），正常 ID3 音频不受影响
  QA2-12  「报损」话术不产出 purchase/sale 事件 + 引导 warning（此前被
          兜底成 0.85 置信的进货）
  QA2-13  limit≤0 语义统一为空列表（pos 负 limit 不再借 SQLite LIMIT -N
          返回全量，与 catalog 域一致）
  QA2-14  POS 单价先 ROUND_HALF_UP 量化（取浮点真实二进制值，与 catalog
          经 SQLite REAL 的有效舍入同规则）再校验 ≤1e6 上限
  QA2-18  「卖了F测加权菜」不再残留「了」；连字符 SKU 名归一后命中
"""

import uuid

import pytest
from sqlalchemy import select
from tests.conftest import TEST_MERCHANT_ID

from app.core.timezone import cst_today
from app.models.batch import BatchLifecycle
from app.models.catalog import ProductSKU
from app.models.voice import VoiceLog
from app.services.batch import create_batch
from app.services.voice_parser import parse_voice_events, parse_voice_text
from decimal import Decimal


pytestmark = pytest.mark.asyncio

PRODUCT_NAMES = [
    "白菜",
    "土豆",
    "猪肉",
    "西瓜",
    "豆腐",
    "韭菜",
    "红薯",
    "橙子",
]

MERCHANT_ID = uuid.UUID(TEST_MERCHANT_ID)


# ---------------------------------------------------------------------------
# 造数工具
# ---------------------------------------------------------------------------


async def _seed_stock(db_session, quantity: int = 10):
    """直接建一个可售批次（白菜/product_id=1），供 POS 下单消耗。"""
    async with db_session() as session:
        await create_batch(
            session,
            MERCHANT_ID,
            1,
            "白菜",
            f"白菜-qa2-{uuid.uuid4().hex[:6]}",
            Decimal(str(quantity)),
        )
        await session.commit()


async def _parse_and_confirm(client, text: str) -> dict:
    """parse-text → confirm 全链路，返回 confirm 响应 JSON。"""
    resp = await client.post("/api/v1/voice/parse-text", json={"text": text})
    assert resp.status_code == 200, resp.text
    parsed = resp.json()["data"]["parsed"]
    assert parsed is not None, f"解析无事件: {text}"
    confirm = await client.post(
        "/api/v1/voice/confirm", json={"voice_log_id": parsed["voice_log_id"]}
    )
    assert confirm.status_code == 200, confirm.text
    return confirm.json()["data"]


async def _create_sku(db_session, name: str) -> uuid.UUID:
    async with db_session() as session:
        sku = ProductSKU(
            merchant_id=MERCHANT_ID,
            name=name,
            canonical_unit="斤",
            shelf_life_hours=96,
        )
        session.add(sku)
        await session.commit()
        await session.refresh(sku)
        return sku.id


# ---------------------------------------------------------------------------
# QA2-01：asr_text 净化
# ---------------------------------------------------------------------------


class TestQa201AsrTextSanitized:
    async def test_parse_text_payload_sanitized_in_db_and_response(self, client, db_session):
        """B-201 原探针：注入串此前 100% 原样落库+回显。"""
        payload = "现金支出<svg onload=alert(3)>20元买塑料袋' or '1'='1"
        resp = await client.post("/api/v1/voice/parse-text", json={"text": payload})
        assert resp.status_code == 200
        data = resp.json()["data"]

        # 响应回显已是净化文本
        assert "<" not in data["asr_text"] and ">" not in data["asr_text"]
        assert "'" not in data["asr_text"] and '"' not in data["asr_text"]
        assert "svg onload=alert(3)" in data["asr_text"]  # 内容保留、危险字符剥离

        # 落库行本体净化
        log_id = data["voice_log_id"]
        async with db_session() as session:
            log = await session.get(VoiceLog, uuid.UUID(log_id))
            assert log is not None
            assert "<" not in log.asr_text and ">" not in log.asr_text
            assert "'" not in log.asr_text and '"' not in log.asr_text

        # /voice/logs 读回同样净化
        logs = await client.get("/api/v1/voice/logs")
        row = next(r for r in logs.json()["data"] if r["id"] == log_id)
        assert "<" not in row["asr_text"] and "'" not in row["asr_text"]

    async def test_payload_only_text_rejected_422(self, client):
        """剥净后为空串 → 422，不产出垃圾语音记录。"""
        resp = await client.post("/api/v1/voice/parse-text", json={"text": "<>\"'` \n\t"})
        assert resp.status_code == 422

    async def test_clean_text_unchanged(self, client):
        """正常文本净化为无操作（既有用例口径不变）。"""
        resp = await client.post(
            "/api/v1/voice/parse-text", json={"text": "进了土豆20斤"}
        )
        assert resp.status_code == 200
        assert resp.json()["data"]["asr_text"] == "进了土豆20斤"


# ---------------------------------------------------------------------------
# QA2-02：POS/语音消耗路径启用无主批次回退
# ---------------------------------------------------------------------------


class TestQa02FallbackToUnowned:
    async def _voice_purchase_unowned_batch(self, client):
        """语音进货（无 SKU → 批次 sku_id=NULL），返回批次 id。"""
        await _parse_and_confirm(client, "进货白菜20斤花了30块")
        # 语音入库批次：商品维度的唯一批次
        return None

    async def test_pos_with_own_sku_consumes_unowned_batch(self, client, db_session):
        """语音进货批次 sku_id=NULL → 事后建档 → POS 带 own_sku_id 下单可卖（此前 409 可售 0）。"""
        await self._voice_purchase_unowned_batch(client)
        sku_id = await _create_sku(db_session, "白菜")

        resp = await client.post(
            "/api/v1/pos/orders",
            json={
                "client_id": "qa2-02-pos-1",
                "payment_method": "cash",
                "items": [
                    {
                        "product_id": 1,
                        "sku_id": str(sku_id),
                        "quantity": 1,
                        "unit": "斤",
                        "unit_price": 3,
                    }
                ],
            },
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["data"]["total_amount"] == 3.0

        # 无主批次被实际消耗：20 - 1 = 19
        async with db_session() as session:
            remaining = (
                (
                    await session.execute(
                        select(BatchLifecycle.remaining_qty).where(
                            BatchLifecycle.merchant_id == MERCHANT_ID,
                            BatchLifecycle.product_id == 1,
                        )
                    )
                )
                .scalars()
                .all()
            )
            assert sum(remaining) == 19.0

    async def test_voice_sale_after_sku_created_consumes_unowned_batch(self, client, db_session):
        """语音进货后建档，语音卖出（SKU 过滤）也走同款回退，不再 409。"""
        await self._voice_purchase_unowned_batch(client)
        await _create_sku(db_session, "白菜")

        result = await _parse_and_confirm(client, "卖了白菜2斤6块")
        assert result["consumed_from_batches"] == 2.0


# ---------------------------------------------------------------------------
# QA2-05：语音/离线销售纳入日结
# ---------------------------------------------------------------------------


class TestQa05ChannelSalesInSettlement:
    async def test_voice_sale_matches_daily_report(self, client, db_session):
        """语音卖 6 元：日报 revenue +6，日结 total_sales/payments 同步 +6，diff 归零。"""
        await _parse_and_confirm(client, "进货白菜10斤花了20块")
        await _parse_and_confirm(client, "卖了白菜2斤6块")

        daily = await client.get("/api/v1/reports/daily")
        assert daily.status_code == 200
        assert daily.json()["data"]["revenue"] == 6.0

        settle = await client.get(f"/api/v1/pos/daily-settlement/{cst_today().isoformat()}")
        assert settle.status_code == 200
        data = settle.json()["data"]
        assert data["total_sales"] == 6.0, "日结 total_sales 必须与日报 revenue 一致"
        assert data["cash_amount"] == 6.0  # 语音销售按现金渠道并入
        assert data["total_payments"] == 6.0
        assert data["refunds_total"] == 0.0
        # 净额恒等：diff = total_sales − total_payments − credit_amount
        assert data["diff_amount"] == 0.0

    async def test_offline_sale_enters_settlement(self, client, db_session):
        """离线补账销售同样计入日结（此前只在日报出现）。"""
        await _seed_stock(db_session, quantity=10)
        resp = await client.post(
            "/api/v1/inventory/offline-sync",
            json={
                "items": [
                    {
                        "idempotency_key": "qa2-05-offline-sale-1",
                        "event_type": "sale",
                        "product_id": 1,
                        "quantity": -2,
                        "unit": "斤",
                        "unit_price": 3,
                        "total_amount": 6,
                    }
                ]
            },
        )
        assert resp.status_code == 200, resp.text

        settle = await client.get(f"/api/v1/pos/daily-settlement/{cst_today().isoformat()}")
        data = settle.json()["data"]
        assert data["total_sales"] == 6.0
        assert data["cash_amount"] == 6.0
        assert data["diff_amount"] == 0.0

    async def test_voided_voice_sale_leaves_settlement(self, client, db_session):
        """撤销语音单后流水 is_voided → 日结回零（净额口径不残留）。"""
        await _parse_and_confirm(client, "进货白菜10斤花了20块")
        await _parse_and_confirm(client, "卖了白菜2斤6块")
        log_id = None
        logs = await client.get("/api/v1/voice/logs")
        for row in logs.json()["data"]:
            event = row.get("parsed_event") or {}
            if event.get("event_type") == "sale" and row["status"] == "confirmed":
                log_id = row["id"]
        assert log_id is not None

        void = await client.post(
            f"/api/v1/voice/{log_id}/void", json={"reason": "QA2-05 撤销回归"}
        )
        assert void.status_code == 200, void.text

        settle = await client.get(f"/api/v1/pos/daily-settlement/{cst_today().isoformat()}")
        data = settle.json()["data"]
        assert data["total_sales"] == 0.0
        assert data["diff_amount"] == 0.0


# ---------------------------------------------------------------------------
# QA2-11：mp3 容器夹带可执行字节
# ---------------------------------------------------------------------------


def _id3_mp3(tag_body: bytes, payload: bytes) -> bytes:
    """构造合法 ID3v2 头的 mp3：10 字节头（synchsafe 标签长）+ 标签体 + 载荷。"""
    size = len(tag_body)
    synchsafe = bytes(
        [(size >> 21) & 0x7F, (size >> 14) & 0x7F, (size >> 7) & 0x7F, size & 0x7F]
    )
    return b"ID3\x03\x00\x00" + synchsafe + tag_body + payload


class TestQa211Mp3ContainerSmuggling:
    async def _patch_audio_dir(self, monkeypatch, tmp_path):
        from app.config import settings

        monkeypatch.setattr(settings, "audio_dir", str(tmp_path / "audio"), raising=False)

    async def _upload(self, client, content: bytes, filename="evil.mp3"):
        return await client.post(
            "/api/v1/voice/upload",
            files={"audio": (filename, content, "audio/mpeg")},
            data={"dialect": "mandarin"},
        )

    async def test_id3_with_mz_payload_rejected(self, client, monkeypatch, tmp_path):
        """B-204 构造：合法 ID3 头（空标签）+ MZ exe 字节 → 400。"""
        await self._patch_audio_dir(monkeypatch, tmp_path)
        resp = await self._upload(client, _id3_mp3(b"", b"MZ\x90\x00" + b"\x00" * 64))
        assert resp.status_code == 400
        assert "音频文件格式不正确" in resp.json()["detail"]

    async def test_id3_with_tag_body_then_mz_rejected(self, client, monkeypatch, tmp_path):
        """带非空 ID3v2 标签体（长度字段正确）夹带 MZ → 剥标签后仍命中 400。"""
        await self._patch_audio_dir(monkeypatch, tmp_path)
        resp = await self._upload(
            client, _id3_mp3(b"\x00" * 32, b"MZ\x90\x00" + b"A" * 128)
        )
        assert resp.status_code == 400

    async def test_id3_with_pe_signature_rejected(self, client, monkeypatch, tmp_path):
        """载荷前 4KB 含 PE 签名（PE\0\0）→ 400。"""
        await self._patch_audio_dir(monkeypatch, tmp_path)
        resp = await self._upload(
            client, _id3_mp3(b"\x00" * 16, b"\x00" * 64 + b"PE\x00\x00" + b"\x00" * 32)
        )
        assert resp.status_code == 400

    async def test_legit_id3_audio_still_accepted(self, client, monkeypatch, tmp_path):
        """正常 ID3 音频（无可执行头）不受影响 → 200。"""
        await self._patch_audio_dir(monkeypatch, tmp_path)
        resp = await self._upload(
            client, _id3_mp3(b"\x00" * 20, b"\xfb\xff\x90\x44" + b"\x00" * 64)
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["data"]["voice_log_id"]


# ---------------------------------------------------------------------------
# QA2-12：报损话术
# ---------------------------------------------------------------------------


class TestQa212WasteReportKeyword:
    def test_parser_produces_no_event_for_baosun(self):
        """「白菜报损6.4斤其他」不再产出 purchase 事件（此前 0.85 置信进货）。"""
        assert parse_voice_events("白菜报损6.4斤其他", PRODUCT_NAMES) == []

    def test_other_phrases_unaffected(self):
        """既有话术回归：正常进货/销售/报废物料不受报损抑制影响。"""
        assert parse_voice_text("进货白菜10斤30块", PRODUCT_NAMES)["event_type"] == "purchase"
        assert parse_voice_text("卖了白菜3斤9块", PRODUCT_NAMES)["event_type"] == "sale"
        assert parse_voice_text("白菜坏了2斤", PRODUCT_NAMES)["event_type"] == "waste"

    async def test_parse_text_returns_warning(self, client):
        """API：报损话术 → 无事件 + 引导 warning。"""
        resp = await client.post(
            "/api/v1/voice/parse-text", json={"text": "白菜报损6.4斤其他"}
        )
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["events"] == []
        assert data["parsed"] is None
        assert data["warning"] is not None
        assert "报损" in data["warning"] and "手动报损" in data["warning"]

    async def test_confirm_rejects_baosun_log(self, client):
        """报损残留的 pending 记录 confirm 自然拒绝，不错账。"""
        resp = await client.post(
            "/api/v1/voice/parse-text", json={"text": "白菜报损6.4斤其他"}
        )
        log_id = resp.json()["data"]["voice_log_id"]
        confirm = await client.post("/api/v1/voice/confirm", json={"voice_log_id": log_id})
        assert confirm.status_code == 400

    async def test_mixed_sentence_with_baosun_suppressed(self, client):
        """混合句夹带报损分句整体抑制，防止报损部分被误记。"""
        resp = await client.post(
            "/api/v1/voice/parse-text",
            json={"text": "卖了白菜3斤9块，白菜报损2斤"},
        )
        assert resp.status_code == 200
        assert resp.json()["data"]["events"] == []


# ---------------------------------------------------------------------------
# QA2-13：limit≤0 语义统一空列表
# ---------------------------------------------------------------------------


class TestQa213LimitZeroConsistency:
    async def test_pos_orders_limit_zero_and_negative_return_empty(self, client, db_session):
        """POS limit=0 / 负数 → 空列表（此前负 limit 借 SQLite LIMIT -N 返回全量）。"""
        await _seed_stock(db_session)
        created = await client.post(
            "/api/v1/pos/orders",
            json={
                "client_id": "qa2-13-seed-1",
                "payment_method": "cash",
                "items": [{"product_id": 1, "quantity": 1, "unit": "斤", "unit_price": 3.5}],
            },
        )
        assert created.status_code == 200

        for bad_limit in ("0", "-1", "-5"):
            resp = await client.get(f"/api/v1/pos/orders?limit={bad_limit}")
            assert resp.status_code == 200
            # AnyResponse 信封只保留 code/message/data（meta 不外露）
            assert resp.json()["data"] == [], f"limit={bad_limit} 应返回空列表"

        # 默认分页行为不受影响
        normal = await client.get("/api/v1/pos/orders")
        assert len(normal.json()["data"]) == 1

    async def test_catalog_domain_never_returns_full_on_zero(self, client):
        """catalog 域 page_size=0 → 422（ge=1），两域对 0 均不给全量/默认放行。"""
        resp = await client.get("/api/v1/catalog/skus?page_size=0")
        assert resp.status_code == 422


# ---------------------------------------------------------------------------
# QA2-14：单价舍入与上限两端口一致
# ---------------------------------------------------------------------------


class TestQa214PriceRoundHalfUp:
    async def test_pos_999999_995_rounds_down_like_catalog(self, client, db_session):
        """POS 999999.995 → 999999.99（与 catalog 经 REAL 的有效舍入一致），不越限。"""
        await _seed_stock(db_session)
        resp = await client.post(
            "/api/v1/pos/orders",
            json={
                "client_id": "qa2-14-995",
                "payment_method": "cash",
                "items": [
                    {"product_id": 1, "quantity": 1, "unit": "斤", "unit_price": 999999.995}
                ],
            },
        )
        assert resp.status_code == 200, resp.text
        order_id = resp.json()["data"]["order_id"]
        detail = await client.get(f"/api/v1/pos/orders/{order_id}")
        item = detail.json()["data"]["items"][0]
        assert item["unit_price"] == 999999.99
        assert item["unit_price"] <= 1000000  # 不越上限

    async def test_pos_999999_994_rounds_down(self, client, db_session):
        """POS 999999.994 → 999999.99（catalog 侧同为 999999.99），两侧行为一致。"""
        await _seed_stock(db_session)
        resp = await client.post(
            "/api/v1/pos/orders",
            json={
                "client_id": "qa2-14-994",
                "payment_method": "cash",
                "items": [
                    {"product_id": 1, "quantity": 1, "unit": "斤", "unit_price": 999999.994}
                ],
            },
        )
        assert resp.status_code == 200, resp.text
        order_id = resp.json()["data"]["order_id"]
        detail = await client.get(f"/api/v1/pos/orders/{order_id}")
        assert detail.json()["data"]["items"][0]["unit_price"] == 999999.99

    async def test_pos_999999_999_reaches_cap_like_catalog(self, client, db_session):
        """999999.999 → 量化恰为 1e6 上限值放行（catalog 侧同存 1000000.0）。"""
        await _seed_stock(db_session)
        resp = await client.post(
            "/api/v1/pos/orders",
            json={
                "client_id": "qa2-14-999",
                "payment_method": "cash",
                "items": [
                    {"product_id": 1, "quantity": 1, "unit": "斤", "unit_price": 999999.999}
                ],
            },
        )
        assert resp.status_code == 200, resp.text
        order_id = resp.json()["data"]["order_id"]
        detail = await client.get(f"/api/v1/pos/orders/{order_id}")
        assert detail.json()["data"]["items"][0]["unit_price"] == 1000000.0

    async def test_catalog_999999_995_stores_999999_99(self, client):
        """catalog 侧基准：999999.995 落库 999999.99（POS 与其对齐）。"""
        create = await client.post(
            "/api/v1/catalog/skus",
            json={
                "name": "QA2测价菜",
                "canonical_unit": "斤",
                "default_sale_price": 999999.995,
            },
        )
        assert create.status_code == 200, create.text
        listing = await client.get("/api/v1/catalog/skus")
        row = next(
            r for r in listing.json()["data"] if r["name"] == "QA2测价菜"
        )
        assert row["default_sale_price"] == 999999.99

    async def test_pos_over_cap_still_rejected(self, client, db_session):
        """量化后超上限（1000000.01）→ 422，防护不放松。"""
        await _seed_stock(db_session)
        resp = await client.post(
            "/api/v1/pos/orders",
            json={
                "client_id": "qa2-14-over",
                "payment_method": "cash",
                "items": [
                    {"product_id": 1, "quantity": 1, "unit": "斤", "unit_price": 1000000.01}
                ],
            },
        )
        assert resp.status_code == 422


# ---------------------------------------------------------------------------
# QA2-18：动词助词残留 + 连字符 SKU 命中
# ---------------------------------------------------------------------------


class TestQa218VerbParticleAndHyphenSku:
    def test_sale_verb_particle_stripped(self):
        """「卖了F测加权菜」：剥「卖」后补剥「了」，product_word=F测加权菜。"""
        r = parse_voice_text("卖了F测加权菜2斤20元", PRODUCT_NAMES)
        assert r["product_word"] == "F测加权菜"
        assert r["event_type"] == "sale"

    def test_drop_particle_stripped(self):
        """「卖掉了X」同款剥离（掉）。"""
        assert parse_voice_text("卖掉了赣南橙3斤", PRODUCT_NAMES)["product_word"] == "赣南橙"

    def test_existing_verb_cases_unchanged(self):
        """既有动词剥离用例回归（QA-24 口径不变）。"""
        assert parse_voice_text("进货赣南橙1斤4块", PRODUCT_NAMES)["product_word"] == "赣南橙"
        assert parse_voice_text("进货葱20斤", PRODUCT_NAMES)["product_word"] == "葱"
        assert parse_voice_text("卖了3斤火龙果10块", PRODUCT_NAMES)["product_word"] == "火龙果"

    async def test_hyphen_sku_hit_via_spoken_name(self, client, db_session):
        """口述「F测加权菜」命中建档名「F测-加权菜」，confirm 不再 400。"""
        sku_id = await _create_sku(db_session, "F测-加权菜")
        # 先语音进货建档品类+批次（sale confirm 需要可消耗库存）
        await _parse_and_confirm(client, "进了F测-加权菜10斤30块")

        resp = await client.post(
            "/api/v1/voice/parse-text", json={"text": "卖了F测加权菜2斤20元"}
        )
        assert resp.status_code == 200
        parsed = resp.json()["data"]["parsed"]
        assert parsed["product"] == "F测-加权菜"  # SKU 标准名入账，原词保留在 product_word
        assert parsed["product_word"] == "F测加权菜"
        assert parsed["sku_id"] is not None

        confirm = await client.post(
            "/api/v1/voice/confirm", json={"voice_log_id": parsed["voice_log_id"]}
        )
        assert confirm.status_code == 200, confirm.text
        assert confirm.json()["data"]["product"] == "F测-加权菜"
        assert confirm.json()["data"]["consumed_from_batches"] == 2.0

    async def test_exact_hyphen_sku_still_hit(self, client, db_session):
        """带连字符说全名（既有精确路径）不受归一逻辑影响。"""
        await _create_sku(db_session, "F测-加权菜")
        resp = await client.post(
            "/api/v1/voice/parse-text", json={"text": "进了F测-加权菜10斤30块"}
        )
        parsed = resp.json()["data"]["parsed"]
        assert parsed["product"] == "F测-加权菜"
        assert parsed["warning"] is None

    async def test_unrelated_sku_not_hijacked_by_normalized_match(self, client, db_session):
        """归一比对仅剥 -/_/空格：不同字（F测西瓜菜）不得命中「F测-加权菜」。

        注：「西瓜」由全局品类词表命中（既有行为），此处防的是归一化
        SKU 匹配把不相干词绑定到 SKU 上（sku_id 必须为空）。
        """
        await _create_sku(db_session, "F测-加权菜")
        resp = await client.post(
            "/api/v1/voice/parse-text", json={"text": "进了F测西瓜菜10斤30块"}
        )
        parsed = resp.json()["data"]["parsed"]
        assert parsed["sku_id"] is None
        assert parsed["product"] != "F测-加权菜"
