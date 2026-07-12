"""SQLAlchemy database engine and session management.

Supports both PostgreSQL (production) and SQLite (development).

Schema bootstrap 策略（drift-free，统一走 Alembic）：
  - `init_db()` 在应用启动时调用，以 Alembic 为唯一建表权威：
    自动 `alembic upgrade head`（全量基线 5242218be814 + 未来增量迁移），
    从空库即可建出与 ORM 模型完全一致的 schema。
  - dev/test 若 Alembic 不可用或失败：debug=True 时回退到 create_all 保持本地
    开发便利；debug=False（生产）时 Alembic 失败必须 fail-fast，绝不掩盖。
"""

import asyncio
import logging
from collections.abc import AsyncGenerator
from pathlib import Path

from alembic.config import Config
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from app.config import settings


_engine_kwargs: dict[str, object] = {"echo": settings.debug}
if "sqlite" in settings.database_url:
    _engine_kwargs["connect_args"] = {"check_same_thread": False}

engine = create_async_engine(settings.database_url, **_engine_kwargs)

async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

logger = logging.getLogger(__name__)


class Base(DeclarativeBase):
    pass


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async with async_session() as session:
        try:
            yield session
        finally:
            await session.close()


async def init_db():
    """初始化数据库：以 Alembic 为唯一建表权威。

    生产/部署（run_migrations_on_startup=True）：自动 `alembic upgrade head`
    （全量基线 5242218be814 + 未来增量），从空库即可建出与 ORM 模型一致的完整 schema。

    存量库兼容：若库已由旧 create_all 建好全量表但无 alembic_version，基线迁移会因
    「表已存在」失败 —— 此时先 `stamp head` 基线化（使后续 upgrade 变 no-op），
    让 Alembic 真正托管该库，未来 005+ 增量也能正常应用。

    dev/test：Alembic 仍失败（非「表已存在」类）时，debug=True 回退 create_all 保便利；
    debug=False（生产）则 fail-fast，绝不掩盖迁移错误。
    """
    if settings.run_migrations_on_startup:
        try:
            # Alembic 内部用 asyncio.run，须丢到独立线程避免在 running loop 中调用
            await asyncio.to_thread(_run_alembic_upgrade, engine)
            return
        except Exception as e:  # noqa: BLE001
            # 存量库（有表无 alembic_version）：基线迁移报「表已存在」→ 先 stamp 再重试
            if settings.debug and _is_table_exists_error(e):
                try:
                    await asyncio.to_thread(_stamp_head, engine)
                    await asyncio.to_thread(_run_alembic_upgrade, engine)
                    return
                except Exception as e2:  # noqa: BLE001
                    logger.warning("Alembic 基线后仍失败，回退 create_all: %s", e2)
            elif settings.debug:
                logger.warning("Alembic 迁移失败，回退 create_all: %s", e)
            else:
                raise
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


def _alembic_config(target_engine) -> Config:
    """构造 Alembic Config：注入运行时真实 DSN 与 app 的 engine。"""
    backend_dir = Path(__file__).resolve().parent.parent
    cfg = Config(str(backend_dir / "alembic.ini"))
    cfg.set_main_option("script_location", str(backend_dir / "migrations"))
    # 防御：alembic.ini 的 sqlalchemy.url 是 stale PG 占位符，用运行时真实 DSN 覆盖
    cfg.set_main_option("sqlalchemy.url", settings.database_url)
    # 注入 engine，使 Alembic 建表到与 app 同一库（兼容 in-memory / 文件 SQLite）
    cfg.attributes["engine"] = target_engine
    return cfg


def _run_alembic_upgrade(target_engine) -> None:
    """复用 app 的 engine 跑 Alembic 到 head（建表到同一数据库）。"""
    from alembic import command

    command.upgrade(_alembic_config(target_engine), "head")


def _stamp_head(target_engine) -> None:
    """将库基线到 head（写入 alembic_version），不执行任何迁移。"""
    from alembic import command

    command.stamp(_alembic_config(target_engine), "head")


def _is_table_exists_error(e: Exception) -> bool:
    """判断是否因「表已存在」导致迁移失败（存量库未托管 Alembic 的典型场景）。"""
    msg = " ".join(str(a) for a in getattr(e, "args", [e])).lower()
    return "already exists" in msg or "relation" in msg and "exists" in msg
