"""add invoice per-subscription period unique constraint

Revision ID: r0b1c2d3e4f5
Revises: q9a0b1c2d3e4
Create Date: 2026-08-14

saas_invoices 加 UNIQUE (subscription_id, period_start)，约束名
uq_invoice_subscription_period —— 防止同一订阅同一计费周期重复出账
（发票一路的 DB 侧配套；ORM 侧同名 UniqueConstraint 由 saas 模型路落地）。

NULL 语义：PG 16 与 SQLite 默认均 NULLS DISTINCT（NULL 不参与唯一性
比较），存量 subscription_id/period_start 为 NULL 的行（手工账单等）
不会冲突，可直接建约束。

存量去重（V1-M5）：建约束前先清理同 (subscription_id, period_start)
的重复行 —— 两个出票入口历史上键未归一时产生的重叠期票会撞新约束使
迁移失败。保留规则「已付优先、再保留最新」：每组保留一张 status='paid'
的票（无则保留 created_at 最新的一张），其余删除。窗口函数实现，
PG 16 / SQLite 3.25+ 双兼容。

batch_alter_table：SQLite 整表重建；PG 原生 ADD CONSTRAINT。
downgrade 可逆（drop 约束；被去重删除的行不恢复，与 n6d7e8f9a0b1
同策略——重复票本身是脏数据）。
"""

from collections.abc import Sequence

from alembic import op


revision: str = "r0b1c2d3e4f5"
down_revision: str | None = "q9a0b1c2d3e4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_CONSTRAINT_NAME = "uq_invoice_subscription_period"

# 同 (subscription_id, period_start) 保留 rn=1（已付优先 → 最新 → id 大者），
# 其余删除。id 作最终 tie-break：created_at 同秒时保证结果确定。
_DEDUP_SQL = """
DELETE FROM saas_invoices
WHERE id IN (
    SELECT id FROM (
        SELECT id,
               ROW_NUMBER() OVER (
                   PARTITION BY subscription_id, period_start
                   ORDER BY (status = 'paid') DESC,
                            created_at DESC,
                            id DESC
               ) AS rn
        FROM saas_invoices
        WHERE subscription_id IS NOT NULL
          AND period_start IS NOT NULL
    ) ranked
    WHERE ranked.rn > 1
)
"""


def upgrade() -> None:
    op.execute(_DEDUP_SQL)
    with op.batch_alter_table("saas_invoices") as batch_op:
        batch_op.create_unique_constraint(
            _CONSTRAINT_NAME, ["subscription_id", "period_start"]
        )


def downgrade() -> None:
    with op.batch_alter_table("saas_invoices") as batch_op:
        batch_op.drop_constraint(_CONSTRAINT_NAME, type_="unique")
