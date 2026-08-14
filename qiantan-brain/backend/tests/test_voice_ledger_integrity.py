"""语音记账链路完整性测试 —— confirm 幂等兜底、void/edit 冲销与防线。

覆盖（af3df55 之后的修复）：
  1. confirm 双重提交 → 只落一条库存流水（锚点行锁 + 幂等键唯一约束），赊账只落一条应收
  2. edit 数量超过 FIFO 可用量 → 409 且事务回滚（库存不被改负）
  3. 赊账/回款记录 void / edit → 往来账净额冲平/按修正后金额对齐
  4. source="pos" 的流水拒绝语音 void/edit（订单体系另有退款链路）
"""

import uuid
from decimal import Decimal

import pytest
from sqlalchemy import select
from tests.conftest import TEST_MERCHANT_ID

from app.core.timezone import utc_now
from app.models.accounts import CustomerReceivable
from app.models.inventory import InventoryRecord
from app.models.voice import VoiceLog
from app.services.accounts_service import get_customer_balance


pytestmark = pytest.mark.asyncio

MERCHANT_ID = uuid.UUID(TEST_MERCHANT_ID)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _seed_voice_log(db_session, parsed_event: dict, *, status: str = "parsed") -> uuid.UUID:
    """直接落一条 VoiceLog，parsed_event 完全由测试控制（绕过 ASR/解析随机性）。"""
    async with db_session() as session:
        log = VoiceLog(
            merchant_id=MERCHANT_ID,
            asr_text="seed",
            parsed_event=parsed_event,
            status=status,
        )
        session.add(log)
        await session.commit()
        await session.refresh(log)
        return log.id


async def _purchase_stock(client, text: str = "进了白菜50斤，三毛钱一斤") -> None:
    """通过 API 完成一次采购确认，为后续 sale/waste 提供可售批次。"""
    resp = await client.post(
        "/api/v1/voice/parse-text",
        json={"merchant_id": TEST_MERCHANT_ID, "text": text},
    )
    assert resp.status_code == 200
    log_id = resp.json()["data"]["parsed"]["voice_log_id"]
    resp = await client.post("/api/v1/voice/confirm", json={"voice_log_id": log_id})
    assert resp.status_code == 200


async def _fetch_receivables(db_session) -> list[CustomerReceivable]:
    async with db_session() as session:
        rows = (
            (
                await session.execute(
                    select(CustomerReceivable).where(
                        CustomerReceivable.merchant_id == MERCHANT_ID
                    )
                )
            )
            .scalars()
            .all()
        )
        return list(rows)


async def _customer_balance(db_session, name: str) -> Decimal:
    async with db_session() as session:
        return await get_customer_balance(session, MERCHANT_ID, name)


def _credit_sale_event(quantity: float = 10, total: float = 30, party: str = "张记饭店") -> dict:
    return {
        "event_type": "sale",
        "product": "白菜",
        "product_id": 1,
        "quantity": quantity,
        "unit": "斤",
        "unit_price": 3,
        "total_amount": total,
        "party_name": party,
        "is_credit": True,
        "is_repay": False,
    }


def _cash_sale_event(quantity: float = 5) -> dict:
    return {
        "event_type": "sale",
        "product": "白菜",
        "product_id": 1,
        "quantity": quantity,
        "unit": "斤",
        "unit_price": 2,
        "total_amount": quantity * 2,
    }


def _repay_event(total: float = 50, party: str = "老李饭店") -> dict:
    return {
        "event_type": "sale",
        "product": "白菜",
        "product_id": 1,
        "quantity": 1,
        "unit": "斤",
        "total_amount": total,
        "party_name": party,
        "is_credit": False,
        "is_repay": True,
    }


# ---------------------------------------------------------------------------
# 1. confirm 幂等：双重提交只入账一次
# ---------------------------------------------------------------------------


class TestConfirmIdempotency:
    async def test_double_confirm_writes_single_ledger_entry(self, client, db_session):
        """同一 log 连续两次 confirm → 只落一条库存流水，且带幂等键。"""
        resp = await client.post(
            "/api/v1/voice/parse-text",
            json={"merchant_id": TEST_MERCHANT_ID, "text": "进了白菜50斤，三毛钱一斤"},
        )
        voice_log_id = resp.json()["data"]["parsed"]["voice_log_id"]

        first = await client.post("/api/v1/voice/confirm", json={"voice_log_id": voice_log_id})
        second = await client.post("/api/v1/voice/confirm", json={"voice_log_id": voice_log_id})

        assert first.status_code == 200
        assert second.status_code == 200
        assert second.json()["data"]["idempotent"] is True

        async with db_session() as session:
            records = (
                (
                    await session.execute(
                        select(InventoryRecord).where(
                            InventoryRecord.voice_log_id == uuid.UUID(voice_log_id)
                        )
                    )
                )
                .scalars()
                .all()
            )
        assert len(records) == 1
        assert records[0].idempotency_key == f"voice:{voice_log_id}"
        assert float(records[0].quantity) == 50.0

    async def test_double_confirm_credit_sale_single_receivable(self, client, db_session):
        """赊账销售双重确认 → 应收只落一条 charge 流水。"""
        await _purchase_stock(client)
        log_id = await _seed_voice_log(db_session, _credit_sale_event())

        for _ in range(2):
            resp = await client.post("/api/v1/voice/confirm", json={"voice_log_id": str(log_id)})
            assert resp.status_code == 200

        receivables = await _fetch_receivables(db_session)
        assert len(receivables) == 1
        assert receivables[0].direction == "charge"
        assert float(receivables[0].amount) == 30.0
        assert receivables[0].idempotency_key == f"voice:{log_id}:charge"


# ---------------------------------------------------------------------------
# 2. edit 防线：FIFO 不足 409 + 事务回滚
# ---------------------------------------------------------------------------


class TestEditGuardrails:
    async def test_edit_over_stock_rejected_and_rolled_back(self, client, db_session):
        """edit 改成超库存数量 → 409，原记录未作废、库存不变。"""
        await _purchase_stock(client, "进了白菜10斤，三毛钱一斤")  # 可售 10 斤
        log_id = await _seed_voice_log(db_session, _cash_sale_event(quantity=5))
        confirm = await client.post("/api/v1/voice/confirm", json={"voice_log_id": str(log_id)})
        assert confirm.status_code == 200  # 剩余 5 斤

        edit = await client.put(
            f"/api/v1/voice/{log_id}/edit",
            json={"quantity": 50, "reason": "改错数量"},
        )
        assert edit.status_code == 409
        assert "库存不足" in edit.json()["detail"]

        # 事务回滚：原销售记录仍在账上，未被作废，也没有冲正记录
        async with db_session() as session:
            records = (
                (
                    await session.execute(
                        select(InventoryRecord).where(
                            InventoryRecord.voice_log_id == log_id
                        )
                    )
                )
                .scalars()
                .all()
            )
        assert len(records) == 1
        assert records[0].is_voided is False
        assert float(records[0].quantity) == -5.0

        inv = await client.get(
            "/api/v1/inventory/current", params={"merchant_id": TEST_MERCHANT_ID}
        )
        baicai = [i for i in inv.json()["data"] if i.get("product_id") == 1][0]
        assert baicai["current_qty"] == 5.0  # 10 进 - 5 卖，未被改负

    async def test_successful_edit_sets_correction_idempotency_key(self, client, db_session):
        """正常 edit → 冲正记录带 voice:{log.id}:edit{n} 幂等键。"""
        await _purchase_stock(client)
        log_id = await _seed_voice_log(db_session, _cash_sale_event(quantity=5))
        confirm = await client.post("/api/v1/voice/confirm", json={"voice_log_id": str(log_id)})
        assert confirm.status_code == 200

        edit = await client.put(
            f"/api/v1/voice/{log_id}/edit", json={"quantity": 8, "reason": "少记了"}
        )
        assert edit.status_code == 200

        async with db_session() as session:
            correction = (
                await session.execute(
                    select(InventoryRecord).where(
                        InventoryRecord.voice_log_id == log_id,
                        InventoryRecord.is_correction.is_(True),
                    )
                )
            ).scalar_one()
        assert correction.idempotency_key == f"voice:{log_id}:edit1"
        assert float(correction.quantity) == -8.0


# ---------------------------------------------------------------------------
# 3. void/edit 冲销往来账
# ---------------------------------------------------------------------------


class TestVoidEditReceivables:
    async def test_void_credit_sale_zeroes_receivable(self, client, db_session):
        """赊账记录 void → 应收余额归零（charge + 反向 repay）。"""
        await _purchase_stock(client)
        log_id = await _seed_voice_log(db_session, _credit_sale_event())
        confirm = await client.post("/api/v1/voice/confirm", json={"voice_log_id": str(log_id)})
        assert confirm.status_code == 200
        assert float(await _customer_balance(db_session, "张记饭店")) == 30.0

        void = await client.post(
            f"/api/v1/voice/{log_id}/void", json={"reason": "记错对手方了"}
        )
        assert void.status_code == 200

        assert float(await _customer_balance(db_session, "张记饭店")) == 0.0
        receivables = await _fetch_receivables(db_session)
        assert len(receivables) == 2
        repay_rows = [r for r in receivables if r.direction == "repay"]
        assert len(repay_rows) == 1
        assert repay_rows[0].idempotency_key == f"voice:{log_id}:void:repay"
        assert float(repay_rows[0].amount) == 30.0

    async def test_void_repay_log_returns_balance_to_pre_repay_state(self, client, db_session):
        """回款记录 void → 该语音单往来账净额归零，余额回到回款前状态。

        只有回款流水时余额为负（相当于预收）；void 写入反向 charge 抵销
        回款，余额恢复为 0 —— 而不是凭空产生一笔应收。
        """
        await _purchase_stock(client)
        log_id = await _seed_voice_log(db_session, _repay_event(total=50))
        confirm = await client.post("/api/v1/voice/confirm", json={"voice_log_id": str(log_id)})
        assert confirm.status_code == 200
        assert float(await _customer_balance(db_session, "老李饭店")) == -50.0

        void = await client.post(f"/api/v1/voice/{log_id}/void", json={"reason": "重复记账"})
        assert void.status_code == 200

        assert float(await _customer_balance(db_session, "老李饭店")) == 0.0
        receivables = await _fetch_receivables(db_session)
        reversal = [r for r in receivables if r.idempotency_key == f"voice:{log_id}:void:charge"]
        assert len(reversal) == 1
        assert float(reversal[0].amount) == 50.0

    async def test_edit_credit_sale_tracks_corrected_amount(self, client, db_session):
        """赊账记录 edit 金额 → 应收对齐修正后金额；重复编辑不双重冲销。"""
        await _purchase_stock(client)
        log_id = await _seed_voice_log(db_session, _credit_sale_event(total=30))
        confirm = await client.post("/api/v1/voice/confirm", json={"voice_log_id": str(log_id)})
        assert confirm.status_code == 200

        # 第一次编辑：30 → 25（净额 30 冲 5，余额 25）
        edit1 = await client.put(
            f"/api/v1/voice/{log_id}/edit", json={"total_amount": 25, "reason": "算错价"}
        )
        assert edit1.status_code == 200
        assert float(await _customer_balance(db_session, "张记饭店")) == 25.0

        # 第二次编辑：25 → 20（净额 25 冲 5，余额 20，不会把 30 再冲一遍）
        edit2 = await client.put(
            f"/api/v1/voice/{log_id}/edit", json={"total_amount": 20, "reason": "再改"}
        )
        assert edit2.status_code == 200
        assert float(await _customer_balance(db_session, "张记饭店")) == 20.0

        receivables = await _fetch_receivables(db_session)
        keys = sorted(r.idempotency_key for r in receivables)
        assert keys == [
            f"voice:{log_id}:charge",
            f"voice:{log_id}:edit1:repay",
            f"voice:{log_id}:edit2:repay",
        ]

    async def test_void_cash_sale_leaves_receivables_untouched(self, client, db_session):
        """现金销售（无对手方）void → 不产生任何往来账流水。"""
        await _purchase_stock(client)
        log_id = await _seed_voice_log(db_session, _cash_sale_event(quantity=5))
        confirm = await client.post("/api/v1/voice/confirm", json={"voice_log_id": str(log_id)})
        assert confirm.status_code == 200

        void = await client.post(f"/api/v1/voice/{log_id}/void", json={"reason": "误记"})
        assert void.status_code == 200
        assert await _fetch_receivables(db_session) == []


# ---------------------------------------------------------------------------
# 4. source="pos" 防线
# ---------------------------------------------------------------------------


class TestStateConflictStatuses:
    """锚点锁后的状态冲突统一 409（与 pos/purchase/inventory 约定一致）。"""

    async def test_double_void_returns_409(self, client, db_session):
        await _purchase_stock(client)
        log_id = await _seed_voice_log(db_session, _cash_sale_event(quantity=5))
        assert (
            await client.post("/api/v1/voice/confirm", json={"voice_log_id": str(log_id)})
        ).status_code == 200

        first = await client.post(f"/api/v1/voice/{log_id}/void", json={"reason": "误记"})
        second = await client.post(f"/api/v1/voice/{log_id}/void", json={"reason": "再点一次"})

        assert first.status_code == 200
        assert second.status_code == 409
        assert "已撤销" in second.json()["detail"]

    async def test_edit_after_void_returns_409(self, client, db_session):
        await _purchase_stock(client)
        log_id = await _seed_voice_log(db_session, _cash_sale_event(quantity=5))
        assert (
            await client.post("/api/v1/voice/confirm", json={"voice_log_id": str(log_id)})
        ).status_code == 200
        assert (
            await client.post(f"/api/v1/voice/{log_id}/void", json={"reason": "误记"})
        ).status_code == 200

        resp = await client.put(
            f"/api/v1/voice/{log_id}/edit", json={"quantity": 3, "reason": "改数量"}
        )
        assert resp.status_code == 409
        assert "只能修改已确认" in resp.json()["detail"]


class TestPosSourceGuard:
    async def _seed_confirmed_pos_log(self, db_session) -> uuid.UUID:
        async with db_session() as session:
            log = VoiceLog(
                merchant_id=MERCHANT_ID,
                asr_text="pos",
                parsed_event=_cash_sale_event(quantity=5),
                status="confirmed",
            )
            session.add(log)
            await session.flush()
            session.add(
                InventoryRecord(
                    merchant_id=MERCHANT_ID,
                    product_id=1,
                    quantity=Decimal("-5"),
                    unit="斤",
                    event_type="sale",
                    event_time=utc_now(),
                    source="pos",
                    voice_log_id=log.id,
                )
            )
            await session.commit()
            return log.id

    async def test_void_pos_source_rejected(self, client, db_session):
        log_id = await self._seed_confirmed_pos_log(db_session)

        resp = await client.post(f"/api/v1/voice/{log_id}/void", json={"reason": "误操作"})

        assert resp.status_code == 409
        assert "订单退款" in resp.json()["detail"]
        # 状态与流水均未被改动
        async with db_session() as session:
            log = await session.get(VoiceLog, log_id)
            record = (
                await session.execute(
                    select(InventoryRecord).where(InventoryRecord.voice_log_id == log_id)
                )
            ).scalar_one()
        assert log.status == "confirmed"
        assert record.is_voided is False

    async def test_edit_pos_source_rejected(self, client, db_session):
        log_id = await self._seed_confirmed_pos_log(db_session)

        resp = await client.put(
            f"/api/v1/voice/{log_id}/edit", json={"quantity": 3, "reason": "改数量"}
        )

        assert resp.status_code == 409
        assert "订单退款" in resp.json()["detail"]
        async with db_session() as session:
            record = (
                await session.execute(
                    select(InventoryRecord).where(InventoryRecord.voice_log_id == log_id)
                )
            ).scalar_one()
        assert record.is_voided is False


# ---------------------------------------------------------------------------
# 5. /correct 状态守卫（V2-M1）
# ---------------------------------------------------------------------------


class TestCorrectStateGuard:
    async def test_correct_confirmed_log_rejected(self, client, db_session):
        """已确认的单调用 correct → 409，状态不被打回 parsed。

        修复前：correct 无状态检查，把 confirmed 打回 parsed 后二次 confirm
        会撞 voice:{log.id} 幂等唯一键 → 500。
        """
        await _purchase_stock(client)
        log_id = await _seed_voice_log(db_session, _cash_sale_event(quantity=5))
        confirm = await client.post("/api/v1/voice/confirm", json={"voice_log_id": str(log_id)})
        assert confirm.status_code == 200

        resp = await client.post(
            "/api/v1/voice/correct",
            json={"voice_log_id": str(log_id), "corrections": {"quantity": 3}},
        )
        assert resp.status_code == 409
        assert "只能修正未确认" in resp.json()["detail"]

        async with db_session() as session:
            log = await session.get(VoiceLog, log_id)
            assert log.status == "confirmed"  # 状态未被改回 parsed
        # 后续 confirm 仍走幂等短路，而不是撞唯一键
        again = await client.post("/api/v1/voice/confirm", json={"voice_log_id": str(log_id)})
        assert again.status_code == 200
        assert again.json()["data"]["idempotent"] is True

    async def test_correct_parsed_log_still_works(self, client, db_session):
        """未确认（parsed）的单正常修正 → 字段更新、状态保持 parsed。"""
        log_id = await _seed_voice_log(db_session, _cash_sale_event(quantity=5))

        resp = await client.post(
            "/api/v1/voice/correct",
            json={"voice_log_id": str(log_id), "corrections": {"quantity": 3}},
        )
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["parsed"]["quantity"] == 3

        async with db_session() as session:
            log = await session.get(VoiceLog, log_id)
            assert log.status == "parsed"
            assert log.correction_count == 1

    async def test_correct_voided_log_rejected(self, client, db_session):
        """已撤销的单不允许 correct（重新解析需走新语音单）。"""
        await _purchase_stock(client)
        log_id = await _seed_voice_log(db_session, _cash_sale_event(quantity=5))
        assert (
            await client.post("/api/v1/voice/confirm", json={"voice_log_id": str(log_id)})
        ).status_code == 200
        assert (
            await client.post(f"/api/v1/voice/{log_id}/void", json={"reason": "误记"})
        ).status_code == 200

        resp = await client.post(
            "/api/v1/voice/correct",
            json={"voice_log_id": str(log_id), "corrections": {"quantity": 3}},
        )
        assert resp.status_code == 409
