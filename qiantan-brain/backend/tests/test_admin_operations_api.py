"""Integration tests for the SaaS admin operations and CSV export APIs."""

from __future__ import annotations

import csv
import io
import json
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest_asyncio
from tests.conftest import TEST_MERCHANT_ID

from app.core.admin_security import create_admin_token
from app.models.ai_action import AIAction
from app.models.device import Device
from app.models.merchant import Merchant
from app.models.saas import Invoice, Plan, PlatformAdmin, Subscription, Tenant, UsageRecord


@pytest_asyncio.fixture
async def admin_headers(db_session):
    admin_id = uuid.uuid4()
    async with db_session() as session:
        session.add(
            PlatformAdmin(
                id=admin_id,
                email="admin-api-test@example.com",
                password_hash="not-used-in-token-tests",
                name="测试管理员",
                role="super_admin",
                is_active=True,
            )
        )
        await session.commit()

    token = create_admin_token(admin_id, role="super_admin")
    return {"Authorization": f"Bearer {token}"}


class TestAdminInvoices:
    async def test_line_items_contract_and_state_machine(self, client, db_session, admin_headers):
        tenant_id = uuid.uuid4()
        async with db_session() as session:
            session.add(Tenant(id=tenant_id, name="账单测试租户", slug="invoice-contract"))
            await session.commit()

        create_response = await client.post(
            "/api/admin/invoices",
            headers=admin_headers,
            json={
                "tenant_id": str(tenant_id),
                "amount": "99.00",
                "line_items": [
                    {"name": "专业版月费", "amount": "99.00"},
                ],
            },
        )
        assert create_response.status_code == 201, create_response.text
        invoice = create_response.json()
        assert invoice["line_items"] == [
            {"name": "专业版月费", "amount": "99.00"},
        ]
        invoice_id = invoice["id"]

        paid_via_generic_update = await client.put(
            f"/api/admin/invoices/{invoice_id}",
            headers=admin_headers,
            json={"status": "paid"},
        )
        assert paid_via_generic_update.status_code == 400
        assert "标记已付接口" in paid_via_generic_update.json()["detail"]

        sent_response = await client.put(
            f"/api/admin/invoices/{invoice_id}",
            headers=admin_headers,
            json={"status": "sent"},
        )
        assert sent_response.status_code == 200

        invalid_reverse = await client.put(
            f"/api/admin/invoices/{invoice_id}",
            headers=admin_headers,
            json={"status": "draft"},
        )
        assert invalid_reverse.status_code == 409

        paid_response = await client.post(
            f"/api/admin/invoices/{invoice_id}/mark-paid",
            headers=admin_headers,
            json={"payment_method": "manual", "reason": "线下到账"},
        )
        assert paid_response.status_code == 200
        assert paid_response.json()["status"] == "paid"

        list_response = await client.get(
            f"/api/admin/invoices?tenant_id={tenant_id}",
            headers=admin_headers,
        )
        assert list_response.status_code == 200
        assert list_response.json()["items"][0]["line_items"] == invoice["line_items"]


class TestAdminAIOps:
    async def test_actions_use_real_status_and_merchant_tenant_relation(
        self, client, db_session, admin_headers
    ):
        tenant_id = uuid.uuid4()
        async with db_session() as session:
            tenant = Tenant(id=tenant_id, name="华东示范市场", slug="east-demo")
            session.add(tenant)

            merchant = await session.get(Merchant, uuid.UUID(TEST_MERCHANT_ID))
            assert merchant is not None
            merchant.tenant_id = tenant_id

            session.add_all(
                [
                    AIAction(
                        merchant_id=merchant.id,
                        action_type="purchase",
                        title="采购白菜 20 斤",
                        status="pending",
                        payload={"qty": 20, "unit": "斤"},
                    ),
                    AIAction(
                        merchant_id=merchant.id,
                        action_type="price",
                        title="调整白菜售价",
                        status="executed",
                        result={"old_price": 2.5, "new_price": 2.8},
                        executed_at=datetime.now(UTC).replace(tzinfo=None),
                    ),
                    AIAction(
                        merchant_id=merchant.id,
                        action_type="purchase",
                        title="采购失败记录",
                        status="failed",
                        result={"error": "供应商不可用"},
                        executed_at=datetime.now(UTC).replace(tzinfo=None),
                    ),
                ]
            )
            await session.commit()

        response = await client.get(
            "/api/admin/aiops/actions?page=1&page_size=1",
            headers=admin_headers,
        )
        assert response.status_code == 200
        payload = response.json()
        assert payload["total"] == 3
        assert len(payload["items"]) == 1
        assert payload["stats"] == {
            "total": 3,
            "executed": 1,
            "success": 1,
            "adoption_rate": "33.3%",
        }

        all_response = await client.get(
            "/api/admin/aiops/actions?page_size=100",
            headers=admin_headers,
        )
        assert all_response.status_code == 200
        items = {item["title"]: item for item in all_response.json()["items"]}

        executed = items["调整白菜售价"]
        assert executed["tenant_name"] == "华东示范市场"
        assert executed["executed"] is True
        assert executed["result"] == "success"
        assert json.loads(executed["detail"]) == {"old_price": 2.5, "new_price": 2.8}

        failed = items["采购失败记录"]
        assert failed["executed"] is False
        assert failed["result"] == "failed"
        assert json.loads(failed["detail"]) == {"error": "供应商不可用"}

        pending = items["采购白菜 20 斤"]
        assert pending["executed"] is False
        assert pending["result"] is None
        assert json.loads(pending["detail"]) == {"qty": 20, "unit": "斤"}

        stats_response = await client.get("/api/admin/aiops/stats", headers=admin_headers)
        assert stats_response.status_code == 200
        stats = stats_response.json()
        assert stats["total_actions"] == 3
        assert stats["executed"] == 1
        assert stats["successful"] == 1
        assert stats["failed"] == 1
        assert stats["adoption_rate"] == "33.3%"


class TestAdminMonitoring:
    async def test_overview_uses_usage_devices_and_ai_actions(
        self, client, db_session, admin_headers
    ):
        now = datetime.now(UTC).replace(tzinfo=None)
        tenant_id = uuid.uuid4()
        async with db_session() as session:
            session.add(
                Tenant(
                    id=tenant_id,
                    name="监控测试租户",
                    slug="monitoring-test",
                    status="active",
                )
            )
            merchant = await session.get(Merchant, uuid.UUID(TEST_MERCHANT_ID))
            assert merchant is not None
            merchant.tenant_id = tenant_id
            session.add_all(
                [
                    UsageRecord(
                        tenant_id=tenant_id,
                        metric="api_calls",
                        recorded_date=now.date().isoformat(),
                        value=120,
                    ),
                    Device(
                        merchant_id=merchant.id,
                        device_type="scale",
                        device_name="在线秤",
                        serial_number="MONITOR-ONLINE",
                        last_heartbeat=now,
                    ),
                    Device(
                        merchant_id=merchant.id,
                        device_type="camera",
                        device_name="离线摄像头",
                        serial_number="MONITOR-STALE",
                        last_heartbeat=now - timedelta(hours=2),
                        last_error="heartbeat timeout",
                    ),
                    AIAction(
                        merchant_id=merchant.id,
                        action_type="purchase",
                        title="正常任务",
                        status="executed",
                        created_at=now,
                    ),
                    AIAction(
                        merchant_id=merchant.id,
                        action_type="price",
                        title="失败任务",
                        status="failed",
                        created_at=now,
                    ),
                ]
            )
            await session.commit()

        response = await client.get(
            "/api/admin/monitoring/overview?days=1",
            headers=admin_headers,
        )
        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["request_total"] == 120
        assert payload["today_requests"] == 120
        assert payload["active_tenants"] == 1
        assert payload["device_total"] == 2
        assert payload["device_online"] == 1
        assert payload["device_stale"] == 1
        assert payload["device_errors"] == 1
        assert payload["ai_action_total"] == 2
        assert payload["ai_action_failed"] == 1
        assert payload["ai_success_rate"] == 50.0
        assert len(payload["checks"]) == 4

        analytics_response = await client.get(
            "/api/admin/dashboard/analytics?days=7",
            headers=admin_headers,
        )
        assert analytics_response.status_code == 200, analytics_response.text
        analytics = analytics_response.json()
        assert analytics["range_days"] == 7
        assert analytics["active_tenant_rate"] == 100.0
        assert len(analytics["trend"]) == 7
        assert analytics["trend"][-1]["api_calls"] == 120


class TestAdminExport:
    async def test_exports_tenants_subscriptions_and_invoices_without_n_plus_one(
        self, client, db_session, admin_headers
    ):
        now = datetime.now(UTC).replace(tzinfo=None)
        tenant_id = uuid.uuid4()
        plan_id = uuid.uuid4()
        subscription_id = uuid.uuid4()

        async with db_session() as session:
            session.add(
                Plan(
                    id=plan_id,
                    code="admin-export-pro",
                    name="专业导出版",
                    price_monthly=Decimal("199.00"),
                    price_yearly=Decimal("1990.00"),
                )
            )
            session.add(
                Tenant(
                    id=tenant_id,
                    name="导出测试市场",
                    slug="export-market",
                    plan_id=plan_id,
                    status="active",
                    contact_email="owner@example.com",
                )
            )
            session.add(
                Subscription(
                    id=subscription_id,
                    tenant_id=tenant_id,
                    plan_id=plan_id,
                    billing_cycle="yearly",
                    status="active",
                    current_period_start=now,
                    current_period_end=now + timedelta(days=365),
                    auto_renew=True,
                )
            )
            session.add(
                Invoice(
                    tenant_id=tenant_id,
                    subscription_id=subscription_id,
                    invoice_no="INV-ADMIN-EXPORT-001",
                    amount=Decimal("1990.00"),
                    currency="CNY",
                    status="paid",
                    due_date=now + timedelta(days=7),
                    paid_at=now,
                    payment_method="wechat_pay",
                )
            )
            await session.commit()

        tenant_rows = await self._download_csv(client, admin_headers, "tenants")
        assert tenant_rows[0]["名称"] == "导出测试市场"
        assert tenant_rows[0]["联系邮箱"] == "owner@example.com"

        subscription_rows = await self._download_csv(client, admin_headers, "subscriptions")
        assert subscription_rows[0]["租户"] == "导出测试市场"
        assert subscription_rows[0]["套餐"] == "专业导出版"
        assert subscription_rows[0]["计费周期"] == "年付"
        assert subscription_rows[0]["自动续费"] == "是"

        invoice_rows = await self._download_csv(client, admin_headers, "invoices")
        assert invoice_rows[0]["发票号"] == "INV-ADMIN-EXPORT-001"
        assert invoice_rows[0]["租户"] == "导出测试市场"
        assert invoice_rows[0]["金额"] == "1990.00"
        assert invoice_rows[0]["支付方式"] == "wechat_pay"

    async def test_rejects_unknown_export_type(self, client, admin_headers):
        response = await client.get("/api/admin/export/unknown", headers=admin_headers)
        assert response.status_code == 400
        assert "不支持的类型" in response.json()["detail"]

    @staticmethod
    async def _download_csv(client, headers, data_type: str) -> list[dict[str, str]]:
        response = await client.get(f"/api/admin/export/{data_type}", headers=headers)
        assert response.status_code == 200
        assert "text/csv" in response.headers["content-type"]
        assert response.headers["content-disposition"] == (f"attachment; filename={data_type}.csv")
        text = response.content.decode("utf-8-sig")
        return list(csv.DictReader(io.StringIO(text)))
