"""增强版种子数据入口 — 三赛通吃（计设 / 大创 / iCAN）。

把原 seed_db.py（1 商户 + 10 商品 + 30 天进销存）扩展为：
  - 3 个故事化摊主（菜摊 / 水果摊 / 肉摊），完整经营轨迹
  - 24 个页面全部有真实内容（POS / 采购 / 供应商 / 员工 / 财务 /
    盘点 / 食安批次 / AI 参谋 / 经营报告 / 往来账 …）
  - 刻意制造演示故事点：临期预警、赊账催收、对账差异、批次锁定召回

运行：
    cd qiantan-brain/backend
    python -m scripts.seed_enhanced

幂等：全部用确定性 UUID / 计数判重，重复运行自动跳过已存在数据。

依赖：先运行 `python -m scripts.seed_saas` 建好租户/套餐/管理员
（本脚本会自动把三摊绑到演示租户）。
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path


sys.path.insert(0, str(Path(__file__).parent.parent))

from app.database import async_session, init_db
from scripts.seed_data import (
    seed_advisor,
    seed_catalog,
    seed_finance,
    seed_inventory_and_batches,
    seed_pos_and_reconciliation,
    seed_purchasing_and_payables,
    seed_staff_and_stocktake,
)


async def main() -> None:
    print("=" * 64)
    print("千摊智脑 — 增强版种子数据（三赛通吃）")
    print("=" * 64)

    await init_db()

    async with async_session() as db:
        # 按依赖顺序编排：目录 → 库存 → POS → 采购/账期 → 财务 → 员工/盘点 → AI
        await seed_catalog(db)
        await seed_inventory_and_batches(db)
        await seed_pos_and_reconciliation(db)
        await seed_purchasing_and_payables(db)
        await seed_finance(db)
        await seed_staff_and_stocktake(db)
        await seed_advisor(db)
        await db.commit()

    print("\n" + "=" * 64)
    print("[OK] 增强版种子数据写入完成！")
    print("     3 摊主 × 24 页面全覆盖，含临期/赊账/对账差异/批次锁定故事点")
    print("     商户 ID：")
    print("       老张菜摊   a0000000-0000-0000-0000-000000000001")
    print("       王姐水果铺 a0000000-0000-0000-0000-000000000002")
    print("       刘哥鲜肉铺 a0000000-0000-0000-0000-000000000003")
    print("=" * 64)


if __name__ == "__main__":
    asyncio.run(main())
