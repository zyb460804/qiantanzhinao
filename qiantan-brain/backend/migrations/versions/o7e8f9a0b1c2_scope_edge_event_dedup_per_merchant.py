"""scope edge event_id dedup per merchant (security M-5)

Revision ID: o7e8f9a0b1c2
Revises: n6d7e8f9a0b1
Create Date: 2026-08-14

edge_events.event_id 幂等去重键由全局唯一改为 (merchant_id, event_id)：
不同商户的边缘设备各自生成序列号，复用同一 event_id 属正常事件；
全局唯一约束会把商户 B 的合法事件误判为商户 A 的重复而丢弃。
旧全局约束保证不存在跨商户重复行，因此本迁移可无损收窄。
"""
from collections.abc import Sequence

from alembic import op


revision: str = "o7e8f9a0b1c2"
down_revision: str | None = "n6d7e8f9a0b1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("edge_events") as batch_op:
        batch_op.drop_constraint("uq_edge_events_event_id", type_="unique")
        batch_op.create_unique_constraint(
            "uq_edge_events_merchant_event_id",
            ["merchant_id", "event_id"],
        )


def downgrade() -> None:
    with op.batch_alter_table("edge_events") as batch_op:
        batch_op.drop_constraint("uq_edge_events_merchant_event_id", type_="unique")
        batch_op.create_unique_constraint("uq_edge_events_event_id", ["event_id"])
