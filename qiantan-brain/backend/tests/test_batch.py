"""Unit tests for batch lifecycle tracking service.

Covers batch creation (shelf-life derived expiry), FIFO consumption,
and the expiry-counting query that powers dashboard alerts.

Uses the shared ``db_session`` fixture from conftest, which seeds:
  product 1 = 白菜 (叶菜类, 72h), 2 = 土豆 (根茎类, 168h),
  product 3 = 豆腐 (豆制品, 24h),   4 = 猪肉 (肉类, 48h)
"""

import uuid
from datetime import datetime, timedelta

from sqlalchemy import select

from app.models.batch import BatchLifecycle
from app.services.batch import (
    consume_batches_fifo,
    count_expiring_batches,
    create_batch,
    get_active_batches,
)


# Matches the merchant seeded by conftest.db_session.
MERCHANT_ID = uuid.UUID("00000000-0000-0000-0000-000000000001")


# ── create_batch ──────────────────────────────────────────────────────


async def test_create_batch_sets_expiry_from_shelf_life(db_session):
    """白菜 (叶菜类) → expiry = purchase_time + 72h."""
    purchase_time = datetime(2026, 7, 11, 8, 0, 0)
    async with db_session() as session:
        batch = await create_batch(
            session,
            merchant_id=MERCHANT_ID,
            product_id=1,
            product_name="白菜",
            batch_label="白菜-0701",
            quantity=50,
            purchase_time=purchase_time,
        )
        await session.commit()

        assert batch.purchase_qty == 50
        assert batch.remaining_qty == 50
        assert batch.status == "sellable"
        assert batch.expiry_date == purchase_time + timedelta(hours=72)


async def test_create_batch_uses_correct_shelf_life_per_category(db_session):
    """豆腐 24h vs 土豆 168h vs 猪肉 48h."""
    now = datetime.now()
    async with db_session() as session:
        tofu = await create_batch(session, MERCHANT_ID, 3, "豆腐", "豆腐-1", 10, now)
        potato = await create_batch(session, MERCHANT_ID, 2, "土豆", "土豆-1", 20, now)
        pork = await create_batch(session, MERCHANT_ID, 4, "猪肉", "猪肉-1", 15, now)
        await session.commit()

        assert tofu.expiry_date == now + timedelta(hours=24)
        assert potato.expiry_date == now + timedelta(hours=168)
        assert pork.expiry_date == now + timedelta(hours=48)


# ── consume_batches_fifo ──────────────────────────────────────────────


async def test_consume_fifo_drains_oldest_batch_first(db_session):
    """Oldest batch is consumed before newer ones."""
    old_time = datetime(2026, 7, 9, 8, 0, 0)
    new_time = datetime(2026, 7, 11, 8, 0, 0)
    async with db_session() as session:
        await create_batch(session, MERCHANT_ID, 1, "白菜", "白菜-old", 30, old_time)
        await create_batch(session, MERCHANT_ID, 1, "白菜", "白菜-new", 30, new_time)
        await session.commit()

        consumed = await consume_batches_fifo(session, MERCHANT_ID, 1, 25)
        await session.commit()

        assert consumed == 25

        batches = (
            (
                await session.execute(
                    select(BatchLifecycle)
                    .where(BatchLifecycle.merchant_id == MERCHANT_ID)
                    .order_by(BatchLifecycle.purchase_date)
                )
            )
            .scalars()
            .all()
        )
        assert float(batches[0].remaining_qty) == 5  # old: 30 - 25
        assert float(batches[1].remaining_qty) == 30  # new untouched


async def test_consume_fifo_spans_multiple_batches(db_session):
    """Consumption larger than one batch spills into the next."""
    old_time = datetime(2026, 7, 9, 8, 0, 0)
    new_time = datetime(2026, 7, 11, 8, 0, 0)
    async with db_session() as session:
        await create_batch(session, MERCHANT_ID, 1, "白菜", "白菜-old", 20, old_time)
        await create_batch(session, MERCHANT_ID, 1, "白菜", "白菜-new", 20, new_time)
        await session.commit()

        consumed = await consume_batches_fifo(session, MERCHANT_ID, 1, 30)
        await session.commit()

        assert consumed == 30
        batches = (
            (
                await session.execute(
                    select(BatchLifecycle)
                    .where(BatchLifecycle.merchant_id == MERCHANT_ID)
                    .order_by(BatchLifecycle.purchase_date)
                )
            )
            .scalars()
            .all()
        )
        assert float(batches[0].remaining_qty) == 0  # old fully drained
        assert float(batches[1].remaining_qty) == 10  # new: 20 - 10


async def test_consume_fifo_returns_actual_when_stock_insufficient(db_session):
    """Insufficient stock returns only what was available, not the request."""
    async with db_session() as session:
        await create_batch(
            session,
            MERCHANT_ID,
            1,
            "白菜",
            "白菜-1",
            10,
            datetime(2026, 7, 11),
        )
        await session.commit()

        consumed = await consume_batches_fifo(session, MERCHANT_ID, 1, 25)

        assert consumed == 10


async def test_consume_fifo_zero_quantity_is_noop(db_session):
    """Consuming zero quantity changes nothing."""
    async with db_session() as session:
        await create_batch(
            session,
            MERCHANT_ID,
            1,
            "白菜",
            "白菜-1",
            10,
            datetime(2026, 7, 11),
        )
        await session.commit()

        consumed = await consume_batches_fifo(session, MERCHANT_ID, 1, 0)

        assert consumed == 0


async def test_consume_fifo_isolates_by_product(db_session):
    """Consuming product A must not touch product B's batches."""
    async with db_session() as session:
        await create_batch(
            session,
            MERCHANT_ID,
            1,
            "白菜",
            "白菜-1",
            20,
            datetime(2026, 7, 11),
        )
        await create_batch(
            session,
            MERCHANT_ID,
            2,
            "土豆",
            "土豆-1",
            20,
            datetime(2026, 7, 11),
        )
        await session.commit()

        await consume_batches_fifo(session, MERCHANT_ID, 1, 20)
        await session.commit()

        cabbage = (
            await session.execute(select(BatchLifecycle).where(BatchLifecycle.product_id == 1))
        ).scalar_one()
        potato = (
            await session.execute(select(BatchLifecycle).where(BatchLifecycle.product_id == 2))
        ).scalar_one()
        assert float(cabbage.remaining_qty) == 0
        assert float(potato.remaining_qty) == 20


# ── count_expiring_batches / get_active_batches ───────────────────────


async def test_count_expiring_catches_batch_within_window(db_session):
    """A batch expiring inside the window is counted; a fresh one is not."""
    async with db_session() as session:
        # Entered 70h ago → expires in 2h (within the 24h window).
        await create_batch(
            session,
            MERCHANT_ID,
            1,
            "白菜",
            "白菜-临期",
            15,
            datetime.now() - timedelta(hours=70),
        )
        # Fresh batch → expires in 72h.
        await create_batch(
            session,
            MERCHANT_ID,
            1,
            "白菜",
            "白菜-新鲜",
            15,
            datetime.now(),
        )
        await session.commit()

        count = await count_expiring_batches(session, MERCHANT_ID, within_hours=24)

        assert count == 1


async def test_count_expiring_excludes_depleted_batch(db_session):
    """A fully-consumed batch is not counted even if it is near expiry."""
    async with db_session() as session:
        await create_batch(
            session,
            MERCHANT_ID,
            1,
            "白菜",
            "白菜-临期",
            15,
            datetime.now() - timedelta(hours=70),
        )
        await session.commit()

        await consume_batches_fifo(session, MERCHANT_ID, 1, 15)
        await session.commit()

        count = await count_expiring_batches(session, MERCHANT_ID, within_hours=24)

        assert count == 0


async def test_get_active_batches_ordered_by_expiry(db_session):
    """Active batches are returned soonest-expiring first."""
    async with db_session() as session:
        await create_batch(
            session,
            MERCHANT_ID,
            1,
            "白菜",
            "白菜-晚",
            10,
            datetime.now(),
        )
        await create_batch(
            session,
            MERCHANT_ID,
            1,
            "白菜",
            "白菜-早",
            10,
            datetime.now() - timedelta(hours=60),
        )
        await session.commit()

        batches = await get_active_batches(session, MERCHANT_ID)

        assert len(batches) == 2
        assert batches[0].expiry_date <= batches[1].expiry_date
