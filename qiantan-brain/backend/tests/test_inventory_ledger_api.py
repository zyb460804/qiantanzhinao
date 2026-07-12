"""Tests for inventory router — stock ledger summary (§4.4), current inventory.

Covers §4.4 and §6: normal flow, boundary, auth, cross-tenant isolation.
"""

import uuid

import pytest


class TestCurrentInventory:
    async def test_get_current_inventory(self, client):
        res = await client.get("/api/v1/inventory/current")
        assert res.status_code == 200
        data = res.json()
        assert data["code"] == 0
        assert isinstance(data["data"], list)

    async def test_current_inventory_no_auth(self, auth_client):
        res = await auth_client.get("/api/v1/inventory/current")
        assert res.status_code == 401


class TestStockLedgerSummary:
    """§4.4: 库存统一流水报告。"""

    async def test_ledger_summary_empty(self, client):
        res = await client.get("/api/v1/inventory/ledger/summary")
        assert res.status_code == 200
        data = res.json()["data"]
        assert "inventory_states" in data
        states = data["inventory_states"]
        assert "book" in states
        assert "sellable" in states
        assert "locked" in states
        assert "held" in states
        assert "waste_this_month" in states

    async def test_ledger_summary_has_by_event_type(self, client):
        res = await client.get("/api/v1/inventory/ledger/summary")
        data = res.json()["data"]
        assert "by_event_type" in data
        assert isinstance(data["by_event_type"], dict)

    async def test_ledger_summary_has_active_products(self, client):
        res = await client.get("/api/v1/inventory/ledger/summary")
        data = res.json()["data"]
        assert data["active_products"] >= 4  # 白菜/土豆/豆腐/猪肉

    async def test_ledger_summary_cross_merchant(self, client):
        other = str(uuid.uuid4())
        res = await client.get("/api/v1/inventory/ledger/summary",
                               headers={"X-Test-Merchant-Id": other})
        assert res.status_code == 200
        data = res.json()["data"]
        assert data["inventory_states"]["book"]["quantity"] == 0


class TestInventoryAlerts:
    async def test_alerts_empty(self, client):
        res = await client.get("/api/v1/inventory/alerts")
        assert res.status_code == 200
        data = res.json()["data"]
        assert "expiry_alerts" in data
        assert data["expiring_count"] == 0


class TestVoidRecord:
    async def test_void_nonexistent_record(self, client):
        fake_id = str(uuid.uuid4())
        res = await client.post(f"/api/v1/inventory/{fake_id}/void",
                                json={"reason": "test"})
        assert res.status_code == 404
