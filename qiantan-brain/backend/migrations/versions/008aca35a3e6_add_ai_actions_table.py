"""add ai_actions table

Revision ID: 008aca35a3e6
Revises: 1a35e979b661
Create Date: 2026-07-12 08:48:10.408273
"""
from typing import Union
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


# revision identifiers
revision: str = '008aca35a3e6'
down_revision: str | None = '1a35e979b661'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table('ai_actions',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('merchant_id', sa.Uuid(), nullable=False),
        sa.Column('recommendation_id', sa.Uuid(), nullable=True),
        sa.Column('action_type', sa.String(length=20), nullable=False),
        sa.Column('status', sa.String(length=20), nullable=False),
        sa.Column('title', sa.String(length=100), nullable=False),
        sa.Column('payload', sa.JSON(), nullable=True),
        sa.Column('result', sa.JSON(), nullable=True),
        sa.Column('executed_by', sa.String(length=50), nullable=True),
        sa.Column('created_at', sa.DateTime(), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
        sa.Column('executed_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['merchant_id'], ['merchants.id'], ),
        sa.PrimaryKeyConstraint('id')
    )


def downgrade() -> None:
    op.drop_table('ai_actions')
