"""一次性 backfill：把历史 category 账本补上 sku_id（P0-B 收口）。

逻辑（幂等、可重复运行）：
1. 遍历每个商户。
2. 收集该商户在 inventory_records / purchase_items / batch_lifecycles 中用过的
   product_id（全局品类整数）。
3. 对每个 product_id，ensure_sku_for_category：若该商户下无对应 SKU，按品类名补建。
4. 把三张表里 sku_id 仍为空、且 product_id 相同的行，UPDATE 为解析到的 sku_id。

前置：先确保列已存在（生产走 Alembic 005；本地 dev 先跑 dev_apply_sku_columns.py）。

用法：
    cd qiantan-brain/backend
    python scripts/dev_apply_sku_columns.py   # 仅 dev
    python scripts/backfill_sku.py
"""

import asyncio

from sqlalchemy import distinct, select, update

from app.database import async_session
from app.models.batch import BatchLifecycle
from app.models.inventory import InventoryRecord
from app.models.merchant import Merchant
from app.models.purchase import PurchaseItem
from app.services.sku_service import ensure_sku_for_category


_LEDGER_MODELS = (InventoryRecord, PurchaseItem, BatchLifecycle)


async def backfill() -> int:
    total = 0
    async with async_session() as db:
        merchants = (await db.execute(select(Merchant))).scalars().all()
        for m in merchants:
            # 该商户用过的全部 product_id（去重）
            pids: set[int] = set()
            for model in _LEDGER_MODELS:
                rows = (
                    await db.execute(
                        select(distinct(model.product_id)).where(
                            model.merchant_id == m.id
                        )
                    )
                ).scalars().all()
                pids.update(rows)

            for pid in pids:
                sku_id = await ensure_sku_for_category(db, m.id, pid)
                if not sku_id:
                    print(f"[skip] merchant={m.id} product_id={pid} 无对应品类，跳过")
                    continue
                for model in _LEDGER_MODELS:
                    res = await db.execute(
                        update(model)
                        .where(
                            model.merchant_id == m.id,
                            model.product_id == pid,
                            model.sku_id.is_(None),
                        )
                        .values(sku_id=sku_id)
                    )
                    total += res.rowcount or 0
            await db.commit()
        print(f"[backfill] done. rows updated: {total}")
        return total


if __name__ == "__main__":
    asyncio.run(backfill())
