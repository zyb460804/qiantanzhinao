"""增强版种子数据包 — 三赛通吃（计设 / 大创 / iCAN）。

把原 seed_db.py 的"1 商户 + 10 商品 + 30 天进销存"扩展为：
  - 3 个故事化摊主（菜摊 / 水果摊 / 肉摊），各自有完整经营轨迹
  - 每个摊主覆盖全部 24 个页面（POS / 采购 / 供应商 / 员工 / 财务 /
    盘点 / 食安批次 / AI 参谋 / 经营报告 / 往来账 …）
  - 刻意制造可演示的"故事点"：临期预警、赊账催收、对账差异、批次锁定、
    AI 建议采纳/拒绝 …

全部使用确定性 UUID，每个分片幂等（重复运行跳过已存在记录）。

入口：python -m scripts.seed_enhanced
"""

from scripts.seed_data.advisor import seed_advisor
from scripts.seed_data.catalog import seed_catalog
from scripts.seed_data.finance import seed_finance
from scripts.seed_data.inventory import seed_inventory_and_batches
from scripts.seed_data.pos import seed_pos_and_reconciliation
from scripts.seed_data.purchasing import seed_purchasing_and_payables
from scripts.seed_data.staffing import seed_staff_and_stocktake

__all__ = [
    "seed_catalog",
    "seed_inventory_and_batches",
    "seed_pos_and_reconciliation",
    "seed_purchasing_and_payables",
    "seed_finance",
    "seed_staff_and_stocktake",
    "seed_advisor",
]
