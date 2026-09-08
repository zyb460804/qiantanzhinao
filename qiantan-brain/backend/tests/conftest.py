"""
Pytest fixtures for router integration tests.

Uses in-memory SQLite + dependency override to test the full
FastAPI request → DB → response pipeline without touching real data.
"""

import asyncio
import sys
import uuid
from pathlib import Path

import pytest
import pytest_asyncio
from fastapi import Depends, Request  # noqa: E402
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select  # noqa: E402
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine


# Add backend to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from app.core.security import get_current_merchant  # noqa: E402
from app.database import Base, get_db  # noqa: E402
from app.main import app  # noqa: E402
from app.models.merchant import Merchant  # noqa: E402
from app.models.product import ProductCategory  # noqa: E402
from app.models.saas import Tenant  # noqa: E402


# Shared constants — tests can import these
TEST_MERCHANT_ID = "00000000-0000-0000-0000-000000000001"
TEST_PRODUCT_ID = 1
DEFAULT_TENANT_ID = "00000000-0000-0000-0000-0000000000aa"
DEFAULT_TENANT_SLUG = "default"


@pytest.fixture(scope="session")
def event_loop():
    """Single event loop for the whole test session."""
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest_asyncio.fixture(scope="function")
async def db_session():
    """Fresh in-memory SQLite database for each test (isolation)."""
    from sqlalchemy.pool import StaticPool

    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    # Create all tables
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    # Seed minimal test data
    async with session_factory() as session:
        # 注意：默认租户不在这里预置，避免污染 admin 租户列表/导出统计。
        # 需要绑定默认租户的 merchant 端测试由 _override_get_current_merchant
        # 按需创建并绑定；auth_client 走真实登录时会由 auth.wechat_login 创建。
        merchant = Merchant(
            id=uuid.UUID(TEST_MERCHANT_ID),
            name="测试摊位",
            business_type="蔬菜",
        )
        session.add(merchant)

        # Seed a few product categories matching voice parser test cases
        products = [
            ProductCategory(
                id=1, name="白菜", unit="斤", shelf_life_hours=72, category_group="叶菜类"
            ),
            ProductCategory(
                id=2, name="土豆", unit="斤", shelf_life_hours=168, category_group="根茎类"
            ),
            ProductCategory(
                id=3, name="豆腐", unit="斤", shelf_life_hours=24, category_group="豆制品"
            ),
            ProductCategory(
                id=4, name="猪肉", unit="斤", shelf_life_hours=48, category_group="肉类"
            ),
        ]
        session.add_all(products)
        await session.commit()

    yield session_factory

    # Teardown
    await engine.dispose()


async def _override_get_current_merchant(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> Merchant:
    """测试依赖覆盖：默认返回 TEST 商户；带 X-Test-Merchant-Id 头时返回对应商户。

    用于多租户隔离测试。未播种的商户自动创建，使隔离测试无需手动建表。

    测试角色模拟：带 X-Test-Token-Role 头时，把对应角色注入 merchant._token_role，
    模拟生产链路中 get_current_merchant 从 JWT claim 写入的 _token_role。
    用于 market_admin 等依赖 _token_role 做权限判断的路由测试。
    """
    raw = request.headers.get("X-Test-Merchant-Id") or TEST_MERCHANT_ID
    mid = uuid.UUID(raw)
    merchant = await db.get(Merchant, mid)
    if merchant is None:
        merchant = Merchant(id=mid, name="测试商户", business_type="蔬菜")
        db.add(merchant)
        await db.commit()
        await db.refresh(merchant)

    # 默认测试商户按需绑定默认租户：让 client fixture 在严格模式下仍能通过
    # 带租户门禁的常规接口；其它 X-Test-Merchant-Id 隔离商户保持无租户，
    # 供未绑定租户 403 测试使用。
    if mid == uuid.UUID(TEST_MERCHANT_ID) and merchant.tenant_id is None:
        result = await db.execute(select(Tenant).where(Tenant.slug == DEFAULT_TENANT_SLUG))
        tenant = result.scalar_one_or_none()
        if tenant is None:
            tenant = Tenant(
                id=uuid.UUID(DEFAULT_TENANT_ID),
                name="默认租户",
                slug=DEFAULT_TENANT_SLUG,
                status="active",
            )
            db.add(tenant)
            await db.flush()
        merchant.tenant_id = tenant.id
        await db.commit()
        await db.refresh(merchant)

    # 测试角色模拟：注入 _token_role，与生产 get_current_merchant 行为对齐
    test_role = request.headers.get("X-Test-Token-Role")
    if test_role:
        merchant._token_role = test_role
    else:
        # 与生产默认行为一致：无 token 时 _token_role 默认 "owner"
        merchant._token_role = "owner"

    # 与生产 get_current_merchant 对齐：把商户租户写入请求上下文。
    # 未绑定租户的商户（隔离测试）会得到 None，供严格模式 403 验证。
    from app.core.tenant_context import set_tenant_id

    set_tenant_id(merchant.tenant_id)
    return merchant


@pytest_asyncio.fixture(scope="function")
async def client(db_session):
    """Async HTTP client with DB dependency overridden to test database."""

    # 重置限流后端，防止跨测试状态污染（staff_login 等限流路径）
    from app.core import rate_limiter as _rl

    _rl._backend = None

    async def override_get_db():
        async with db_session() as session:
            try:
                yield session
            finally:
                await session.close()

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_current_merchant] = _override_get_current_merchant

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac

    app.dependency_overrides.clear()


@pytest_asyncio.fixture(scope="function")
async def auth_client(db_session):
    """真实 JWT 校验客户端：不覆盖 get_current_merchant，走生产鉴权路径。

    用于鉴权路由本身的测试（登录/刷新/登出/越权隔离）。
    """

    # 重置限流后端，防止跨测试状态污染
    from app.core import rate_limiter as _rl

    _rl._backend = None

    async def override_get_db():
        async with db_session() as session:
            try:
                yield session
            finally:
                await session.close()

    app.dependency_overrides[get_db] = override_get_db

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac

    app.dependency_overrides.clear()
