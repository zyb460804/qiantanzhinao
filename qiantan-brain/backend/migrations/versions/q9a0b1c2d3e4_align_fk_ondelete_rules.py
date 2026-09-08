"""align FK ondelete rules with ORM models

Revision ID: q9a0b1c2d3e4
Revises: o7e8f9a0b1c2
Create Date: 2026-08-14

FK 约束漂移对齐（来源：temp 库 `alembic upgrade head` + `alembic check`
对 Base.metadata 的真实全量 diff）：

1. 9 个 FK：ORM 声明了 ondelete（CASCADE / SET NULL）但建库迁移未落地
   —— drop 旧约束后按 ORM 定义重建。
2. merchants.tenant_id → tenants.id：h9c0d1e2f3a4 只加了列和索引，
   漏了 FK 本身（ORM app/models/merchant.py 有声明）—— 补建。
   风险提示：PG 侧 ADD CONSTRAINT 会校验存量行；tenant_id 由应用层
   写入真实租户 id，且可空，正常无悬挂引用；若生产确有脏数据，先清理
   再执行本迁移。

dev/test SQLite 无法 ALTER FK，全部走 batch_alter_table 整表重建
（数据原样拷贝，同表多个 FK 合并到一次重建）；PG 16 走原生
DROP/ADD CONSTRAINT，drop 前用 pg_catalog 查实际约束名，不假设默认命名。

downgrade 完整可逆：ondelete FK 还原为普通 FK，merchants.tenant_id
的 FK 移除。
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "q9a0b1c2d3e4"
down_revision: str | None = "o7e8f9a0b1c2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# （表, 列, 引用表, ondelete）—— ORM 声明了 ondelete 但 DB 未设的 FK。
_FK_ONDELETE: tuple[tuple[str, str, str, str], ...] = (
    ("sale_order_items", "order_id", "sale_orders", "CASCADE"),
    ("payments", "order_id", "sale_orders", "CASCADE"),
    ("purchase_items", "list_id", "purchase_lists", "CASCADE"),
    ("stocktake_items", "session_id", "stocktake_sessions", "CASCADE"),
    ("sensitive_operations", "staff_id", "staff_members", "SET NULL"),
    ("reconciliation_differences", "task_id", "reconciliation_tasks", "CASCADE"),
    ("channel_bill_imports", "task_id", "reconciliation_tasks", "CASCADE"),
    ("channel_bill_entries", "import_id", "channel_bill_imports", "CASCADE"),
    ("channel_bill_entries", "task_id", "reconciliation_tasks", "CASCADE"),
)

# （表, 列, 引用表）—— 建库迁移漏掉的整条 FK（列已存在，仅缺约束）。
_FK_MISSING: tuple[tuple[str, str, str], ...] = (
    ("merchants", "tenant_id", "tenants"),
)

# SQLite batch 重建时给「无名 FK」按命名约定赋确定性名字，供 drop_constraint 定位
# （SQLite 不存储 FK 约束名，反射回来的名字由该约定重新推导）。
_NAMING = {"fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s"}


def _sqlite_fk_name(table: str, column: str, ref_table: str) -> str:
    """与 _NAMING 约定对齐的确定性 FK 名（SQLite 路径专用）。"""
    return f"fk_{table}_{column}_{ref_table}"


def _pg_fk_name(table: str, column: str) -> str | None:
    """PG 上查该列实际参与的单列 FK 约束名（容忍非默认命名；无则 None）。"""
    bind = op.get_bind()
    return bind.execute(
        sa.text(
            "SELECT con.conname FROM pg_constraint con "
            "JOIN pg_class rel ON rel.oid = con.conrelid "
            "JOIN pg_attribute att ON att.attrelid = rel.oid AND att.attnum = ANY(con.conkey) "
            "WHERE con.contype = 'f' AND rel.relname = :t AND att.attname = :c "
            "AND array_length(con.conkey, 1) = 1"
        ),
        {"t": table, "c": column},
    ).scalar_one_or_none()


def _by_table() -> dict[str, list[tuple[str, str, str]]]:
    """把 (表, 列, 引用表, ondelete) 按表分组，SQLite 下同表只重建一次。"""
    grouped: dict[str, list[tuple[str, str, str]]] = {}
    for table, column, ref, ondelete in _FK_ONDELETE:
        grouped.setdefault(table, []).append((column, ref, ondelete))
    return grouped


def upgrade() -> None:
    is_sqlite = op.get_bind().dialect.name == "sqlite"

    if is_sqlite:
        for table, fks in _by_table().items():
            with op.batch_alter_table(table, naming_convention=_NAMING) as batch_op:
                for column, ref, ondelete in fks:
                    batch_op.drop_constraint(
                        _sqlite_fk_name(table, column, ref), type_="foreignkey"
                    )
                    batch_op.create_foreign_key(
                        _sqlite_fk_name(table, column, ref),
                        ref,
                        [column],
                        ["id"],
                        ondelete=ondelete,
                    )
        for table, column, ref in _FK_MISSING:
            with op.batch_alter_table(table, naming_convention=_NAMING) as batch_op:
                batch_op.create_foreign_key(
                    _sqlite_fk_name(table, column, ref), ref, [column], ["id"]
                )
    else:
        for table, column, ref, ondelete in _FK_ONDELETE:
            existing = _pg_fk_name(table, column)
            if existing is not None:
                op.drop_constraint(existing, table, type_="foreignkey")
            op.create_foreign_key(
                f"{table}_{column}_fkey", table, ref, [column], ["id"], ondelete=ondelete
            )
        for table, column, ref in _FK_MISSING:
            if _pg_fk_name(table, column) is None:
                op.create_foreign_key(
                    f"{table}_{column}_fkey", table, ref, [column], ["id"]
                )


def downgrade() -> None:
    is_sqlite = op.get_bind().dialect.name == "sqlite"

    if is_sqlite:
        for table, column, ref in _FK_MISSING:
            with op.batch_alter_table(table, naming_convention=_NAMING) as batch_op:
                batch_op.drop_constraint(
                    _sqlite_fk_name(table, column, ref), type_="foreignkey"
                )
        for table, fks in _by_table().items():
            with op.batch_alter_table(table, naming_convention=_NAMING) as batch_op:
                for column, ref, _ondelete in fks:
                    batch_op.drop_constraint(
                        _sqlite_fk_name(table, column, ref), type_="foreignkey"
                    )
                    # 还原为不带 ondelete 的普通 FK（迁移前形态）
                    batch_op.create_foreign_key(
                        _sqlite_fk_name(table, column, ref), ref, [column], ["id"]
                    )
    else:
        for table, column, _ref in _FK_MISSING:
            existing = _pg_fk_name(table, column)
            if existing is not None:
                op.drop_constraint(existing, table, type_="foreignkey")
        for table, column, ref, _ondelete in _FK_ONDELETE:
            existing = _pg_fk_name(table, column)
            if existing is not None:
                op.drop_constraint(existing, table, type_="foreignkey")
            op.create_foreign_key(
                f"{table}_{column}_fkey", table, ref, [column], ["id"]
            )
