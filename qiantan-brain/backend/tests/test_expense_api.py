"""财务费用、月报和发票输入边界回归测试。"""

import pytest


pytestmark = pytest.mark.asyncio


async def test_negative_expense_rejected(client):
    res = await client.post("/api/v1/expenses", json={
        "category": "rent", "amount": -1, "expense_date": "2026-07-12",
    })
    assert res.status_code == 400


async def test_non_numeric_expense_rejected_without_500(client):
    res = await client.post("/api/v1/expenses", json={
        "category": "rent", "amount": "not-money", "expense_date": "2026-07-12",
    })
    assert res.status_code == 400


async def test_invalid_month_rejected_without_500(client):
    res = await client.get("/api/v1/expenses/monthly-report?month=2026-13")
    assert res.status_code == 400


async def test_invoice_tax_cannot_exceed_amount(client):
    res = await client.post("/api/v1/expenses/invoices", json={
        "invoice_number": "INV-TAX", "amount": 100, "tax_amount": 101,
        "invoice_date": "2026-07-12",
    })
    assert res.status_code == 400


async def test_duplicate_invoice_number_rejected_per_merchant(client):
    payload = {
        "invoice_number": "INV-001", "amount": 100,
        "invoice_date": "2026-07-12",
    }
    assert (await client.post("/api/v1/expenses/invoices", json=payload)).status_code == 200
    assert (await client.post("/api/v1/expenses/invoices", json=payload)).status_code == 409


async def test_invalid_expense_category_rejected(client):
    res = await client.post("/api/v1/expenses", json={
        "category": "made-up", "amount": 10, "expense_date": "2026-07-12",
    })
    assert res.status_code == 400


async def test_invalid_export_month_rejected_without_500(client):
    res = await client.get("/api/v1/expenses/export/monthly?month=bad")
    assert res.status_code == 400
