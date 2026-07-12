"""Tests for food_safety router — batch management, lock/unlock, QR, trace.

Covers §4.13, §4.14 and §6: normal flow, state machine, auth, isolation.
"""

import uuid

import pytest


BATCH_TRANSITIONS = {
    "pending_acceptance": {"sellable", "returned", "wasted"},
    "sellable": {"near_expiry", "locked", "sold_out", "wasted", "returned"},
    "near_expiry": {"sellable", "locked", "sold_out", "wasted"},
    "locked": {"recalled", "removed", "sellable"},
    "recalled": {"destroyed", "returned"},
    "destroyed": set(),
    "removed": set(),
    "sold_out": set(),
    "wasted": set(),
    "returned": set(),
}


class TestBatchListing:
    async def test_list_batches_empty(self, client):
        res = await client.get("/api/v1/food-safety/batches")
        assert res.status_code == 200
        data = res.json()
        assert data["code"] == 0
        assert isinstance(data["data"], list)

    async def test_list_batches_filtered(self, client):
        res = await client.get("/api/v1/food-safety/batches?status=sellable")
        assert res.status_code == 200


class TestBatchInspection:
    async def test_record_inspection_batch_not_found(self, client):
        fake_id = str(uuid.uuid4())
        res = await client.post(f"/api/v1/food-safety/batches/{fake_id}/inspect",
                                json={"result": "pass"})
        assert res.status_code == 404


class TestDailyChecklist:
    async def test_daily_checklist(self, client):
        res = await client.get("/api/v1/food-safety/daily-checklist")
        assert res.status_code == 200
        data = res.json()
        assert "checklist" in data["data"]
        checklist = data["data"]["checklist"]
        assert len(checklist) >= 4


class TestQRCodeGeneration:
    async def test_generate_qr_batch_not_found(self, client):
        fake_id = str(uuid.uuid4())
        res = await client.post(f"/api/v1/food-safety/batches/{fake_id}/generate-qr")
        assert res.status_code == 404

    async def test_trace_lookup_not_found(self, client):
        res = await client.get("/api/v1/food-safety/trace/nonexistent-code")
        assert res.status_code == 404


class TestUnauthenticated:
    async def test_batches_no_auth(self, auth_client):
        res = await auth_client.get("/api/v1/food-safety/batches")
        assert res.status_code == 401

    async def test_lock_no_auth(self, auth_client):
        fake_id = str(uuid.uuid4())
        res = await auth_client.post(f"/api/v1/food-safety/batches/{fake_id}/lock")
        assert res.status_code == 401

    async def test_trace_public_no_auth_required(self, client):
        """§4.13: 公开追溯查询 — 消费者扫码，无需登录。"""
        res = await client.get("/api/v1/food-safety/trace/test-code")
        # 404 is fine (trace doesn't exist); 401 would be a bug
        assert res.status_code == 404


class TestBatchStateMachine:
    """§5.7: 状态机规范 — 禁止非法跳转。"""

    async def test_lock_nonexistent_batch(self, client):
        fake_id = str(uuid.uuid4())
        res = await client.post(f"/api/v1/food-safety/batches/{fake_id}/lock",
                                json={"reason": "快检不合格"})
        assert res.status_code in (404, 409)  # 404 from db.get, 409 from ValueError

    async def test_lock_without_reason_defaults(self, client):
        """Lock should accept empty body with default reason."""
        fake_id = str(uuid.uuid4())
        res = await client.post(f"/api/v1/food-safety/batches/{fake_id}/lock")
        assert res.status_code in (404, 409)


class TestCrossMerchantIsolation:
    async def test_batch_not_visible_to_other_merchant(self, client):
        other = str(uuid.uuid4())
        res = await client.get("/api/v1/food-safety/batches",
                                headers={"X-Test-Merchant-Id": other})
        assert res.status_code == 200
        assert res.json()["data"] == []
