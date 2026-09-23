"""财务费用、月报和发票输入边界回归测试。"""

import pytest


pytestmark = pytest.mark.asyncio


async def test_negative_expense_rejected(client):
    res = await client.post("/api/v1/expenses", json={
        "category": "rent", "amount": -1, "expense_date": "2026-07-12",
    })
    # 金额区间约束（QA P2 修复）改用 422，与 SKU 售价校验状态码口径一致
    assert res.status_code == 422
    assert "费用金额必须在" in res.json()["detail"]


async def test_zero_expense_rejected_422(client):
    """金额为 0 不允许入账 → 422。"""
    res = await client.post("/api/v1/expenses", json={
        "category": "rent", "amount": 0, "expense_date": "2026-07-12",
    })
    assert res.status_code == 422


async def test_expense_amount_over_cap_rejected_422(client):
    """1e9 / 1e13 级脏金额（QA 实测原版本可落库）→ 422 中文报错。"""
    for amount in (1_000_000_000, 10_000_000_000_000):
        res = await client.post("/api/v1/expenses", json={
            "category": "rent", "amount": amount, "expense_date": "2026-07-12",
        })
        assert res.status_code == 422, f"金额 {amount} 应被拒绝"
        assert "费用金额必须在" in res.json()["detail"]


async def test_expense_amount_just_over_cap_rejected_422(client):
    """上限边界外一点（1000000.01）→ 422。"""
    res = await client.post("/api/v1/expenses", json={
        "category": "rent", "amount": 1000000.01, "expense_date": "2026-07-12",
    })
    assert res.status_code == 422


async def test_expense_amount_cap_boundary_accepted(client):
    """上限边界值 1000000 → 200 正常落库（与 SKU 售价上限口径对齐）。"""
    res = await client.post("/api/v1/expenses", json={
        "category": "rent", "amount": 1000000, "expense_date": "2026-07-12",
    })
    assert res.status_code == 200, res.text
    assert res.json()["data"]["amount"] == 1000000


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
