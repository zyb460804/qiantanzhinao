"""Dev-only：把陈旧 dev 库补齐到当前全量基线（5242218be814）。

背景：仓库已把全部 schema 收敛到单条「initial full schema baseline」迁移，
其中已包含 sku_id / supplier_id / payment_status / paid_amount 等列。但本地
qiantan_dev.db 是用更早的 create_all 建的，缺这些列，导致模型与库漂移、
运行时报「列不存在」。

本脚本幂等地补上缺失列（保留数据），随后应执行 `alembic stamp 5242218be814`
把基线标记为已应用。生产/CI 用全新库直接 `alembic upgrade head` 即可，无需本脚本。

用法：
    cd qiantan-brain/backend
    python scripts/dev_sync_schema.py
    DATABASE_URL=sqlite+aiosqlite:///./qiantan_dev.db alembic stamp 5242218be814
"""

import asyncio

from sqlalchemy import text

from app.database import engine


# (表, 列, 类型, 是否可空, 默认值SQL)  —— 必须与此后基线迁移的列定义一致
_COLUMNS = [
    ("inventory_records", "sku_id", "VARCHAR", True, None),
    ("purchase_items", "sku_id", "VARCHAR", True, None),
    ("purchase_items", "supplier_id", "VARCHAR", True, None),
    ("batch_lifecycles", "sku_id", "VARCHAR", True, None),
    ("purchase_lists", "payment_status", "VARCHAR(20)", False, "'unpaid'"),
    ("purchase_lists", "paid_amount", "NUMERIC(12,2)", False, "0"),
]


async def sync() -> None:
    async with engine.begin() as conn:
        for table, col, ctype, nullable, default in _COLUMNS:
            existing = {
                r[1] for r in (await conn.execute(text(f"PRAGMA table_info({table})"))).fetchall()
            }
            if col in existing:
                print(f"[skip] {table}.{col} already exists")
                continue
            ddl = f"ALTER TABLE {table} ADD COLUMN {col} {ctype}"
            if not nullable:
                ddl += f" NOT NULL DEFAULT {default}"
            await conn.execute(text(ddl))
            print(f"[add]  {table}.{col}")


if __name__ == "__main__":
    asyncio.run(sync())
