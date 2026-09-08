"""add usage_records unique + product_skus active-name partial unique

Revision ID: s1d2e3f4a6b7
Revises: r0b1c2d3e4f5
Create Date: 2026-08-17

一、usage_records 补 UNIQUE (tenant_id, metric, recorded_date)，
约束名 uq_usage_per_tenant_metric_date —— 与 ORM 模型（app/models/saas.py
UsageRecord.__table_args__）一字不差。

为什么需要：quota.record_usage 用 ON CONFLICT (tenant_id, metric,
recorded_date) DO UPDATE 原子累加用量，该语法要求库中存在对应唯一索引/
约束。从迁移链全新建的库（基线 h9c0d1e2f3a4 建表已带约束）没问题；
但「create_all 建表后被 stamp head 托管」的存量库，建表时模型若尚未
声明该约束，stamp 后迁移链不会补 —— /inventory/current 与 /pos/orders
在 ON CONFLICT 上直接 500（实测）。本迁移把缺的约束补上。

幂等：去重 SQL 对已无重复的库是 no-op；建约束/索引前先反射检查，
已存在则跳过（全新迁移库跑本迁移不报「已存在」）。

存量去重：UPSERT 计量上线前 record_usage 是应用层 SELECT-then-INSERT，
并发下可能落下同 (tenant_id, metric, recorded_date) 的重复行，直接建
约束会失败。保留 created_at 最新一条（id 大者 tie-break，与
r0b1c2d3e4f5 同策略）。窗口函数实现，PG 16 / SQLite 3.25+ 双兼容。

二、product_skus 部分唯一索引 uq_active_sku_name_per_merchant
(merchant_id, name) WHERE is_active —— 与 ORM 模型
（app/models/catalog.py ProductSKU.__table_args__）一字不差。

为什么是部分索引而不是普通唯一约束：SKU 删除走软停用（is_active=False），
普通唯一约束会让「停用后同名重建」永远 409；部分索引只约束活跃行，
停用行不占坑。PG / SQLite 均支持 partial index，各自方言渲染 WHERE
谓词（PG 布尔列直接 is_active，SQLite is_active = 1）。

存量去重：同商户活跃同名 SKU 保留 created_at 最新一条，其余置
is_active=0（软停用而非删除 —— SKU 被别名/规格/报价/价格历史引用，
硬删会炸 FK）。

batch_alter_table：SQLite 整表重建；PG 原生 ADD CONSTRAINT。
downgrade 可逆（drop 约束/索引；被去重停用的行不恢复，与
r0b1c2d3e4f5 同策略 —— 重复行本身是脏数据）。
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "s1d2e3f4a6b7"
down_revision: str | None = "r0b1c2d3e4f5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_USAGE_CONSTRAINT = "uq_usage_per_tenant_metric_date"
_SKU_INDEX = "uq_active_sku_name_per_merchant"

# 同 (tenant_id, metric, recorded_date) 保留 created_at 最新一条（id 作
# 最终 tie-break，同秒时保证结果确定），其余删除。id 作 tie-break 的原因
# 同 r0b1c2d3e4f5：UUID 无时间序，但足够让结果确定。
_DEDUP_USAGE_SQL = """
DELETE FROM usage_records
WHERE id IN (
    SELECT id FROM (
        SELECT id,
               ROW_NUMBER() OVER (
                   PARTITION BY tenant_id, metric, recorded_date
                   ORDER BY created_at DESC, id DESC
               ) AS rn
        FROM usage_records
    ) ranked
    WHERE ranked.rn > 1
)
"""

# 同 (merchant_id, name) 的活跃 SKU 保留最新一条，其余软停用（不删行）。
_DEDUP_SKU_SQL = """
UPDATE product_skus SET is_active = 0
WHERE id IN (
    SELECT id FROM (
        SELECT id,
               ROW_NUMBER() OVER (
                   PARTITION BY merchant_id, name
                   ORDER BY created_at DESC, id DESC
               ) AS rn
        FROM product_skus
        WHERE is_active = 1
    ) ranked
    WHERE ranked.rn > 1
)
"""


def _has_object(table: str, name: str) -> bool:
    """约束或索引是否已存在（按名反射，PG / SQLite 双兼容）。"""
    insp = sa.inspect(op.get_bind())
    if any(uc.get("name") == name for uc in insp.get_unique_constraints(table)):
        return True
    return any(ix.get("name") == name for ix in insp.get_indexes(table))


def upgrade() -> None:
    # 一、usage_records：去重 → 补唯一约束
    op.execute(_DEDUP_USAGE_SQL)
    if not _has_object("usage_records", _USAGE_CONSTRAINT):
        with op.batch_alter_table("usage_records") as batch_op:
            batch_op.create_unique_constraint(
                _USAGE_CONSTRAINT, ["tenant_id", "metric", "recorded_date"]
            )

    # 二、product_skus：活跃同名去重 → 部分唯一索引
    op.execute(_DEDUP_SKU_SQL)
    if not _has_object("product_skus", _SKU_INDEX):
        op.create_index(
            _SKU_INDEX,
            "product_skus",
            ["merchant_id", "name"],
            unique=True,
            sqlite_where=sa.text("is_active = 1"),
            postgresql_where=sa.text("is_active"),
        )


def downgrade() -> None:
    op.drop_index(_SKU_INDEX, table_name="product_skus")
    with op.batch_alter_table("usage_records") as batch_op:
        batch_op.drop_constraint(_USAGE_CONSTRAINT, type_="unique")
