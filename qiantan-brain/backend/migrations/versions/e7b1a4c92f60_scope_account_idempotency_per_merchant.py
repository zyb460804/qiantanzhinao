"""scope account ledger idempotency keys to each merchant

Revision ID: e7b1a4c92f60
Revises: c3d8f6a42b91
Create Date: 2026-07-12
"""
from collections.abc import Sequence

from alembic import op


revision: str = "e7b1a4c92f60"
down_revision: str | None = "c3d8f6a42b91"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _scope_to_merchant(table: str, index: str, constraint: str) -> None:
    with op.batch_alter_table(table, schema=None) as batch_op:
        batch_op.drop_index(index)
        batch_op.create_index(index, ["idempotency_key"], unique=False)
        batch_op.create_unique_constraint(
            constraint,
            ["merchant_id", "idempotency_key"],
        )


def _restore_global(table: str, index: str, constraint: str) -> None:
    with op.batch_alter_table(table, schema=None) as batch_op:
        batch_op.drop_constraint(constraint, type_="unique")
        batch_op.drop_index(index)
        batch_op.create_index(index, ["idempotency_key"], unique=True)


def upgrade() -> None:
    _scope_to_merchant(
        "supplier_payables",
        "ix_supplier_payables_idempotency_key",
        "uq_supplier_payable_idempotency_per_merchant",
    )
    _scope_to_merchant(
        "customer_receivables",
        "ix_customer_receivables_idempotency_key",
        "uq_customer_receivable_idempotency_per_merchant",
    )


def downgrade() -> None:
    _restore_global(
        "customer_receivables",
        "ix_customer_receivables_idempotency_key",
        "uq_customer_receivable_idempotency_per_merchant",
    )
    _restore_global(
        "supplier_payables",
        "ix_supplier_payables_idempotency_key",
        "uq_supplier_payable_idempotency_per_merchant",
    )
