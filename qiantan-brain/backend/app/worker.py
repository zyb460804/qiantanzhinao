"""后台任务 Worker — APScheduler 定时任务调度。

启动方式: python -m app.worker

职责:
  - 订阅到期提醒与自动过期
  - 账单周期生成
  - 配额月度重置
  - 租户试用到期自动停服
  - 过期 Token 清理
  - 审计日志归档（TODO）

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
from app.database import async_session
from app.models.auth import AuthRevokedToken
from app.models.saas import (
    Invoice,
    Plan,
    Subscription,
    Tenant,
)


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
    """每天检查需要生成账单的订阅。"""
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

            # 检查是否已有该周期的账单
            existing = await db.execute(
                select(Invoice).where(
                    Invoice.subscription_id == sub.id,
                    Invoice.period_start == sub.current_period_end,
                )
            )
            if existing.scalar_one_or_none():
                continue

            amount = plan.price_yearly if sub.billing_cycle == "yearly" else plan.price_monthly
            inv = Invoice(
                tenant_id=sub.tenant_id,
                subscription_id=sub.id,
                invoice_no=_next_invoice_no(now),
                amount=amount,
                currency="CNY",
                status="draft",
                period_start=sub.current_period_end,
                period_end=sub.current_period_end + timedelta(days=30),
                due_date=sub.current_period_end + timedelta(days=7),
                line_items=[
                    {
                        "name": f"{plan.name} - {sub.billing_cycle}",
                        "amount": str(amount),
                    }
                ],
            )
            db.add(inv)
            logger.info(
                "generated invoice=%s for tenant=%s amount=%s",
                inv.invoice_no,
                sub.tenant_id,
                amount,
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


# ── 辅助 ──


def _next_invoice_no(now: datetime | None = None) -> str:
    """生成账单编号 INV-YYYYMM-XXXX（简单递增，生产改用 DB sequence）。"""
    now = now or datetime.now(UTC)
    ts = int(now.timestamp() * 1000)
    return f"INV-{now.strftime('%Y%m')}-{ts % 10000:04d}"


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
