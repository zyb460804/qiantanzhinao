"""后台任务 Worker — APScheduler 定时任务调度。

启动方式: python -m app.worker

职责:
  - 订阅到期提醒与自动过期
  - 账单周期生成
  - 配额月度重置
  - 租户试用到期自动停服
  - 过期 Token 清理
  - 审计日志归档
  - 离线同步死信队列定时重放

架构:
  - AsyncIOScheduler 在独立线程运行
  - 每个 Job 使用独立 DB session
  - Redis 分布式锁防止多 Worker 重复执行（可选）
"""

from __future__ import annotations

import asyncio
import logging
import signal
from datetime import UTC, datetime, timedelta

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger
from sqlalchemy import func, select, update

from app.config import settings
from app.core.invoicing import get_overlapping_invoice, insert_invoice
from app.core.timezone import utc_now
from app.database import async_session
from app.models.auth import AuthRevokedToken
from app.models.dead_letter import DeadLetterEvent
from app.models.saas import (
    Plan,
    Subscription,
    Tenant,
)
from app.services.audit_archiver import archive_old_audit_logs
from app.services.offline_sync import replay_dead_letter


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [worker] %(levelname)s %(message)s",
)
logger = logging.getLogger("qiantan.worker")

scheduler = AsyncIOScheduler()


# ═══════════════════════════════════════════════════════════════
# 定时任务定义
# ═══════════════════════════════════════════════════════════════


async def check_trial_expiry():
    """每小时检查试用到期租户 → 切换为 active 或 expired。"""
    async with async_session() as db:
        now = datetime.now(UTC)
        result = await db.execute(
            select(Tenant).where(
                Tenant.status == "trial",
                Tenant.trial_ends_at.isnot(None),
                Tenant.trial_ends_at <= now + timedelta(hours=1),
            )
        )
        expiring = result.scalars().all()

        for tenant in expiring:
            if tenant.trial_ends_at <= now:
                # 试用到期 → 检查是否有有效订阅
                sub_result = await db.execute(
                    select(Subscription).where(
                        Subscription.tenant_id == tenant.id,
                        Subscription.status.in_(("active", "trialing")),
                    )
                )
                if sub_result.scalar_one_or_none():
                    tenant.status = "active"
                    logger.info("tenant=%s 试用到期，有有效订阅，切换为 active", tenant.id)
                else:
                    tenant.status = "expired"
                    logger.info("tenant=%s 试用到期，无有效订阅，切换为 expired", tenant.id)
            else:
                logger.info("tenant=%s 试用即将在 %s 到期", tenant.id, tenant.trial_ends_at)

        await db.commit()


async def check_subscription_expiry():
    """每天检查订阅状态，处理过期、逾期。"""
    async with async_session() as db:
        now = datetime.now(UTC)

        # 1) active/trialing 订阅 → period_end 已过 → 标记 past_due
        await db.execute(
            update(Subscription)
            .where(
                Subscription.status.in_(("active", "trialing")),
                Subscription.current_period_end.isnot(None),
                Subscription.current_period_end <= now,
            )
            .values(status="past_due")
        )
        await db.commit()

        # 2) past_due 超过 15 天 → expired
        await db.execute(
            update(Subscription)
            .where(
                Subscription.status == "past_due",
                Subscription.current_period_end.isnot(None),
                Subscription.current_period_end <= now - timedelta(days=15),
            )
            .values(status="expired")
        )
        await db.commit()

        # 3) expired 订阅对应的 tenant → 自动停服
        result = await db.execute(select(Subscription).where(Subscription.status == "expired"))
        expired_subs = result.scalars().all()
        for sub in expired_subs:
            tenant = await db.get(Tenant, sub.tenant_id)
            if tenant and tenant.status == "active":
                tenant.status = "suspended"
                logger.info("tenant=%s 订阅已过期 15 天，自动停服", tenant.id)
        await db.commit()


async def generate_invoices():
    """每天检查需要生成账单的订阅 — 同订阅同周期只出一票。

    幂等保证：插入走方言感知 INSERT ... ON CONFLICT DO NOTHING +
    (subscription_id, period_start) 唯一约束（uq_invoice_subscription_period），
    两个 worker 并发跑同一周期也只出一票；invoice_no 数据库侧 MAX+1 发号，
    撞号竞态自动重试（见 app/core/invoicing.py）。

    续期票语义：票覆盖「下一计费周期」，period_start = current_period_end
    （该周期的起点），period_end 按计费周期长度推进（monthly 30d / yearly
    365d）。与 admin generate-from-subscription 入共用一键，跨入口重试幂等；
    周期起点漂移（activate 续期）形成的重叠期票由 get_overlapping_invoice
    拦截（V1-H1）。
    """
    async with async_session() as db:
        now = datetime.now(UTC)
        # 查找 period_end 在 3 天内的 active 订阅
        result = await db.execute(
            select(Subscription).where(
                Subscription.status == "active",
                Subscription.current_period_end.isnot(None),
                Subscription.current_period_end <= now + timedelta(days=3),
                Subscription.current_period_end >= now,
                Subscription.auto_renew == True,  # noqa: E712
            )
        )
        due_subs = result.scalars().all()

        for sub in due_subs:
            plan = await db.get(Plan, sub.plan_id)
            if not plan:
                continue
            if sub.current_period_end is None:
                continue  # 上方过滤已保证，防御性兜底

            period_start = sub.current_period_end
            # 续期票覆盖下一整个计费周期：monthly 30 天 / yearly 365 天（V1-M2）。
            # 原实现固定 +30 天，年度订阅 12 张续期票的覆盖区间会互相重叠。
            cycle_days = 365 if sub.billing_cycle == "yearly" else 30
            period_end = period_start + timedelta(days=cycle_days)

            # 重叠守卫（V1-H1）：约束只拦周期起点相等的重复。订阅被 activate
            # 续期后 current_period_end 漂移，本周期目标可能与既有票区间重叠
            # （键不等，约束拦不住）——跳过并不出票，宁可少出不可重出。
            overlapping = await get_overlapping_invoice(db, sub.id, period_start, period_end)
            if overlapping is not None and overlapping.period_start != period_start:
                logger.warning(
                    "subscription=%s 目标周期 [%s, %s) 与已有发票 %s (%s) 重叠，跳过出票",
                    sub.id,
                    period_start,
                    period_end,
                    overlapping.invoice_no,
                    overlapping.period_start,
                )
                continue

            amount = plan.price_yearly if sub.billing_cycle == "yearly" else plan.price_monthly
            values = {
                "tenant_id": sub.tenant_id,
                "subscription_id": sub.id,
                "amount": amount,
                "currency": "CNY",
                "status": "draft",
                "period_start": period_start,
                "period_end": period_end,
                "due_date": period_start + timedelta(days=7),
                "line_items": [
                    {
                        "name": f"{plan.name} - {sub.billing_cycle}",
                        "amount": str(amount),
                    }
                ],
            }
            inv, created = await insert_invoice(db, values, now=now)
            if created:
                logger.info(
                    "generated invoice=%s for tenant=%s amount=%s",
                    inv.invoice_no,
                    sub.tenant_id,
                    amount,
                )
            else:
                logger.info(
                    "invoice already exists for tenant=%s period=%s, skipped (idempotent)",
                    sub.tenant_id,
                    period_start,
                )

        await db.commit()


async def reset_monthly_quotas():
    """每月 1 号：月度用量记录归档（当前版本不做物理重置，仅打日志）。"""
    logger.info(
        "monthly quota reset check — current design uses rolling window, no physical reset needed"
    )


async def clean_expired_tokens():
    """每天清理过期的吊销 Token 记录（保留 30 天）。"""
    async with async_session() as db:
        cutoff = datetime.now(UTC) - timedelta(days=30)
        result = await db.execute(
            select(func.count())
            .select_from(AuthRevokedToken)
            .where(
                AuthRevokedToken.expires_at.isnot(None),
                AuthRevokedToken.expires_at < cutoff,
            )
        )
        count = result.scalar() or 0
        if count > 0:
            await db.execute(
                update(AuthRevokedToken)
                .where(
                    AuthRevokedToken.expires_at.isnot(None),
                    AuthRevokedToken.expires_at < cutoff,
                )
                .values(expires_at=None)  # soft delete
            )
            await db.commit()
            logger.info("cleaned %d expired revoked tokens", count)


async def archive_audit_logs():
    """每天归档超过保留期的审计日志（保留天数由 settings.audit_archive_days 控制）。"""
    async with async_session() as db:
        result = await archive_old_audit_logs(db)
        logger.info("audit log archive result: %s", result)


async def process_dead_letter_retries(
    session_factory=async_session,
    batch_size: int = 50,
) -> dict:
    """扫描到期的 pending 死信并重放（每分钟）。

    成功 → resolved；失败 → 计数+1、指数退避 next_retry_at；超上限 → failed
    （状态迁移逻辑统一在 app.services.offline_sync.replay_dead_letter）。
    每条事件独立提交，单条崩溃不影响本批其余事件。session_factory 可注入，
    供测试用内存库验证，不碰生产 DB。
    """
    stats = {"scanned": 0, "resolved": 0, "pending": 0, "failed": 0}
    async with session_factory() as db:
        result = await db.execute(
            select(DeadLetterEvent)
            .where(
                DeadLetterEvent.status == "pending",
                DeadLetterEvent.next_retry_at.isnot(None),
                DeadLetterEvent.next_retry_at <= utc_now(),
            )
            .order_by(DeadLetterEvent.next_retry_at)
            .limit(batch_size)
        )
        events = result.scalars().all()
        stats["scanned"] = len(events)

        for event in events:
            try:
                outcome = await replay_dead_letter(db, event)
                await db.commit()
            except Exception:  # noqa: BLE001 — 单条异常不拖垮整批
                await db.rollback()
                logger.exception("dead-letter %s replay crashed", event.id)
                continue
            stats[outcome["status"]] = stats.get(outcome["status"], 0) + 1
            logger.info(
                "dead-letter %s replay -> %s (%s)",
                event.id,
                outcome["status"],
                outcome.get("message", ""),
            )

    if stats["scanned"]:
        logger.info("dead-letter retry pass: %s", stats)
    return stats


# ── 辅助 ──
# 发号已迁移至 app/core/invoicing.py（数据库侧 MAX+1，进程重启/双副本安全），
# 原 _next_invoice_no 的 ts % 10000 在同秒多票/跨进程下必然撞号，已移除。


# ═══════════════════════════════════════════════════════════════
# 调度器启动
# ═══════════════════════════════════════════════════════════════


def start_scheduler():
    """注册所有定时任务并启动调度器。"""
    # 每小时：试用到期检查
    scheduler.add_job(
        check_trial_expiry,
        IntervalTrigger(hours=1),
        id="check_trial_expiry",
        name="试用到期检查",
        replace_existing=True,
    )

    # 每天 02:00：订阅状态检查和自动停服
    scheduler.add_job(
        check_subscription_expiry,
        CronTrigger(hour=2, minute=0),
        id="check_subscription_expiry",
        name="订阅过期检查",
        replace_existing=True,
    )

    # 每天 03:00：账单生成
    scheduler.add_job(
        generate_invoices,
        CronTrigger(hour=3, minute=0),
        id="generate_invoices",
        name="账单生成",
        replace_existing=True,
    )

    # 每月 1 号 04:00：配额重置
    scheduler.add_job(
        reset_monthly_quotas,
        CronTrigger(day=1, hour=4, minute=0),
        id="reset_monthly_quotas",
        name="月度配额重置",
        replace_existing=True,
    )

    # 每天 05:00：清理过期 Token
    scheduler.add_job(
        clean_expired_tokens,
        CronTrigger(hour=5, minute=0),
        id="clean_expired_tokens",
        name="过期 Token 清理",
        replace_existing=True,
    )

    # 每天 06:00：审计日志归档
    scheduler.add_job(
        archive_audit_logs,
        CronTrigger(hour=6, minute=0),
        id="archive_audit_logs",
        name="审计日志归档",
        replace_existing=True,
    )

    # 每分钟：扫描到期的死信事件并重放
    scheduler.add_job(
        process_dead_letter_retries,
        IntervalTrigger(minutes=1),
        id="process_dead_letter_retries",
        name="死信队列重放",
        replace_existing=True,
    )

    scheduler.start()
    logger.info("worker scheduler started with %d jobs", len(scheduler.get_jobs()))


def stop_scheduler():
    """优雅停止调度器。"""
    scheduler.shutdown(wait=False)
    logger.info("worker scheduler stopped")


# ── 入口 ──

if __name__ == "__main__":
    logger.info("千摊智脑后台 Worker 启动 (env=%s)", settings.app_env)

    # 信号处理
    loop = asyncio.new_event_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, lambda: asyncio.ensure_future(_shutdown(loop)))

    async def _shutdown(event_loop):
        logger.info("收到退出信号...")
        stop_scheduler()
        await asyncio.sleep(1)
        event_loop.stop()

    try:
        start_scheduler()
        loop.run_forever()
    except KeyboardInterrupt:
        stop_scheduler()
    finally:
        loop.close()
