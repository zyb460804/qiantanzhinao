"""align migration schema with hardened ORM models

Revision ID: n5c6d7e8f9a0
Revises: m4b5c6d7e8f9
Create Date: 2026-08-14

模型对齐迁移（af3df55 模型层硬化后无迁移跟随）：
- 18 张表 created_at：nullable True -> False（ORM 侧 Mapped[datetime] 均为
  nullable=False 且带 server_default=func.now()；迁移建表时也已带
  CURRENT_TIMESTAMP 默认值，故先回填可能的 NULL 行再收紧，无数据风险）。
  实际以 autogenerate 对已迁移库的 diff 为准（18 张，含 m4b5c6d7e8f9
  新建的 4 张 device/dead_letter 表）。
- price_displays.current_price：Float NOT NULL -> Numeric(10,2) NULL
  （对齐 app/models/device.py PriceDisplay.current_price：
  Mapped[Decimal | None] = mapped_column(sa.Numeric(10, 2))）。
- merchant_feedback.created_at：Date -> DateTime（对齐 ORM sa.DateTime）。

全部走 batch_alter_table：SQLite 下自动表重建，PG 16 下退化为原生
ALTER COLUMN，两端兼容。FK ondelete / 索引 / CHECK 约束类漂移不在本迁移
范围（alembic 默认不比较 CHECK 约束），留待后续单独处理。
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "n5c6d7e8f9a0"
down_revision: str | None = "m4b5c6d7e8f9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# 迁移建表时 created_at 误为 nullable=True 而 ORM 为 nullable=False 的表。
# 来源：autogenerate 对 upgrade head 后数据库与 Base.metadata 的实际 diff。
_CREATED_AT_TABLES: tuple[str, ...] = (
    "dead_letter_events",
    "device_firmwares",
    "device_model_versions",
    "device_remote_logs",
    "devices",
    "expenses",
    "invoices",
    "market_complaints",
    "market_inspections",
    "market_merchants",
    "market_notices",
    "markets",
    "payment_channels",
    "price_displays",
    "reconciliation_differences",
    "reconciliation_tasks",
    "sensitive_operations",
    "staff_members",
)


def _tighten_created_at(table: str) -> None:
    """回填历史 NULL 行后把 created_at 收紧为 NOT NULL。

    建表迁移均带 CURRENT_TIMESTAMP server_default，ORM 侧也带
    func.now()，正常路径不会写入 NULL；回填仅防御直接 SQL 写入的行。
    """
    op.execute(
        f"UPDATE {table} SET created_at = CURRENT_TIMESTAMP WHERE created_at IS NULL"
    )
    with op.batch_alter_table(table) as batch_op:
        batch_op.alter_column(
            "created_at",
            existing_type=sa.DateTime(),
            nullable=False,
        )


def upgrade() -> None:
    for table in _CREATED_AT_TABLES:
        _tighten_created_at(table)

    # Float( NOT NULL ) -> Numeric(10,2) NULL，对齐 ORM。
    # PG: float8 -> numeric 走隐式赋值转换；SQLite: 表重建，旧值原样保留。
    with op.batch_alter_table("price_displays") as batch_op:
        batch_op.alter_column(
            "current_price",
            existing_type=sa.Float(),
            type_=sa.Numeric(10, 2),
            nullable=True,
        )

    # Date -> DateTime，对齐 ORM sa.DateTime。PG: date -> timestamp 隐式可转。
    with op.batch_alter_table("merchant_feedback") as batch_op:
        batch_op.alter_column(
            "created_at",
            existing_type=sa.Date(),
            type_=sa.DateTime(),
            nullable=False,
        )


def downgrade() -> None:
    with op.batch_alter_table("merchant_feedback") as batch_op:
        batch_op.alter_column(
            "created_at",
            existing_type=sa.DateTime(),
            type_=sa.Date(),
            nullable=False,
        )

    with op.batch_alter_table("price_displays") as batch_op:
        batch_op.alter_column(
            "current_price",
            existing_type=sa.Numeric(10, 2),
            type_=sa.Float(),
            nullable=False,
        )

    for table in _CREATED_AT_TABLES:
        with op.batch_alter_table(table) as batch_op:
            batch_op.alter_column(
                "created_at",
                existing_type=sa.DateTime(),
                nullable=True,
            )
