"""staff PIN 哈希化：pin_code 列加宽到 128（bcrypt 60 字符）

Revision ID: t2e3f4a5b7c8
Revises: s1d2e3f4a6b7
Create Date: 2026-09-08

P1-4 修复（审计实测三连）：pin_code 原为 String(10) 明文存储，库中直接
可读（'123456'）。改为 bcrypt 哈希（$2b$… 60 字符）需要列宽 ≥ 60。

本迁移只做列宽调整（SQLite 不强制 varchar 长度，整表重建 batch；PG 原生
ALTER）。**不**在 SQL 里哈希存量明文 —— 哈希属于应用层 bcrypt，由
app/routers/staff.py 的 _verify_pin 在员工下次登录成功时透明升级落库；
存量行在升级前保持明文（登录仍兼容）。

downgrade 可逆：列宽回 10（哈希行会被 PG 截断报错 —— 先置 NULL 再缩，
与「明文不回填」同策略，回滚即放弃 PIN）。
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "t2e3f4a5b7c8"
down_revision: str | None = "s1d2e3f4a6b7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("staff_members") as batch:
        batch.alter_column(
            "pin_code",
            existing_type=sa.String(10),
            type_=sa.String(128),
            existing_nullable=True,
        )
    # P2-6：日结关闭时保存完整统计快照（order_count/refund_amount 等），
    # closed 回显不再丢字段。旧行 snapshot=NULL → 回退旧列值（读取端兼容）。
    with op.batch_alter_table("daily_settlements") as batch:
        batch.add_column(sa.Column("snapshot", sa.JSON(), nullable=True))


def downgrade() -> None:
    # 回滚前放弃所有哈希 PIN（明文历史不恢复），再缩列宽。
    op.execute("UPDATE staff_members SET pin_code = NULL WHERE pin_code LIKE '$2%'")
    with op.batch_alter_table("daily_settlements") as batch:
        batch.drop_column("snapshot")
    with op.batch_alter_table("staff_members") as batch:
        batch.alter_column(
            "pin_code",
            existing_type=sa.String(128),
            type_=sa.String(10),
            existing_nullable=True,
        )
