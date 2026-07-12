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
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine


# Add backend to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from app.core.security import get_current_merchant  # noqa: E402
from app.database import Base, get_db  # noqa: E402
from app.main import app  # noqa: E402
from app.models.merchant import Merchant  # noqa: E402
from app.models.product import ProductCategory  # noqa: E402


# Shared constants — tests can import these
TEST_MERCHANT_ID = "00000000-0000-0000-0000-000000000001"
TEST_PRODUCT_ID = 1


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
    """
    raw = request.headers.get("X-Test-Merchant-Id") or TEST_MERCHANT_ID
    mid = uuid.UUID(raw)
    merchant = await db.get(Merchant, mid)
    if merchant is None:
        merchant = Merchant(id=mid, name="测试商户", business_type="蔬菜")
        db.add(merchant)
        await db.commit()
        await db.refresh(merchant)
    return merchant


@pytest_asyncio.fixture(scope="function")
async def client(db_session):
    """Async HTTP client with DB dependency overridden to test database."""

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
