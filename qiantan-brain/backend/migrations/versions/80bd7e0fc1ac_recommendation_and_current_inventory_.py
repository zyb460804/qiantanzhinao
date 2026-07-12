"""recommendation_and_current_inventory_sku_id

Revision ID: 80bd7e0fc1ac
Revises: 008aca35a3e6
Create Date: 2026-07-12 09:38:22.228840
"""
from typing import Union
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


# revision identifiers
revision: str = '80bd7e0fc1ac'
down_revision: str | None = '008aca35a3e6'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # P0-B 真正收尾：AI 建议与当前库存视图精确到 SKU。
    # 使用 batch_alter_table 以兼容 SQLite（SQLite 不支持原生 ALTER FK）。
    with op.batch_alter_table('recommendations', schema=None) as batch_op:
        batch_op.add_column(sa.Column('sku_id', sa.Uuid(), nullable=True))
        batch_op.create_index(
            op.f('ix_recommendations_sku_id'), ['sku_id'], unique=False
        )
        batch_op.create_foreign_key(
            'fk_recommendations_sku_id', 'product_skus', ['sku_id'], ['id']
        )

    with op.batch_alter_table('current_inventory', schema=None) as batch_op:
        batch_op.add_column(sa.Column('sku_id', sa.Uuid(), nullable=True))
        batch_op.create_index(
            op.f('ix_current_inventory_sku_id'), ['sku_id'], unique=False
        )
        batch_op.create_foreign_key(
            'fk_current_inventory_sku_id', 'product_skus', ['sku_id'], ['id']
        )


def downgrade() -> None:
    with op.batch_alter_table('current_inventory', schema=None) as batch_op:
        batch_op.drop_constraint('fk_current_inventory_sku_id', type_='foreignkey')
        batch_op.drop_index(op.f('ix_current_inventory_sku_id'))
        batch_op.drop_column('sku_id')

    with op.batch_alter_table('recommendations', schema=None) as batch_op:
        batch_op.drop_constraint('fk_recommendations_sku_id', type_='foreignkey')
        batch_op.drop_index(op.f('ix_recommendations_sku_id'))
        batch_op.drop_column('sku_id')
