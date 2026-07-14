"""End-to-end tests for channel bill import and payment reconciliation."""

import uuid
from decimal import Decimal

import pytest
from sqlalchemy import func, select
from tests.conftest import TEST_MERCHANT_ID

from app.core.timezone import utc_now
from app.models.payment import ChannelBillEntry, ChannelBillImport, ReconciliationTask
from app.models.pos import Payment, SaleOrder


MERCHANT_ID = uuid.UUID(TEST_MERCHANT_ID)


async def _seed_payment(
    db_session,
    *,
    amount: str,
    method: str = "wechat",
    transaction_id: str | None = None,
    order_no: str | None = None,
    status: str = "success",
) -> tuple[uuid.UUID, str]:
    async with db_session() as session:
        resolved_order_no = order_no or f"RECON{uuid.uuid4().hex[:20]}"
        order = SaleOrder(
            merchant_id=MERCHANT_ID,
            order_no=resolved_order_no,
            status="paid",
            total_amount=abs(Decimal(amount)),
            paid_amount=abs(Decimal(amount)),
            refunded_amount=Decimal("0"),
            discount_amount=Decimal("0"),
            client_id=f"recon-{uuid.uuid4()}",
        )
        session.add(order)
        await session.flush()
        payment = Payment(
            merchant_id=MERCHANT_ID,
            order_id=order.id,
            amount=Decimal(amount),
            method=method,
            status=status,
            transaction_id=transaction_id,
        )
        session.add(payment)
        await session.commit()
        return payment.id, resolved_order_no


async def _upload(client, content: str, *, channel: str = "wechat", name: str = "bill.csv"):
    recon_date = utc_now().date().isoformat()
    return await client.post(
        f"/api/v1/reconciliation/import/{recon_date}?channel={channel}",
        files={"file": (name, content.encode("utf-8-sig"), "text/csv")},
    )


@pytest.mark.asyncio
async def test_reconciliation_stays_pending_until_channel_bill_is_imported(client, db_session):
    await _seed_payment(
        db_session,
        amount="12.34",
        transaction_id="wx-reconciliation-pending-001",
    )

    recon_date = utc_now().date().isoformat()
    response = await client.post(f"/api/v1/reconciliation/run/{recon_date}?channel=wechat")
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["status"] == "pending"
    assert data["system_total"] == 12.34
    assert data["channel_total"] == 0.0
    assert data["diff_amount"] == 12.34

    async with db_session() as session:
        task = (
            await session.execute(
                select(ReconciliationTask).where(ReconciliationTask.channel == "wechat")
            )
        ).scalar_one()
        assert task.status == "pending"
        assert task.matched_count == 0
        assert task.unmatched_system == 1


@pytest.mark.asyncio
async def test_normalized_csv_matches_transaction_and_merchant_order_and_is_idempotent(
    client, db_session
):
    _, first_order_no = await _seed_payment(
        db_session,
        amount="10.00",
        transaction_id="wx-exact-001",
    )
    _, second_order_no = await _seed_payment(db_session, amount="5.00")
    content = (
        "transaction_id,merchant_order_no,amount,fee,status,record_type\n"
        f"wx-exact-001,{first_order_no},10.00,0.06,SUCCESS,payment\n"
        f",{second_order_no},5.00,0.03,SUCCESS,payment\n"
    )

    first = await _upload(client, content)
    second = await _upload(client, content)
    assert first.status_code == 200
    assert second.status_code == 200
    data = first.json()["data"]
    assert data["status"] == "balanced"
    assert data["matched_count"] == 2
    assert data["difference_count"] == 0
    assert data["system_total"] == 15.0
    assert data["channel_total"] == 15.0
    assert data["fee_amount"] == 0.09
    assert second.json()["data"]["duplicate"] is True
    assert second.json()["data"]["import_id"] == data["import_id"]

    async with db_session() as session:
        assert await session.scalar(select(func.count(ChannelBillImport.id))) == 1
        assert await session.scalar(select(func.count(ChannelBillEntry.id))) == 2
        statuses = set((await session.execute(select(ChannelBillEntry.match_status))).scalars())
        assert statuses == {"matched"}


@pytest.mark.asyncio
async def test_wechat_statement_import_matches_payment_and_refund(client, db_session):
    _, order_no = await _seed_payment(
        db_session,
        amount="10.00",
        transaction_id="420000000001",
    )
    async with db_session() as session:
        order = await session.scalar(select(SaleOrder).where(SaleOrder.order_no == order_no))
        assert order is not None
        session.add(
            Payment(
                merchant_id=MERCHANT_ID,
                order_id=order.id,
                amount=Decimal("-2.00"),
                method="wechat",
                status="refunded",
            )
        )
        await session.commit()

    content = (
        "微信支付账单明细,,,,,,,\n"
        "交易时间,微信订单号,商户订单号,交易状态,应结订单金额,退款金额,微信退款单号,手续费\n"
        f"2026-07-14 08:00:00,`420000000001,`{order_no},支付成功,10.00,2.00,`REFUND001,0.06\n"
    )
    response = await _upload(client, content, name="微信支付账单.csv")
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["status"] == "balanced"
    assert data["matched_count"] == 2
    assert data["system_total"] == 8.0
    assert data["channel_total"] == 8.0


@pytest.mark.asyncio
async def test_alipay_common_headers_are_supported(client, db_session):
    _, order_no = await _seed_payment(
        db_session,
        amount="6.50",
        method="alipay",
        transaction_id="ALI20260714001",
    )
    content = (
        "支付宝交易号,商户订单号,业务类型,完成时间,商家实收（元）,服务费（元）,交易状态\n"
        f"ALI20260714001,{order_no},交易,2026-07-14 09:00:00,6.50,0.04,交易成功\n"
    )
    response = await _upload(client, content, channel="alipay", name="支付宝账单.csv")
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["status"] == "balanced"
    assert data["matched_count"] == 1
    assert data["channel_total"] == 6.5


@pytest.mark.asyncio
async def test_amount_mismatch_and_orphan_rows_create_resolvable_differences(client, db_session):
    _, mismatch_order_no = await _seed_payment(
        db_session,
        amount="10.00",
        transaction_id="wx-mismatch-001",
    )
    await _seed_payment(
        db_session,
        amount="5.00",
        transaction_id="wx-system-only-001",
    )
    content = (
        "transaction_id,merchant_order_no,amount,status,record_type\n"
        f"wx-mismatch-001,{mismatch_order_no},9.00,SUCCESS,payment\n"
        "wx-channel-only-001,CHANNEL-ONLY-ORDER,3.00,SUCCESS,payment\n"
    )

    imported = await _upload(client, content)
    assert imported.status_code == 200
    import_data = imported.json()["data"]
    assert import_data["status"] == "exception"
    assert import_data["matched_count"] == 0
    assert import_data["difference_count"] == 3
    assert import_data["unmatched_system"] == 2
    assert import_data["unmatched_channel"] == 2

    differences = await client.get(
        f"/api/v1/reconciliation/tasks/{import_data['task_id']}/differences"
    )
    assert differences.status_code == 200
    rows = differences.json()["data"]
    assert {row["diff_type"] for row in rows} == {
        "amount_mismatch",
        "system_only",
        "channel_only",
    }

    for row in rows:
        resolved = await client.post(
            f"/api/v1/reconciliation/differences/{row['id']}/resolve",
            json={"status": "resolved", "resolution": "财务已核验并完成线下调整"},
        )
        assert resolved.status_code == 200
    assert resolved.json()["data"]["task_status"] == "resolved"

    duplicate = await _upload(client, content)
    assert duplicate.status_code == 200
    assert duplicate.json()["data"]["duplicate"] is True
    assert duplicate.json()["data"]["status"] == "resolved"


@pytest.mark.asyncio
async def test_duplicate_rows_in_one_statement_are_rejected(client, db_session):
    _, order_no = await _seed_payment(
        db_session,
        amount="10.00",
        transaction_id="wx-duplicate-row-001",
    )
    row = f"wx-duplicate-row-001,{order_no},10.00,SUCCESS,payment\n"
    content = "transaction_id,merchant_order_no,amount,status,record_type\n" + row + row
    response = await _upload(client, content)
    assert response.status_code == 400
    assert "重复交易" in response.json()["detail"]


@pytest.mark.asyncio
async def test_provider_download_flows_into_import_and_matching(client, db_session, monkeypatch):
    _, order_no = await _seed_payment(
        db_session,
        amount="7.20",
        transaction_id="wx-provider-download-001",
    )
    content = (
        "transaction_id,merchant_order_no,amount,fee,status,record_type\n"
        f"wx-provider-download-001,{order_no},7.20,0.04,SUCCESS,payment\n"
    ).encode()

    async def fake_download(channel, bill_date):
        assert channel == "wechat"
        assert bill_date == utc_now().date()
        return content

    monkeypatch.setattr(
        "app.routers.reconciliation.download_channel_bill",
        fake_download,
    )
    recon_date = utc_now().date().isoformat()
    response = await client.post(
        f"/api/v1/reconciliation/download/{recon_date}?channel=wechat"
    )
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["status"] == "balanced"
    assert data["matched_count"] == 1
    assert data["system_total"] == 7.2
    assert data["channel_total"] == 7.2
