"""add missing device and dead_letter tables

Revision ID: m4b5c6d7e8f9
Revises: l3f4a5b6c7d8
Create Date: 2026-07-27

修复（审计 P1-2）：以下 4 张表在 models/ 中定义且被线上代码引用（edge.py /
admin/operations.py / health_monitor.py / offline_sync.py），但从未出现在任何
迁移文件中。生产环境走 Alembic 路径时这些表不存在，对应端点报 relation does
not exist。dev 环境靠 database.py 的 create_all 回退掩盖了漂移。
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "m4b5c6d7e8f9"
down_revision: str | None = "l3f4a5b6c7d8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 1) dead_letter_events —— 死信队列（offline_sync.py 失败时写入）
    op.create_table(
        "dead_letter_events",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "merchant_id",
            sa.Uuid(),
            sa.ForeignKey("merchants.id"),
            nullable=False,
        ),
        sa.Column("idempotency_key", sa.String(64), nullable=True),
        sa.Column("event_type", sa.String(30), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("error_message", sa.Text(), nullable=False),
        sa.Column("retry_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("max_retries", sa.Integer(), nullable=False, server_default="3"),
        sa.Column("next_retry_at", sa.DateTime(), nullable=True),
        sa.Column("status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column(
            "created_at",
            sa.DateTime(),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=True,
        ),
        sa.Column("resolved_at", sa.DateTime(), nullable=True),
    )
    op.create_index(
        "ix_dead_letter_events_merchant_id", "dead_letter_events", ["merchant_id"]
    )
    op.create_index("ix_dead_letter_events_status", "dead_letter_events", ["status"])

    # 2) device_firmwares —— OTA 固件/模型版本管理
    op.create_table(
        "device_firmwares",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("device_type", sa.String(30), nullable=False),
        sa.Column("version", sa.String(20), nullable=False),
        sa.Column("file_url", sa.String(500), nullable=False),
        sa.Column("file_hash", sa.String(64), nullable=False),
        sa.Column("file_size", sa.Integer(), nullable=False),
        sa.Column("changelog", sa.Text(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("rollout_percentage", sa.Integer(), nullable=False, server_default="100"),
        sa.Column("min_hardware_version", sa.String(20), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=True,
        ),
    )

    # 3) device_model_versions —— 设备端模型版本上报
    op.create_table(
        "device_model_versions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "device_id",
            sa.Uuid(),
            sa.ForeignKey("devices.id"),
            nullable=False,
        ),
        sa.Column("model_type", sa.String(30), nullable=False),
        sa.Column("model_version", sa.String(30), nullable=False),
        sa.Column("reported_at", sa.DateTime(), nullable=False),
        sa.Column("metadata_", sa.JSON(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=True,
        ),
    )
    op.create_index(
        "ix_device_model_versions_device_id", "device_model_versions", ["device_id"]
    )

    # 4) device_remote_logs —— 设备远程日志收集
    op.create_table(
        "device_remote_logs",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "device_id",
            sa.Uuid(),
            sa.ForeignKey("devices.id"),
            nullable=False,
        ),
        sa.Column("level", sa.String(10), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("source", sa.String(50), nullable=True),
        sa.Column("device_timestamp", sa.DateTime(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=True,
        ),
    )
    op.create_index(
        "ix_device_remote_logs_device_id", "device_remote_logs", ["device_id"]
    )


def downgrade() -> None:
    op.drop_table("device_remote_logs")
    op.drop_table("device_model_versions")
    op.drop_table("device_firmwares")
    op.drop_table("dead_letter_events")
