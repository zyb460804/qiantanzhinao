"""多意图话语 → 每事件独立 VoiceLog（前后端契约修复）回归测试。

背景：前端对每张事件卡提供独立「确认入账」按钮，事件缺 voice_log_id 时
回退顶层 id → 第二笔起用同一个 log id confirm，被幂等机制误去重（只入
第一笔的账）。本文件锁定后端新契约：

  1. 多意图 parse-text/upload：每事件各建一条 VoiceLog，events[i] 内嵌
     各自 voice_log_id；顶层 voice_log_id == events[0] 的（旧客户端兼容）
  2. 两笔事件各自 confirm 均入账、金额各归各
  3. 幂等重放各归各：同 log 重复 confirm 不重复入账，也不误伤另一笔
  4. /voice/logs：N 笔待确认显示 N 条；确认其一不影响其余
  5. 单意图路径零行为变化（恰好一条 log，无额外行）
"""

import uuid

import pytest
from sqlalchemy import select
from tests.conftest import TEST_MERCHANT_ID

from app.models.inventory import InventoryRecord
from app.models.voice import VoiceLog


pytestmark = pytest.mark.asyncio

MERCHANT_ID = uuid.UUID(TEST_MERCHANT_ID)

# 一句话两笔：采购猪肉 10 斤 50 元 + 卖出猪肉 3 斤 15 元（先确认前者备货）。
MULTI_TEXT = "进了猪肉10斤花了50块又卖了猪肉3斤15块"
SINGLE_TEXT = "进了白菜50斤，三毛钱一斤"


async def _parse(client, text: str) -> dict:
    resp = await client.post(
        "/api/v1/voice/parse-text", json={"merchant_id": TEST_MERCHANT_ID, "text": text}
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["data"]


async def _fetch_logs(db_session) -> list[VoiceLog]:
    async with db_session() as session:
        return (
            (
                await session.execute(
                    select(VoiceLog).where(VoiceLog.merchant_id == MERCHANT_ID)
                )
            )
            .scalars()
            .all()
        )


async def _fetch_voice_records(db_session) -> list[InventoryRecord]:
    async with db_session() as session:
        return (await session.execute(select(InventoryRecord))).scalars().all()


class TestMultiIntentLogPerEvent:
    async def test_each_event_gets_own_log_and_id(self, client, db_session):
        data = await _parse(client, MULTI_TEXT)

        assert len(data["events"]) == 2
        ids = [event["voice_log_id"] for event in data["events"]]
        assert ids[0] and ids[1] and ids[0] != ids[1]
        # 顶层兼容：旧客户端仍拿 events[0] 的 id；parsed/event 同现状。
        assert data["voice_log_id"] == ids[0]
        assert data["event"] == data["parsed"] == data["events"][0]

        logs = await _fetch_logs(db_session)
        assert len(logs) == 2
        by_id = {str(log.id): log for log in logs}
        assert set(by_id) == set(ids)
        # 同 raw_text、各自 parsed_event、各自待确认状态
        assert all(log.asr_text == MULTI_TEXT for log in logs)
        assert all(log.status == "parsed" for log in logs)
        assert sorted(by_id[i].parsed_event["event_type"] for i in ids) == [
            "purchase",
            "sale",
        ]
        for event, log_id in zip(data["events"], ids, strict=True):
            stored = by_id[log_id].parsed_event
            assert stored["event_type"] == event["event_type"]
            assert stored["quantity"] == event["quantity"]
            assert stored["total_amount"] == event["total_amount"]

    async def test_both_confirms_book_independently(self, client, db_session):
        """两笔各自 confirm：均入账、金额各归各（修复前第二笔被幂等吞掉）。"""
        data = await _parse(client, MULTI_TEXT)
        purchase_log, sale_log = (event["voice_log_id"] for event in data["events"])

        first = await client.post("/api/v1/voice/confirm", json={"voice_log_id": purchase_log})
        assert first.status_code == 200, first.text
        assert float(first.json()["data"]["total_amount"]) == 50.0

        second = await client.post("/api/v1/voice/confirm", json={"voice_log_id": sale_log})
        assert second.status_code == 200, second.text
        assert float(second.json()["data"]["total_amount"]) == 15.0

        records = await _fetch_voice_records(db_session)
        assert len(records) == 2
        by_type = {record.event_type: record for record in records}
        assert float(by_type["purchase"].total_amount) == 50.0
        assert float(by_type["purchase"].quantity) == 10.0
        assert float(by_type["sale"].total_amount) == 15.0
        assert float(by_type["sale"].quantity) == -3.0
        # 幂等键按 log 行隔离：两笔键不同，互不吞并
        assert {record.idempotency_key for record in records} == {
            f"voice:{purchase_log}",
            f"voice:{sale_log}",
        }

    async def test_idempotent_replay_each_log(self, client, db_session):
        """同一 log 重复 confirm 不重复入账；重放不影响另一笔入账。"""
        data = await _parse(client, MULTI_TEXT)
        purchase_log, sale_log = (event["voice_log_id"] for event in data["events"])

        assert (
            await client.post("/api/v1/voice/confirm", json={"voice_log_id": purchase_log})
        ).status_code == 200
        replay = await client.post("/api/v1/voice/confirm", json={"voice_log_id": purchase_log})
        assert replay.status_code == 200
        assert replay.json()["data"]["idempotent"] is True

        assert (
            await client.post("/api/v1/voice/confirm", json={"voice_log_id": sale_log})
        ).status_code == 200

        records = await _fetch_voice_records(db_session)
        assert len(records) == 2  # 各 1 条，重放未新增、未误伤

    async def test_logs_endpoint_shows_all_pending(self, client):
        """N 笔待确认 → /voice/logs 显示 N 条；确认其一不影响其余。"""
        data = await _parse(client, MULTI_TEXT)
        purchase_log, sale_log = (event["voice_log_id"] for event in data["events"])

        logs = await client.get(
            "/api/v1/voice/logs",
            params={"merchant_id": TEST_MERCHANT_ID, "page": 1, "limit": 50},
        )
        rows = [row for row in logs.json()["data"] if row["asr_text"] == MULTI_TEXT]
        assert len(rows) == 2
        assert all(row["status"] == "parsed" for row in rows)

        # today-count 语义：每笔事件是一条真实语音记录，计数 +2
        count = await client.get(
            "/api/v1/voice/today-count", params={"merchant_id": TEST_MERCHANT_ID}
        )
        assert count.json()["data"]["today_count"] == 2

        assert (
            await client.post("/api/v1/voice/confirm", json={"voice_log_id": purchase_log})
        ).status_code == 200
        logs2 = await client.get(
            "/api/v1/voice/logs",
            params={"merchant_id": TEST_MERCHANT_ID, "page": 1, "limit": 50},
        )
        statuses = {
            row["id"]: row["status"]
            for row in logs2.json()["data"]
            if row["asr_text"] == MULTI_TEXT
        }
        assert statuses[purchase_log] == "confirmed"
        assert statuses[sale_log] == "parsed"

    async def test_correct_targets_own_event_only(self, client):
        """correct 只改自己那条 log 的 parsed_event，不串到另一笔。"""
        data = await _parse(client, MULTI_TEXT)
        purchase_log, sale_log = (event["voice_log_id"] for event in data["events"])

        resp = await client.post(
            "/api/v1/voice/correct",
            json={"voice_log_id": purchase_log, "corrections": {"total_amount": 60}},
        )
        assert resp.status_code == 200

        logs = await client.get(
            "/api/v1/voice/logs",
            params={"merchant_id": TEST_MERCHANT_ID, "page": 1, "limit": 50},
        )
        by_id = {row["id"]: row["parsed_event"] for row in logs.json()["data"]}
        assert by_id[purchase_log]["total_amount"] == 60.0
        assert by_id[sale_log]["total_amount"] == 15.0


class TestUploadMultiIntent:
    async def test_upload_creates_log_per_event(self, client, db_session, monkeypatch, tmp_path):
        """upload 路径同样每事件一条 VoiceLog（ASR 打桩返回多意图文本）。"""
        from app.config import settings
        from app.services import asr_iflytek

        monkeypatch.setattr(settings, "asr_app_id", "test-app-id", raising=False)
        monkeypatch.setattr(settings, "asr_api_key", "test-key", raising=False)
        monkeypatch.setattr(settings, "asr_api_secret", "test-secret", raising=False)
        # 音频落盘重定向到临时目录，不污染工作区
        monkeypatch.setattr(settings, "audio_dir", str(tmp_path / "audio"), raising=False)

        async def fake_transcribe(path: str, dialect: str = "mandarin") -> str:
            return MULTI_TEXT

        monkeypatch.setattr(asr_iflytek, "transcribe_audio", fake_transcribe)

        resp = await client.post(
            "/api/v1/voice/upload",
            files={"audio": ("rec.wav", b"RIFF-fake-audio-bytes", "audio/wav")},
            data={"dialect": "mandarin"},
        )
        assert resp.status_code == 200, resp.text
        data = resp.json()["data"]
        assert data["asr_text"] == MULTI_TEXT
        assert len(data["events"]) == 2

        ids = [event["voice_log_id"] for event in data["events"]]
        assert data["voice_log_id"] == ids[0]
        assert ids[0] != ids[1]

        logs = await _fetch_logs(db_session)
        assert len(logs) == 2
        assert all(log.audio_url and log.audio_url == logs[0].audio_url for log in logs)
        assert sorted(log.parsed_event["event_type"] for log in logs) == ["purchase", "sale"]


class TestSingleIntentUnchanged:
    async def test_single_intent_creates_exactly_one_log(self, client, db_session):
        """单意图路径零行为变化：一条 log、无 warning、无额外行。"""
        data = await _parse(client, SINGLE_TEXT)

        assert len(data["events"]) == 1
        assert data["voice_log_id"] == data["events"][0]["voice_log_id"]
        assert data["warning"] is None

        logs = await _fetch_logs(db_session)
        assert len(logs) == 1
        assert logs[0].status == "parsed"
        assert logs[0].parsed_event["product"] == "白菜"

    async def test_single_intent_confirm_books_once(self, client, db_session):
        """单意图 confirm + 重放：仍恰 1 条库存流水（旧行为回归）。"""
        data = await _parse(client, SINGLE_TEXT)
        log_id = data["voice_log_id"]

        assert (
            await client.post("/api/v1/voice/confirm", json={"voice_log_id": log_id})
        ).status_code == 200
        replay = await client.post("/api/v1/voice/confirm", json={"voice_log_id": log_id})
        assert replay.status_code == 200
        assert replay.json()["data"]["idempotent"] is True

        records = await _fetch_voice_records(db_session)
        assert len(records) == 1
        assert records[0].idempotency_key == f"voice:{log_id}"
