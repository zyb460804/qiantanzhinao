"""迁移与模型 DDL 对齐测试 — create_all（测试库路径）与 alembic upgrade
（生产路径）的漂移回归防线。

背景（2026-08 审计实测）：UsageRecord 模型有
uq_usage_per_tenant_metric_date(tenant_id, metric, recorded_date)，但部分
存量库（create_all 建表后才被 stamp 托管）缺该约束 → quota.record_usage 的
ON CONFLICT 在迁移库上 100% 500。测试库走 create_all 全绿、生产走迁移
全炸 —— 两套建表路径漂移就是测试盲区。

本文件用 alembic CLI 在临时 SQLite 库上真实 upgrade head（不走
create_all），再以 PRAGMA introspection 断言唯一索引存在，并实测
ON CONFLICT upsert。
"""

import os
import sqlite3
import subprocess
import sys
import tempfile
from collections.abc import Iterator
from pathlib import Path

import pytest
from sqlalchemy import create_engine, inspect

import app.models  # noqa: F401  # 触发全模型注册进 Base.metadata
from app.database import Base


BACKEND_DIR = Path(__file__).resolve().parent.parent


def _sqlite_url(path: Path) -> str:
    return f"sqlite+aiosqlite:///{path.as_posix()}"


def _unique_indexes(con: sqlite3.Connection, table: str) -> dict[str, tuple]:
    """表名 → {索引名: (列元组, partial)}，只保留唯一索引。

    PRAGMA index_list 每行: (seq, name, unique, origin, partial)。
    SQLite 表级 UNIQUE 约束渲染为 sqlite_autoindex_*，名字不带约束名，
    因此断言按「列集」而非「名字」做匹配。
    """
    result: dict[str, tuple] = {}
    for row in con.execute(f"PRAGMA index_list({table})"):
        _, name, is_unique, _, partial = row
        if not is_unique:
            continue
        cols = tuple(r[2] for r in con.execute(f"PRAGMA index_info({name})"))
        result[name] = (cols, partial)
    return result


@pytest.fixture(scope="module")
def migrated_db_path() -> Iterator[Path]:
    """子进程跑 `alembic upgrade head` 建出与生产同构的临时库。"""
    fd, raw = tempfile.mkstemp(suffix=".db", prefix="qiantan_mig_align_")
    os.close(fd)
    os.unlink(raw)
    db_path = Path(raw)
    env = {**os.environ, "DATABASE_URL": _sqlite_url(db_path)}
    ini = str(BACKEND_DIR / "alembic.ini")
    proc = subprocess.run(
        [sys.executable, "-m", "alembic", "-c", ini, "upgrade", "head"],
        cwd=BACKEND_DIR,
        env=env,
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, f"alembic upgrade head 失败:\n{proc.stdout}\n{proc.stderr}"
    yield db_path
    db_path.unlink(missing_ok=True)


@pytest.fixture(scope="module")
def create_all_db_path() -> Iterator[Path]:
    """create_all 建出测试路径同构库（模型即真相）。"""
    fd, raw = tempfile.mkstemp(suffix=".db", prefix="qiantan_createall_")
    os.close(fd)
    os.unlink(raw)
    db_path = Path(raw)
    engine = create_engine(f"sqlite:///{db_path.as_posix()}")
    Base.metadata.create_all(engine)
    engine.dispose()
    yield db_path
    db_path.unlink(missing_ok=True)


class TestUsageRecordUnique:
    def test_migrated_db_has_usage_unique(self, migrated_db_path: Path):
        """迁移库：存在覆盖 (tenant_id, metric, recorded_date) 的唯一索引。"""
        con = sqlite3.connect(migrated_db_path)
        try:
            indexes = _unique_indexes(con, "usage_records")
        finally:
            con.close()
        assert any(
            set(cols) == {"tenant_id", "metric", "recorded_date"} for cols, _ in indexes.values()
        ), f"迁移库缺 usage_records 唯一约束, 现有: {indexes}"

    def test_create_all_db_has_usage_unique(self, create_all_db_path: Path):
        """create_all 库（模型路径）同样存在。"""
        engine = create_engine(f"sqlite:///{create_all_db_path.as_posix()}")
        try:
            constraints = inspect(engine).get_unique_constraints("usage_records")
        finally:
            engine.dispose()
        assert any(
            set(uc["column_names"]) == {"tenant_id", "metric", "recorded_date"}
            for uc in constraints
        ), f"模型缺 usage_records 唯一约束, 现有: {constraints}"

    def test_upsert_on_conflict_works_on_migrated_db(self, migrated_db_path: Path):
        """ON CONFLICT DO UPDATE 在迁移库上可执行（/inventory/current、
        /pos/orders 500 的直接复现）。"""
        con = sqlite3.connect(migrated_db_path)
        try:
            con.execute(
                "INSERT INTO usage_records (id, tenant_id, metric, recorded_date, value) "
                "VALUES ('11111111-1111-1111-1111-111111111111', "
                "'22222222-2222-2222-2222-222222222222', 'api_calls', '2026-08-17', 5) "
                "ON CONFLICT (tenant_id, metric, recorded_date) DO UPDATE "
                "SET value = value + excluded.value"
            )
            con.execute(
                "INSERT INTO usage_records (id, tenant_id, metric, recorded_date, value) "
                "VALUES ('33333333-3333-3333-3333-333333333333', "
                "'22222222-2222-2222-2222-222222222222', 'api_calls', '2026-08-17', 3) "
                "ON CONFLICT (tenant_id, metric, recorded_date) DO UPDATE "
                "SET value = value + excluded.value"
            )
            value = con.execute(
                "SELECT value FROM usage_records "
                "WHERE id = '11111111-1111-1111-1111-111111111111'"
            ).fetchone()[0]
        finally:
            con.close()
        assert value == 8, "upsert 未按约束冲突路径累加"


class TestActiveSkuPartialUnique:
    def test_migrated_db_has_partial_unique(self, migrated_db_path: Path):
        """迁移库：uq_active_sku_name_per_merchant 是 (merchant_id, name)
        的唯一部分索引（只约束活跃行）。"""
        con = sqlite3.connect(migrated_db_path)
        try:
            indexes = _unique_indexes(con, "product_skus")
        finally:
            con.close()
        assert "uq_active_sku_name_per_merchant" in indexes, f"现有: {indexes}"
        cols, partial = indexes["uq_active_sku_name_per_merchant"]
        assert cols == ("merchant_id", "name")
        assert partial == 1, "索引必须是 partial（只约束 is_active=1 行）"

    def test_create_all_db_has_partial_unique(self, create_all_db_path: Path):
        """create_all 库（模型路径）同样存在同名同列部分唯一索引。"""
        con = sqlite3.connect(create_all_db_path)
        try:
            indexes = _unique_indexes(con, "product_skus")
        finally:
            con.close()
        assert "uq_active_sku_name_per_merchant" in indexes, f"现有: {indexes}"
        cols, partial = indexes["uq_active_sku_name_per_merchant"]
        assert cols == ("merchant_id", "name")
        assert partial == 1

    def test_inactive_rows_do_not_collide(self, migrated_db_path: Path):
        """两条同名 SKU，一条活跃一条停用 → 部分索引不拦截（停用后可重建同名）。"""
        con = sqlite3.connect(migrated_db_path)
        try:
            cols = "id, merchant_id, name, canonical_unit, shelf_life_hours, is_active"
            mid = "55555555-5555-5555-5555-555555555555"

            def _insert(row_id: str, is_active: int) -> None:
                con.execute(
                    f"INSERT INTO product_skus ({cols}) VALUES "
                    f"('{row_id}', '{mid}', '番茄', '斤', 72, {is_active})"
                )

            _insert("44444444-4444-4444-4444-444444444444", 1)
            _insert("66666666-6666-6666-6666-666666666666", 0)
            duplicate_active = False
            try:
                _insert("77777777-7777-7777-7777-777777777777", 1)
            except sqlite3.IntegrityError:
                duplicate_active = True
        finally:
            con.close()
        assert duplicate_active, "活跃同名必须被唯一索引拦截"
