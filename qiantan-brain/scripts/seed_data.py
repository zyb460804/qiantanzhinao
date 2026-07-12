"""
Seed script: populate the database with test data for development and demos.
Run: python scripts/seed_data.py
"""

import json
import uuid
import random
from datetime import date, datetime, timedelta
from pathlib import Path

# This script will be run with SQLAlchemy async session in production.
# For now, it prints SQL statements for manual insertion.

MERCHANT_ID = "a0000000-0000-0000-0000-000000000001"

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


def generate_inventory_records(days: int = 30) -> list[str]:
    """Generate realistic purchase and sale records for the past N days."""
    records = []
    for d in range(days, 0, -1):
        day = date.today() - timedelta(days=d)
        # Each day: purchase 3-6 products
        n_purchases = random.randint(3, 6)
        purchased = random.sample(PRODUCTS, n_purchases)

        for prod_id, name, unit, price, _, _ in purchased:
            qty = random.randint(10, 60)
            cost = round(price * random.uniform(0.5, 0.9), 2)
            total = round(qty * cost, 2)
            records.append(
                f"INSERT INTO inventory_records (id, merchant_id, product_id, quantity, unit, "
                f"unit_cost, total_amount, event_type, event_time, source) VALUES "
                f"('{uuid.uuid4()}', '{MERCHANT_ID}', {prod_id}, {qty}, '{unit}', "
                f"{cost}, {total}, 'purchase', '{day.isoformat()} 06:00:00+08', 'voice');"
            )
            # Simulate sales for same product
            sold_qty = random.randint(int(qty * 0.3), qty)
            revenue = round(sold_qty * price, 2)
            records.append(
                f"INSERT INTO inventory_records (id, merchant_id, product_id, quantity, unit, "
                f"unit_price, total_amount, event_type, event_time, source) VALUES "
                f"('{uuid.uuid4()}', '{MERCHANT_ID}', {prod_id}, -{sold_qty}, '{unit}', "
                f"{price}, {revenue}, 'sale', '{day.isoformat()} 18:00:00+08', 'voice');"
            )

    return records


def main():
    print("-- 千摊智脑 种子数据脚本")
    print(f"-- 商户ID: {MERCHANT_ID}\n")

    # Insert merchant
    print(
        f"INSERT INTO merchants (id, name, business_type, location) VALUES "
        f"('{MERCHANT_ID}', '老张菜摊', '蔬菜水果', '上海市浦东新区xx菜市场');"
    )

    # Insert product categories
    print("\n-- 商品品类")
    for prod_id, name, unit, price, shelf_life, group in PRODUCTS:
        print(
            f"INSERT INTO product_categories (id, name, unit, default_price, shelf_life_hours, category_group) "
            f"VALUES ({prod_id}, '{name}', '{unit}', {price}, {shelf_life}, '{group}');"
        )

    # Insert environment records
    print("\n-- 环境数据（最近30天）")
    for d in range(30, 0, -1):
        day = date.today() - timedelta(days=d)
        temp_h = round(random.uniform(15, 35), 1)
        temp_l = round(temp_h - random.uniform(5, 12), 1)
        rain = random.randint(0, 100)
        weather = "雨" if rain > 50 else "多云" if rain > 20 else "晴"
        dow = day.weekday()
        print(
            f"INSERT INTO environment_records (date, city, temp_high, temp_low, weather_type, "
            f"rainfall_prob, is_holiday, day_of_week, is_weekend) VALUES "
            f"('{day.isoformat()}', '上海', {temp_h}, {temp_l}, '{weather}', "
            f"{rain}, false, {dow}, {str(dow >= 5).lower()});"
        )

    # Generate inventory records
    print("\n-- 经营记录（最近30天）")
    records = generate_inventory_records(30)
    for r in records:
        print(r)

    print(f"\n-- 共生成 {len(records)} 条经营记录")
    print("-- 运行完成！将以上SQL在数据库中执行即可。")


if __name__ == "__main__":
    main()
