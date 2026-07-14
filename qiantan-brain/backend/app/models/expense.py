"""费用与发票模型 (section 4.19).

- Expense: 租金/水电/人工/手续费等各项费用
- Invoice: 数电发票归档
"""

import uuid
from datetime import date, datetime
from decimal import Decimal

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class Expense(Base):
    """经营费用 — 租金、水电、人工、其他。"""

    __tablename__ = "expenses"

    id: Mapped[uuid.UUID] = mapped_column(sa.Uuid, primary_key=True, default=uuid.uuid4)
    merchant_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid, sa.ForeignKey("merchants.id"), nullable=False
    )
    category: Mapped[str] = mapped_column(
        sa.String(30), nullable=False
    )  # rent/utility/labor/fee/other
    amount: Mapped[Decimal] = mapped_column(sa.Numeric(12, 2), nullable=False)
    description: Mapped[str | None] = mapped_column(sa.String(200))
    expense_date: Mapped[date] = mapped_column(sa.Date, nullable=False)
    payment_method: Mapped[str | None] = mapped_column(sa.String(20))  # cash/wechat/bank_transfer
    invoice_id: Mapped[uuid.UUID | None] = mapped_column(sa.Uuid)
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime, server_default=sa.func.now(), nullable=True
    )


class Invoice(Base):
    """发票归档 — 数电发票信息。"""

    __tablename__ = "invoices"

    id: Mapped[uuid.UUID] = mapped_column(sa.Uuid, primary_key=True, default=uuid.uuid4)
    merchant_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid, sa.ForeignKey("merchants.id"), nullable=False
    )
    invoice_number: Mapped[str] = mapped_column(sa.String(50), nullable=False)
    invoice_type: Mapped[str] = mapped_column(
        sa.String(20), default="electronic"
    )  # electronic/paper
    supplier_name: Mapped[str | None] = mapped_column(sa.String(100))
    amount: Mapped[Decimal] = mapped_column(sa.Numeric(12, 2), nullable=False)
    tax_amount: Mapped[Decimal | None] = mapped_column(sa.Numeric(12, 2))
    invoice_date: Mapped[date] = mapped_column(sa.Date, nullable=False)
    file_url: Mapped[str | None] = mapped_column(sa.String(500))
    notes: Mapped[str | None] = mapped_column(sa.Text)
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime, server_default=sa.func.now(), nullable=True
    )

    __table_args__ = (
        sa.UniqueConstraint("merchant_id", "invoice_number", name="uq_invoice_per_merchant"),
    )
