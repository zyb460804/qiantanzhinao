"""
migration_offline.py — 离线幂等字段迁移（参考实现 / 教学样例）
────────────────────────────────────────────────────────────────────────
对应 PRD §7.2 D3「client_id 唯一约束涉及数据库迁移（沿用现有 Alembic 流程）」。

这是一份 Alembic 版本的「骨架」。接入生产时：
  1. 用 `alembic revision --autogenerate -m "add client_id for offline idempotency"` 生成正式版本；
  2. 把下面 upgrade()/downgrade() 的 SQL 作为参考核对（务必与附录 A 的字段/索引一致）。
禁止直接 ALTER 线上表而不走迁移！
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


# 版本号占位：生产由 alembic 自动生成，勿手填
revision = "0002_offline_client_id"
down_revision = "001_new_tables_and_void_fields"  # 承接现有最新版本
branch_labels = None
depends_on = None


def upgrade() -> None:
    # InventoryRecord 增加 client_id
    op.add_column(
        "inventory_records",
        sa.Column("client_id", sa.String(length=64), nullable=True),
    )
    op.create_index("ix_inventory_records_client_id", "inventory_records", ["client_id"])
    # 唯一约束：(merchant_id, client_id)
    op.create_unique_constraint(
        "uq_ir_merchant_client", "inventory_records", ["merchant_id", "client_id"]
    )

    # VoiceLog 同样增加（若离线语音也走幂等）
    op.add_column(
        "voice_logs",
        sa.Column("client_id", sa.String(length=64), nullable=True),
    )
    op.create_index("ix_voice_logs_client_id", "voice_logs", ["client_id"])
    op.create_unique_constraint(
        "uq_vl_merchant_client", "voice_logs", ["merchant_id", "client_id"]
    )

    # 收款台若新建 cashier_receipts 表，同样加 client_id + 唯一约束（PRD 附录 A 备注）


def downgrade() -> None:
    op.drop_constraint("uq_vl_merchant_client", "voice_logs", type_="unique")
    op.drop_index("ix_voice_logs_client_id", table_name="voice_logs")
    op.drop_column("voice_logs", "client_id")

    op.drop_constraint("uq_ir_merchant_client", "inventory_records", type_="unique")
    op.drop_index("ix_inventory_records_client_id", table_name="inventory_records")
    op.drop_column("inventory_records", "client_id")
