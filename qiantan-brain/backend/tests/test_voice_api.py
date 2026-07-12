"""
Integration tests for the voice accounting E2E flow.

Tests the full pipeline:
  POST /voice/parse-text  →  POST /voice/confirm  →  GET /inventory/current
  GET /voice/logs

These exercise the real FastAPI router + DB + voice_parser service together.
"""

import pytest
from tests.conftest import TEST_MERCHANT_ID


pytestmark = pytest.mark.asyncio


class TestVoiceParseText:
    """POST /api/v1/voice/parse-text — semantic parsing."""

    async def test_parse_purchase_basic(self, client):
        """'进了白菜50斤' should parse to a purchase event."""
        resp = await client.post(
            "/api/v1/voice/parse-text",
            json={
                "merchant_id": TEST_MERCHANT_ID,
                "text": "今天进了白菜50斤，三毛钱一斤",
            },
        )

        assert resp.status_code == 200
        body = resp.json()
        assert body["code"] == 0

        parsed = body["data"]["parsed"]
        assert parsed["event_type"] == "purchase"
        assert parsed["product"] == "白菜"
        assert parsed["quantity"] == 50.0
        assert parsed["unit_cost"] is not None
        assert parsed["voice_log_id"]  # embedded for confirm step

    async def test_parse_chinese_money_normalization(self, client):
        """'两块钱一斤' — the '两' character must be handled."""
        resp = await client.post(
            "/api/v1/voice/parse-text",
            json={
                "merchant_id": TEST_MERCHANT_ID,
                "text": "进了土豆30斤，两块钱一斤",
            },
        )

        assert resp.status_code == 200
        parsed = resp.json()["data"]["parsed"]
        assert parsed["product"] == "土豆"
        assert parsed["quantity"] == 30.0
        # 两块 = 2 元
        assert parsed["unit_cost"] == 2.0

    async def test_parse_sale_event(self, client):
        """'卖了' triggers sale event type."""
        resp = await client.post(
            "/api/v1/voice/parse-text",
            json={
                "merchant_id": TEST_MERCHANT_ID,
                "text": "卖了西瓜20斤，两块钱一斤，一共卖了40块",
            },
        )

        assert resp.status_code == 200
        parsed = resp.json()["data"]["parsed"]
        assert parsed["event_type"] == "sale"

    async def test_parse_waste_event(self, client):
        """'扔了/坏了' triggers waste event type."""
        resp = await client.post(
            "/api/v1/voice/parse-text",
            json={
                "merchant_id": TEST_MERCHANT_ID,
                "text": "扔了烂豆腐2斤",
            },
        )

        assert resp.status_code == 200
        parsed = resp.json()["data"]["parsed"]
        assert parsed["event_type"] == "waste"
        assert parsed["product"] == "豆腐"


class TestVoiceConfirmFlow:
    """POST /api/v1/voice/confirm — inventory update after confirmation."""

    async def test_confirm_updates_inventory(self, client):
        """Parse → confirm → inventory reflects the purchase."""
        # 1. Parse
        parse_resp = await client.post(
            "/api/v1/voice/parse-text",
            json={
                "merchant_id": TEST_MERCHANT_ID,
                "text": "进了白菜50斤，三毛钱一斤",
            },
        )
        voice_log_id = parse_resp.json()["data"]["parsed"]["voice_log_id"]

        # 2. Confirm
        confirm_resp = await client.post(
            "/api/v1/voice/confirm",
            json={
                "voice_log_id": voice_log_id,
            },
        )
        assert confirm_resp.status_code == 200
        assert confirm_resp.json()["code"] == 0

        # 3. Check inventory
        inv_resp = await client.get(
            "/api/v1/inventory/current",
            params={
                "merchant_id": TEST_MERCHANT_ID,
            },
        )
        assert inv_resp.status_code == 200
        items = inv_resp.json()["data"]
        baicai = [i for i in items if i.get("product_id") == 1]
        assert len(baicai) == 1
        assert baicai[0]["current_qty"] == 50.0
        # Product name should resolve from product_categories (not "产品1")
        assert baicai[0]["product_name"] == "白菜"

    async def test_repeated_confirm_is_idempotent(self, client):
        """Confirming the same voice log twice must not duplicate inventory."""
        parse_resp = await client.post(
            "/api/v1/voice/parse-text",
            json={
                "merchant_id": TEST_MERCHANT_ID,
                "text": "进了白菜50斤，三毛钱一斤",
            },
        )
        voice_log_id = parse_resp.json()["data"]["parsed"]["voice_log_id"]

        first = await client.post("/api/v1/voice/confirm", json={"voice_log_id": voice_log_id})
        second = await client.post("/api/v1/voice/confirm", json={"voice_log_id": voice_log_id})
        assert first.status_code == 200
        assert second.status_code == 200
        assert second.json()["data"]["idempotent"] is True

        inv_resp = await client.get(
            "/api/v1/inventory/current",
            params={
                "merchant_id": TEST_MERCHANT_ID,
            },
        )
        baicai = [i for i in inv_resp.json()["data"] if i.get("product_id") == 1]
        assert baicai[0]["current_qty"] == 50.0


class TestVoiceLogs:
    """GET /api/v1/voice/logs — query voice record history."""

    async def test_logs_return_created_records(self, client):
        """After parsing, the record should appear in logs."""
        await client.post(
            "/api/v1/voice/parse-text",
            json={
                "merchant_id": TEST_MERCHANT_ID,
                "text": "进了土豆20斤",
            },
        )

        resp = await client.get(
            "/api/v1/voice/logs",
            params={
                "merchant_id": TEST_MERCHANT_ID,
                "page": 1,
                "limit": 10,
            },
        )

        assert resp.status_code == 200
        logs = resp.json()["data"]
        assert len(logs) >= 1
        assert logs[0]["asr_text"] == "进了土豆20斤"

    async def test_today_count_is_exact(self, client):
        """Today count is calculated independently from pagination limit."""
        for text in ("进了土豆20斤", "进了白菜10斤"):
            await client.post(
                "/api/v1/voice/parse-text",
                json={
                    "merchant_id": TEST_MERCHANT_ID,
                    "text": text,
                },
            )

        resp = await client.get(
            "/api/v1/voice/today-count",
            params={
                "merchant_id": TEST_MERCHANT_ID,
            },
        )
        assert resp.status_code == 200
        assert resp.json()["data"]["today_count"] == 2
