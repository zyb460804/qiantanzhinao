"""add client_id to inventory_records and voice_logs, add pos tables

Revision ID: 1a35e979b661
Revises: 5242218be814
Create Date: 2026-07-12 08:44:47.989192
"""
from typing import Union
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


# revision identifiers
revision: str = '1a35e979b661'
down_revision: str | None = '5242218be814'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # POS / 日结对账表
    op.create_table('daily_settlements',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('merchant_id', sa.Uuid(), nullable=False),
        sa.Column('date', sa.Date(), nullable=False),
        sa.Column('total_sales', sa.Numeric(precision=12, scale=2), nullable=False),
        sa.Column('total_payments', sa.Numeric(precision=12, scale=2), nullable=False),
        sa.Column('cash_amount', sa.Numeric(precision=12, scale=2), nullable=False),
        sa.Column('credit_amount', sa.Numeric(precision=12, scale=2), nullable=False),
        sa.Column('diff_amount', sa.Numeric(precision=12, scale=2), nullable=False),
        sa.Column('status', sa.String(length=20), nullable=False),
        sa.Column('created_at', sa.DateTime(), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
        sa.Column('closed_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['merchant_id'], ['merchants.id'], ),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('merchant_id', 'date', name='uq_settlement_per_day')
    )
    op.create_table('reconciliations',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('merchant_id', sa.Uuid(), nullable=False),
        sa.Column('date', sa.Date(), nullable=False),
        sa.Column('sale_total', sa.Numeric(precision=12, scale=2), nullable=False),
        sa.Column('payment_total', sa.Numeric(precision=12, scale=2), nullable=False),
        sa.Column('inventory_cost_total', sa.Numeric(precision=12, scale=2), nullable=False),
        sa.Column('diff_amount', sa.Numeric(precision=12, scale=2), nullable=False),
        sa.Column('status', sa.String(length=20), nullable=False),
        sa.Column('note', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
        sa.ForeignKeyConstraint(['merchant_id'], ['merchants.id'], ),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('merchant_id', 'date', name='uq_reconciliation_per_day')
    )
    op.create_table('sale_orders',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('merchant_id', sa.Uuid(), nullable=False),
        sa.Column('order_no', sa.String(length=32), nullable=False),
        sa.Column('status', sa.String(length=20), nullable=False),
        sa.Column('total_amount', sa.Numeric(precision=12, scale=2), nullable=False),
        sa.Column('paid_amount', sa.Numeric(precision=12, scale=2), nullable=False),
        sa.Column('discount_amount', sa.Numeric(precision=12, scale=2), nullable=False),
        sa.Column('client_id', sa.String(length=64), nullable=True),
        sa.Column('note', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
        sa.Column('paid_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['merchant_id'], ['merchants.id'], ),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('order_no')
    )
    op.create_index(op.f('ix_sale_orders_client_id'), 'sale_orders', ['client_id'], unique=True)
    op.create_table('payments',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('merchant_id', sa.Uuid(), nullable=False),
        sa.Column('order_id', sa.Uuid(), nullable=True),
        sa.Column('amount', sa.Numeric(precision=12, scale=2), nullable=False),
        sa.Column('method', sa.String(length=20), nullable=False),
        sa.Column('status', sa.String(length=20), nullable=False),
        sa.Column('transaction_id', sa.String(length=64), nullable=True),
        sa.Column('note', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
        sa.ForeignKeyConstraint(['merchant_id'], ['merchants.id'], ),
        sa.ForeignKeyConstraint(['order_id'], ['sale_orders.id'], ),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('transaction_id')
    )
    op.create_table('sale_order_items',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('order_id', sa.Uuid(), nullable=False),
        sa.Column('merchant_id', sa.Uuid(), nullable=False),
        sa.Column('sku_id', sa.Uuid(), nullable=True),
        sa.Column('product_id', sa.Integer(), nullable=True),
        sa.Column('quantity', sa.Numeric(precision=10, scale=2), nullable=False),
        sa.Column('unit', sa.String(length=20), nullable=False),
        sa.Column('unit_price', sa.Numeric(precision=10, scale=2), nullable=True),
        sa.Column('total_amount', sa.Numeric(precision=12, scale=2), nullable=True),
        sa.Column('created_at', sa.DateTime(), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
        sa.ForeignKeyConstraint(['merchant_id'], ['merchants.id'], ),
        sa.ForeignKeyConstraint(['order_id'], ['sale_orders.id'], ),
        sa.ForeignKeyConstraint(['product_id'], ['product_categories.id'], ),
        sa.ForeignKeyConstraint(['sku_id'], ['product_skus.id'], ),
        sa.PrimaryKeyConstraint('id')
    )

    # 离线同步 client_id
    op.add_column('inventory_records', sa.Column('client_id', sa.String(length=64), nullable=True))
    op.add_column('inventory_records', sa.Column('client_reference', sa.String(length=64), nullable=True))
    op.create_index(op.f('ix_inventory_records_client_id'), 'inventory_records', ['client_id'], unique=False)
    op.add_column('voice_logs', sa.Column('client_id', sa.String(length=64), nullable=True))
    op.create_index(op.f('ix_voice_logs_client_id'), 'voice_logs', ['client_id'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_voice_logs_client_id'), table_name='voice_logs')
    op.drop_column('voice_logs', 'client_id')
    op.drop_index(op.f('ix_inventory_records_client_id'), table_name='inventory_records')
    op.drop_column('inventory_records', 'client_reference')
    op.drop_column('inventory_records', 'client_id')
    op.drop_table('sale_order_items')
    op.drop_table('payments')
    op.drop_index(op.f('ix_sale_orders_client_id'), table_name='sale_orders')
    op.drop_table('sale_orders')
    op.drop_table('reconciliations')
    op.drop_table('daily_settlements')
