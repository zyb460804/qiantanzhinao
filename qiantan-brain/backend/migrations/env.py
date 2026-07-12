"""Alembic migration environment — async PostgreSQL."""

import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy import pool
from sqlalchemy.ext.asyncio import create_async_engine

from app.database import Base
from app.config import settings

# Import all models so Base.metadata includes them.
# 用通配导入自动与 app/models/__init__.py 的 __all__ 保持同步，
# 避免新增模型后 Alembic 漏迁移（历史上 stocktake/purchase/audit 曾遗漏，
# 导致 autogenerate 生成的迁移里缺少这几张表）。
from app.models import *  # noqa: F401,F403

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode (generate SQL without connecting)."""
    url = settings.database_url
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection):
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online(engine_override=None) -> None:
    """Run migrations in 'online' mode.

    engine_override 由 app 启动时注入（复用同一 engine，兼容 in-memory
    SQLite 测试与本地文件库）；为 None 时按 settings.database_url 新建
    独立 engine（CLI `alembic upgrade head` 走此分支）。
    """
    if engine_override is not None:
        connectable = engine_override
        owns_engine = False
    else:
        connectable = create_async_engine(
            settings.database_url,
            poolclass=pool.NullPool,
        )
        owns_engine = True
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    if owns_engine:
        await connectable.dispose()


# 允许外部（app.database.init_db）注入 engine，使 Alembic 建表到与 app 同一数据库
_engine_override = context.config.attributes.get("engine")
if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online(_engine_override))
