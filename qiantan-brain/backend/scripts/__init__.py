"""
Async seed script: populate DB with test data for dev/demo.
Run: python -m scripts.seed_db
"""

import asyncio
import random
import sys
import uuid
from datetime import date, datetime, timedelta
from pathlib import Path


sys.path.insert(0, str(Path(__file__).parent.parent))

from sqlalchemy import select

from app.database import async_session, init_db
from app.models.environment import EnvironmentRecord
from app.models.inventory import InventoryRecord
from app.models.merchant import Merchant
from app.models.product import ProductCategory


MERCHANT_ID = uuid.UUID("a0000000-0000-0000-0000-000000000001")

PRODUCTS = [
    (1, "白菜", "斤", 1.5, 72, "叶菜类"),
    (2, "菠菜", "斤", 3.0, 72, "叶菜类"),
    (3, "土豆", "斤", 2.0, 168, "根茎类"),
    (4, "豆腐", "斤", 2.5, 24, "豆制品"),
    (5, "黄瓜", "斤", 3.5, 96, "瓜果类"),
    (6, "番茄", "斤", 4.0, 96, "瓜果类"),
    (7, "西瓜", "斤", 2.0, 120, "水果类"),
    (8, "苹果", "斤", 6.0, 120, "水果类"),
    (9, "猪肉", "斤", 15.0, 48, "肉类"),
    (10, "鸡蛋", "斤", 6.0, 720, "肉类"),
]


def generate_inventory_records(merchant_id: uuid.UUID, days: int = 30) -> list[InventoryRecord]:
    """Generate realistic purchase and sale records for the past N days."""
    records = []
    for d in range(days, 0, -1):
        day = date.today() - timedelta(days=d)
        n_purchases = random.randint(3, 6)
        purchased = random.sample(PRODUCTS, n_purchases)

        for prod_id, name, unit, price, _, _ in purchased:
            qty = random.randint(10, 60)
            cost = round(price * random.uniform(0.5, 0.9), 2)
            total = round(qty * cost, 2)

            # Purchase record
            records.append(
                InventoryRecord(
                    merchant_id=merchant_id,
                    product_id=prod_id,
                    quantity=qty,
                    unit=unit,
                    unit_cost=cost,
                    total_amount=total,
                    event_type="purchase",
                    event_time=datetime(day.year, day.month, day.day, 6, 0, 0),
                    source="seed",
                    batch_label=f"{name}-{day.strftime('%m%d')}",
                )
            )

            # Sale record
            sold_qty = random.randint(int(qty * 0.3), qty)
            revenue = round(sold_qty * price, 2)
            records.append(
                InventoryRecord(
                    merchant_id=merchant_id,
                    product_id=prod_id,
                    quantity=-sold_qty,
                    unit=unit,
                    unit_price=price,
                    total_amount=revenue,
                    event_type="sale",
                    event_time=datetime(day.year, day.month, day.day, 18, 0, 0),
                    source="seed",
                )
            )

    return records


async def seed():
    """Main seeding logic."""
    await init_db()

    async with async_session() as db:
        # Check if already seeded
        result = await db.execute(select(Merchant).where(Merchant.id == MERCHANT_ID))
        if result.scalar_one_or_none():
            print("Database already seeded. Skipping.")
            return

        # 1. Merchant
        merchant = Merchant(
            id=MERCHANT_ID,
            name="老张菜摊",
            business_type="蔬菜水果",
            location="上海市浦东新区xx菜市场",
        )
        db.add(merchant)

        # 2. Product categories
        for prod_id, name, unit, price, shelf_life, group in PRODUCTS:
            db.add(
                ProductCategory(
                    id=prod_id,
                    name=name,
                    unit=unit,
                    default_price=price,
                    shelf_life_hours=shelf_life,
                    category_group=group,
                )
            )

        # 3. Environment records (30 days)
        for d in range(30, 0, -1):
            day = date.today() - timedelta(days=d)
            temp_h = round(random.uniform(15, 35), 1)
            temp_l = round(temp_h - random.uniform(5, 12), 1)
            rain = random.randint(0, 100)
            weather = "雨" if rain > 50 else "多云" if rain > 20 else "晴"
            dow = day.weekday()
            db.add(
                EnvironmentRecord(
                    date=day,
                    city="上海",
                    temp_high=temp_h,
                    temp_low=temp_l,
                    weather_type=weather,
                    rainfall_prob=float(rain),
                    is_holiday=False,
                    day_of_week=dow,
                    is_weekend=dow >= 5,
                )
            )

        await db.flush()

        # 4. Inventory records (30 days)
        records = generate_inventory_records(MERCHANT_ID, 30)
        for r in records:
            db.add(r)

        await db.commit()
        print(
            f"✅ Seeded: 1 merchant, 10 products, 30 env records, {len(records)} inventory records"
        )


if __name__ == "__main__":
    asyncio.run(seed())
