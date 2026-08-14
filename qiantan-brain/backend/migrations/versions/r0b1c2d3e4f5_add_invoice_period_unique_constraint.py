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

batch_alter_table：SQLite 整表重建；PG 原生 ADD CONSTRAINT。
downgrade 可逆（drop 约束）。
"""

from collections.abc import Sequence

from alembic import op


revision: str = "r0b1c2d3e4f5"
down_revision: str | None = "q9a0b1c2d3e4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_CONSTRAINT_NAME = "uq_invoice_subscription_period"


def upgrade() -> None:
    with op.batch_alter_table("saas_invoices") as batch_op:
        batch_op.create_unique_constraint(
            _CONSTRAINT_NAME, ["subscription_id", "period_start"]
        )


def downgrade() -> None:
    with op.batch_alter_table("saas_invoices") as batch_op:
        batch_op.drop_constraint(_CONSTRAINT_NAME, type_="unique")
