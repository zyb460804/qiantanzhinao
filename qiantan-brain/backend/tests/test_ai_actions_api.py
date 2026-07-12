"""Tests for AI actions router (§4.11) — execute, generate, history.

Covers §4.11 and §6: normal flow, auth, state transitions.
"""

import uuid

import pytest


class TestListActions:
    async def test_list_pending_empty(self, client):
        res = await client.get("/api/v1/ai-actions/pending")
        assert res.status_code == 200
        assert isinstance(res.json()["data"], list)

    async def test_list_history_empty(self, client):
        res = await client.get("/api/v1/ai-actions/history")
        assert res.status_code == 200
        assert isinstance(res.json()["data"], list)


class TestGenerateActions:
    async def test_generate_actions(self, client):
        res = await client.post("/api/v1/ai-actions/generate", json={
            "actions": [
                {
                    "action_type": "price",
                    "title": "番茄降价至3.5元/斤",
                    "payload": {"sku_id": str(uuid.uuid4()), "new_price": 3.5},
                },
                {
                    "action_type": "purchase",
                    "title": "建议采购白菜50斤",
                    "payload": {"items": [], "total_cost": 75},
                },
            ],
        })
        assert res.status_code == 200
        data = res.json()["data"]
        assert len(data) == 2

    async def test_generate_empty_actions(self, client):
        res = await client.post("/api/v1/ai-actions/generate", json={"actions": []})
        assert res.status_code == 200
        assert len(res.json()["data"]) == 0

    async def test_generated_actions_show_in_pending(self, client):
        res = await client.post("/api/v1/ai-actions/generate", json={
            "actions": [{"action_type": "price", "title": "测试动作",
                         "payload": {"sku_id": str(uuid.uuid4()), "new_price": 1}}],
        })
        created_id = res.json()["data"][0]["id"]

        res2 = await client.get("/api/v1/ai-actions/pending")
        pending = [a for a in res2.json()["data"] if a["id"] == created_id]
        assert len(pending) == 1


class TestExecuteAction:
    async def test_execute_nonexistent(self, client):
        fake_id = str(uuid.uuid4())
        res = await client.post(f"/api/v1/ai-actions/{fake_id}/execute")
        assert res.status_code == 404

    async def test_reject_action(self, client):
        """Rejection should not trigger business side effects."""
        res = await client.post("/api/v1/ai-actions/generate", json={
            "actions": [{"action_type": "price", "title": "可拒绝的动作",
                         "payload": {"sku_id": str(uuid.uuid4()), "new_price": 1}}],
        })
        action_id = res.json()["data"][0]["id"]

        res2 = await client.post(f"/api/v1/ai-actions/{action_id}/execute", json={
            "status": "rejected",
        })
        assert res2.status_code == 200
        assert res2.json()["data"]["status"] == "rejected"

    async def test_cannot_execute_rejected_action(self, client):
        res = await client.post("/api/v1/ai-actions/generate", json={
            "actions": [{"action_type": "price", "title": "test",
                         "payload": {"sku_id": str(uuid.uuid4()), "new_price": 1}}],
        })
        action_id = res.json()["data"][0]["id"]

        await client.post(f"/api/v1/ai-actions/{action_id}/execute",
                          json={"status": "rejected"})
        res3 = await client.post(f"/api/v1/ai-actions/{action_id}/execute")
        assert res3.status_code == 409  # Already rejected


class TestUnauthenticated:
    async def test_pending_no_auth(self, auth_client):
        res = await auth_client.get("/api/v1/ai-actions/pending")
        assert res.status_code == 401

    async def test_generate_no_auth(self, auth_client):
        res = await auth_client.post("/api/v1/ai-actions/generate", json={"actions": []})
        assert res.status_code == 401

    async def test_execute_no_auth(self, auth_client):
        fake_id = str(uuid.uuid4())
        res = await auth_client.post(f"/api/v1/ai-actions/{fake_id}/execute")
        assert res.status_code == 401
