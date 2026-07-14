"""
SaaS seed script: create default plans, a demo tenant with subscription,
and a platform admin account for web management backend.

Run: python -m scripts.seed_saas

This script is idempotent — re-running skips existing records.
Plans are also seeded by the Alembic migration (h9c0d1e2f3a4), but this
script can be used standalone on databases where migrations were stamped
without executing the data-migration INSERTs.
"""

import asyncio
import hashlib
import os
import sys
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path


sys.path.insert(0, str(Path(__file__).parent.parent))

from sqlalchemy import select

from app.database import async_session, init_db
from app.models.saas import (
    Plan,
    PlatformAdmin,
    Subscription,
    Tenant,
    UsageRecord,
)


# ── 固定 UUID（幂等：重复运行跳过）──
PLAN_FREE_ID = uuid.UUID("00000000-0000-0000-0000-000000000001")
PLAN_PRO_ID = uuid.UUID("00000000-0000-0000-0000-000000000002")
PLAN_ENTERPRISE_ID = uuid.UUID("00000000-0000-0000-0000-000000000003")

DEMO_TENANT_ID = uuid.UUID("aaa00000-0000-0000-0000-000000000001")
DEMO_SUBSCRIPTION_ID = uuid.UUID("aaa00000-0000-0000-0000-000000000002")

# 平台管理员
ADMIN_EMAIL = os.getenv("PLATFORM_ADMIN_EMAIL", "admin@qiantan.com")
ADMIN_PASSWORD = os.getenv("PLATFORM_ADMIN_PASSWORD", "Admin123!")


def _bcrypt_hash(password: str) -> str:
    """bcrypt 哈希密码。

    生产环境应使用 passlib/bcrypt，此处用 sha256_salt 作为 dev 兜底
    （要求 requirements 中有 bcrypt 时优先用 bcrypt）。
    """
    try:
        import bcrypt  # type: ignore

        return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()
    except ImportError:
        # dev fallback: sha256（非生产级，仅本地开发）
        salt = "qiantan_dev_salt_"
        return hashlib.sha256((salt + password).encode()).hexdigest()


async def seed_plans(db) -> None:
    """创建三档默认套餐（幂等）。"""
    plans = [
        Plan(
            id=PLAN_FREE_ID,
            code="free",
            name="免费版",
            price_monthly=0,
            price_yearly=0,
            max_merchants=1,
            max_api_calls_monthly=1000,
            max_storage_mb=100,
            features={"ai_advisor": False, "vision": False, "edge_device": False},
            is_public=True,
            is_active=True,
            sort_order=0,
        ),
        Plan(
            id=PLAN_PRO_ID,
            code="pro",
            name="专业版",
            price_monthly=99,
            price_yearly=999,
            max_merchants=10,
            max_api_calls_monthly=50000,
            max_storage_mb=5000,
            features={"ai_advisor": True, "vision": True, "edge_device": True},
            is_public=True,
            is_active=True,
            sort_order=1,
        ),
        Plan(
            id=PLAN_ENTERPRISE_ID,
            code="enterprise",
            name="企业版",
            price_monthly=299,
            price_yearly=2999,
            max_merchants=100,
            max_api_calls_monthly=500000,
            max_storage_mb=50000,
            features={
                "ai_advisor": True,
                "vision": True,
                "edge_device": True,
                "white_label": True,
            },
            is_public=True,
            is_active=True,
            sort_order=2,
        ),
    ]
    for plan in plans:
        existing = await db.get(Plan, plan.id)
        if existing is None:
            db.add(plan)
            print(f"  [+] Plan: {plan.code} ({plan.name})")
        else:
            print(f"  [=] Plan: {plan.code} already exists, skip")


async def seed_demo_tenant(db) -> None:
    """创建演示租户 + 订阅（幂等）。"""
    existing = await db.get(Tenant, DEMO_TENANT_ID)
    if existing is not None:
        print("  [=] Demo tenant already exists, skip")
        return

    now = datetime.now(UTC)
    tenant = Tenant(
        id=DEMO_TENANT_ID,
        name="演示租户",
        slug="demo",
        plan_id=PLAN_PRO_ID,
        status="active",
        contact_email="demo@qiantan.com",
        trial_ends_at=now + timedelta(days=14),
    )
    db.add(tenant)
    print(f"  [+] Tenant: {tenant.name} (slug={tenant.slug})")

    subscription = Subscription(
        id=DEMO_SUBSCRIPTION_ID,
        tenant_id=DEMO_TENANT_ID,
        plan_id=PLAN_PRO_ID,
        billing_cycle="monthly",
        status="active",
        current_period_start=now,
        current_period_end=now + timedelta(days=30),
        auto_renew=True,
    )
    db.add(subscription)
    print("  [+] Subscription: pro (monthly, active)")

    # 初始用量记录
    today_str = now.strftime("%Y-%m-%d")
    for metric, value in [("api_calls", 0), ("storage_mb", 0), ("merchant_count", 1)]:
        db.add(UsageRecord(
            tenant_id=DEMO_TENANT_ID,
            metric=metric,
            recorded_date=today_str,
            value=value,
        ))
    print("  [+] Usage records: api_calls=0, storage_mb=0, merchant_count=1")


async def seed_platform_admin(db) -> None:
    """创建平台管理员账号（幂等）。"""
    result = await db.execute(
        select(PlatformAdmin).where(PlatformAdmin.email == ADMIN_EMAIL)
    )
    if result.scalar_one_or_none() is not None:
        print(f"  [=] Platform admin ({ADMIN_EMAIL}) already exists, skip")
        return

    admin = PlatformAdmin(
        email=ADMIN_EMAIL,
        password_hash=_bcrypt_hash(ADMIN_PASSWORD),
        name="超级管理员",
        role="super_admin",
        is_active=True,
    )
    db.add(admin)
    print(f"  [+] Platform admin: {admin.email} (role={admin.role})")
    print("      [!] 密码为环境变量默认值，生产务必修改 PLATFORM_ADMIN_PASSWORD")


async def main():
    print("=" * 60)
    print("千摊智脑 SaaS 种子数据")
    print("=" * 60)

    await init_db()

    async with async_session() as db:
        print("\n[1/3] 套餐 (Plans)")
        await seed_plans(db)

        print("\n[2/3] 演示租户 (Demo Tenant)")
        await seed_demo_tenant(db)

        print("\n[3/3] 平台管理员 (Platform Admin)")
        await seed_platform_admin(db)

        await db.commit()

    print("\n" + "=" * 60)
    print("[OK] SaaS seed complete!")
    print("     Plans: free / pro / enterprise")
    print("     Demo tenant slug: demo")
    print(f"     Admin login: {ADMIN_EMAIL}")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
