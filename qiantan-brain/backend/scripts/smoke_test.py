"""端到端业务闭环冒烟测试 — 答辩前一键验证演示链路不断裂。

业务闭环：
  语音记账 → 进货批次 → POS 销售 → FIFO 消耗 → 临期预警
  → AI 建议 → 采纳生成采购 → 验收入库 → 报告体现 → 数字孪生

每个环节一个检查函数，输出 PASS/FAIL + 详情；任一环节断裂立即定位。
无需启动服务器，直接读库验证数据自洽性。

运行：
    cd qiantan-brain/backend
    python -m scripts.smoke_test
    DATABASE_URL=... python -m scripts.smoke_test   # 指定库
"""

from __future__ import annotations

import asyncio
import sys
from dataclasses import dataclass
from pathlib import Path


sys.path.insert(0, str(Path(__file__).parent.parent))

# Windows 中文控制台默认 GBK 代码页无法输出 ✓/🎉 等字符（UnicodeEncodeError），
# 统一重配 stdout 为 UTF-8，保证 `python -m scripts.smoke_test` 可直接运行。
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from sqlalchemy import func, select

from app.database import async_session, init_db
from app.models.accounts import CustomerCreditProfile, CustomerReceivable, SupplierPayable
from app.models.ai_action import AIAction
from app.models.batch import BatchLifecycle
from app.models.catalog import Supplier
from app.models.expense import Expense, Invoice
from app.models.inventory import CurrentInventory, InventoryRecord
from app.models.merchant import Merchant
from app.models.pos import DailySettlement, Payment, Reconciliation, SaleOrder, SaleOrderItem
from app.models.product import ProductCategory
from app.models.purchase import PurchaseItem, PurchaseList
from app.models.recommendation import Recommendation
from app.models.staff import StaffMember
from app.models.stocktake import StocktakeItem, StocktakeSession
from app.models.voice import VoiceLog
from scripts.seed_data.common import ALL_MERCHANT_IDS


@dataclass
class CheckResult:
    name: str
    passed: bool
    detail: str


async def check(db, name: str, query, min_count: int = 1, extra: str = "") -> CheckResult:
    """通用计数检查。"""
    count = int((await db.execute(query)).scalar_one())
    ok = count >= min_count
    return CheckResult(
        name,
        ok,
        f"{'✓' if ok else '✗'} {name}: {count} 条{'（需≥{}）'.format(min_count) if not ok else ''} {extra}".strip(),
    )


async def run_checks() -> list[CheckResult]:
    results: list[CheckResult] = []

    async with async_session() as db:
        # ── 环节 1：语音记账 ──
        results.append(
            await check(
                db,
                "① 语音记账流水",
                select(func.count()).select_from(VoiceLog).where(VoiceLog.merchant_id.in_(ALL_MERCHANT_IDS)),
                min_count=10,
            )
        )
        # 语音已解析出事件
        parsed = int(
            (
                await db.execute(
                    select(func.count())
                    .select_from(VoiceLog)
                    .where(VoiceLog.parsed_event.isnot(None))
                )
            ).scalar_one()
        )
        results.append(CheckResult("① 语音解析事件", parsed > 0, f"{'✓' if parsed > 0 else '✗'} 语音解析事件: {parsed} 条"))

        # ── 环节 2：进货批次（食安追溯基座）──
        results.append(
            await check(
                db,
                "② 进货批次追溯",
                select(func.count()).select_from(BatchLifecycle).where(BatchLifecycle.merchant_id.in_(ALL_MERCHANT_IDS)),
                min_count=10,
            )
        )
        # 批次带二维码（扫码追溯）
        qr = int(
            (
                await db.execute(
                    select(func.count())
                    .select_from(BatchLifecycle)
                    .where(BatchLifecycle.qr_data.isnot(None))
                )
            ).scalar_one()
        )
        results.append(CheckResult("② 批次二维码", qr > 0, f"{'✓' if qr > 0 else '✗'} 批次二维码: {qr} 条"))

        # ── 环节 3：POS 销售 ──
        results.append(
            await check(
                db,
                "③ POS 销售订单",
                select(func.count()).select_from(SaleOrder).where(SaleOrder.merchant_id.in_(ALL_MERCHANT_IDS)),
                min_count=100,
            )
        )
        results.append(
            await check(
                db,
                "③ 订单行项目",
                select(func.count()).select_from(SaleOrderItem),
                min_count=100,
            )
        )

        # ── 环节 4：FIFO 消耗（批次有消耗痕迹）──
        consumed = int(
            (
                await db.execute(
                    select(func.count())
                    .select_from(BatchLifecycle)
                    .where(BatchLifecycle.remaining_qty < BatchLifecycle.purchase_qty)
                )
            ).scalar_one()
        )
        results.append(CheckResult("④ FIFO 批次消耗", consumed > 0, f"{'✓' if consumed > 0 else '✗'} 已消耗批次: {consumed} 条"))

        # ── 环节 5：临期预警 ──
        near = int(
            (
                await db.execute(
                    select(func.count())
                    .select_from(BatchLifecycle)
                    .where(BatchLifecycle.status == "near_expiry")
                )
            ).scalar_one()
        )
        promo = int(
            (
                await db.execute(
                    select(func.count())
                    .select_from(BatchLifecycle)
                    .where(BatchLifecycle.promotion_price.isnot(None))
                )
            ).scalar_one()
        )
        results.append(CheckResult("⑤ 临期预警批次", near > 0, f"{'✓' if near > 0 else '✗'} 临期批次: {near}，临期促销: {promo}"))

        # ── 环节 5b：食安锁定召回（肉摊故事）──
        locked = int(
            (
                await db.execute(
                    select(func.count())
                    .select_from(BatchLifecycle)
                    .where(BatchLifecycle.status == "locked")
                )
            ).scalar_one()
        )
        results.append(CheckResult("⑤ 食安锁定召回", locked > 0, f"{'✓' if locked > 0 else '✗'} 锁定批次: {locked}"))

        # ── 环节 6：AI 建议 ──
        results.append(
            await check(
                db,
                "⑥ AI 建议",
                select(func.count()).select_from(Recommendation).where(Recommendation.merchant_id.in_(ALL_MERCHANT_IDS)),
                min_count=10,
            )
        )
        adopted = int(
            (
                await db.execute(
                    select(func.count()).select_from(Recommendation).where(Recommendation.was_adopted.is_(True))
                )
            ).scalar_one()
        )
        results.append(CheckResult("⑥ AI 建议采纳", adopted > 0, f"{'✓' if adopted > 0 else '✗'} 已采纳建议: {adopted} 条"))

        # ── 环节 7：建议→行动（采纳生成可执行动作）──
        results.append(
            await check(
                db,
                "⑦ AI 行动追踪",
                select(func.count()).select_from(AIAction).where(AIAction.merchant_id.in_(ALL_MERCHANT_IDS)),
                min_count=10,
            )
        )
        # 行动类型覆盖清货/采购/改价
        types = [
            r[0]
            for r in (
                await db.execute(select(AIAction.action_type).distinct())
            ).all()
        ]
        results.append(
            CheckResult(
                "⑦ 行动类型覆盖",
                {"clearance", "purchase", "price"}.issubset(set(types)),
                f"{'✓' if {'clearance','purchase','price'}.issubset(set(types)) else '✗'} 行动类型: {sorted(types)}",
            )
        )

        # ── 环节 8：采购验收闭环（状态机完整）──
        pl_states = {
            r[0]
            for r in (
                await db.execute(select(PurchaseList.status).distinct())
            ).all()
        }
        expected_states = {"draft", "confirmed", "partial_arrival", "stored", "completed", "returned"}
        results.append(
            CheckResult(
                "⑧ 采购验收状态机",
                expected_states.issubset(pl_states),
                f"{'✓' if expected_states.issubset(pl_states) else '✗'} 采购状态: {sorted(pl_states)}",
            )
        )
        # 验收明细（到货/缺斤/破损）
        accepted = int(
            (
                await db.execute(
                    select(func.count())
                    .select_from(PurchaseItem)
                    .where(PurchaseItem.accepted_qty.isnot(None))
                )
            ).scalar_one()
        )
        results.append(CheckResult("⑧ 到货验收明细", accepted > 0, f"{'✓' if accepted > 0 else '✗'} 验收明细: {accepted} 条"))

        # ── 环节 9：报告（日结 + 对账）──
        results.append(
            await check(
                db,
                "⑨ 日结汇总",
                select(func.count()).select_from(DailySettlement),
                min_count=30,
            )
        )
        recon_exception = int(
            (
                await db.execute(
                    select(func.count())
                    .select_from(Reconciliation).where(Reconciliation.status == "exception")
                )
            ).scalar_one()
        )
        results.append(CheckResult("⑨ 对账差异", recon_exception > 0, f"{'✓' if recon_exception > 0 else '✗'} 异常对账: {recon_exception} 条"))

        # ── 环节 10：数字孪生（当前库存视图）──
        results.append(
            await check(
                db,
                "⑩ 当前库存（数字孪生）",
                select(func.count()).select_from(CurrentInventory),
                min_count=10,
            )
        )

        # ── 附加：POS 状态覆盖（挂单/退款/赊账/组合支付）──
        order_statuses = {
            r[0]
            for r in (
                await db.execute(select(SaleOrder.status).distinct())
            ).all()
        }
        needed = {"paid", "held", "credit", "partial_refund"}
        results.append(
            CheckResult(
                "✦ POS 状态全覆盖",
                needed.issubset(order_statuses),
                f"{'✓' if needed.issubset(order_statuses) else '✗'} POS 状态: {sorted(order_statuses)}",
            )
        )
        # 组合支付
        combined = int(
            (
                await db.execute(
                    select(func.count())
                    .select_from(
                        select(Payment.order_id)
                        .where(Payment.order_id.isnot(None))
                        .group_by(Payment.order_id)
                        .having(func.count(func.distinct(Payment.method)) >= 2)
                        .subquery()
                    )
                )
            ).scalar_one()
        )
        results.append(CheckResult("✦ 组合支付", combined > 0, f"{'✓' if combined > 0 else '✗'} 组合支付订单: {combined} 笔"))

        # ── 附加：供应商评分 + 黑名单 ──
        scored = int(
            (
                await db.execute(
                    select(func.count())
                    .select_from(Supplier)
                    .where(Supplier.composite_score.isnot(None))
                )
            ).scalar_one()
        )
        blacklisted = int(
            (
                await db.execute(select(func.count()).select_from(Supplier).where(Supplier.is_blacklisted.is_(True)))
            ).scalar_one()
        )
        results.append(CheckResult("✦ 供应商评分/黑名单", scored > 0 and blacklisted > 0, f"{'✓' if scored > 0 and blacklisted > 0 else '✗'} 已评分: {scored}, 黑名单: {blacklisted}"))

        # ── 附加：往来账（应付 + 应收 + 信用）──
        payables = int((await db.execute(select(func.count()).select_from(SupplierPayable))).scalar_one())
        receivables = int((await db.execute(select(func.count()).select_from(CustomerReceivable))).scalar_one())
        profiles = int((await db.execute(select(func.count()).select_from(CustomerCreditProfile))).scalar_one())
        results.append(
            CheckResult("✦ 往来账", payables > 0 and receivables > 0, f"{'✓' if payables > 0 and receivables > 0 else '✗'} 应付: {payables}, 应收: {receivables}, 信用档案: {profiles}")
        )

        # ── 附加：多角色员工 ──
        roles = {
            r[0]
            for r in (
                await db.execute(select(StaffMember.role).distinct())
            ).all()
        }
        results.append(
            CheckResult(
                "✦ 多角色员工",
                {"owner", "manager", "cashier"}.issubset(roles),
                f"{'✓' if {'owner','manager','cashier'}.issubset(roles) else '✗'} 角色: {sorted(roles)}",
            )
        )

        # ── 附加：盘点闭环 ──
        sessions = int((await db.execute(select(func.count()).select_from(StocktakeSession))).scalar_one())
        variances = int(
            (
                await db.execute(
                    select(func.count())
                    .select_from(StocktakeItem)
                    .where(StocktakeItem.variance != 0)
                )
            ).scalar_one()
        )
        results.append(CheckResult("✦ 盘点盘亏盘盈", sessions > 0 and variances > 0, f"{'✓' if sessions > 0 and variances > 0 else '✗'} 盘点: {sessions} 次, 差异行: {variances}"))

        # ── 附加：财务费用 + 发票 ──
        expenses = int((await db.execute(select(func.count()).select_from(Expense))).scalar_one())
        invoices = int((await db.execute(select(func.count()).select_from(Invoice))).scalar_one())
        results.append(CheckResult("✦ 费用/发票", expenses > 0 and invoices > 0, f"{'✓' if expenses > 0 and invoices > 0 else '✗'} 费用: {expenses}, 发票: {invoices}"))

    return results


async def main() -> int:
    await init_db()
    print("=" * 64)
    print("千摊智脑 — 端到端业务闭环冒烟测试")
    print("=" * 64)

    results = await run_checks()
    passed = sum(1 for r in results if r.passed)
    failed = len(results) - passed

    for r in results:
        print(f"  {r.detail}")

    print("\n" + "-" * 64)
    status = "✅ 全部通过" if failed == 0 else f"❌ {failed} 项未通过"
    print(f"  结果: {passed}/{len(results)} 通过 — {status}")
    print("-" * 64)

    if failed == 0:
        print("  🎉 业务闭环完整，演示链路无断裂，可放心答辩。")
    else:
        print('  ⚠️  以上 ✗ 项即为演示会「白屏/断裂」的风险点，请补 seed 后重跑。')
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
