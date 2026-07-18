"""支付对账模型 (section 4.7).

- PaymentChannel: 支付渠道配置
- ReconciliationTask: 对账任务
- ReconciliationDifference: 对账差异
"""

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class PaymentChannel(Base):
    """微信/支付宝等渠道配置（商户号、密钥引用等，不存真实密钥）。"""

    __tablename__ = "payment_channels"

    id: Mapped[uuid.UUID] = mapped_column(sa.Uuid, primary_key=True, default=uuid.uuid4)
    merchant_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid, sa.ForeignKey("merchants.id"), nullable=False
    )
    channel: Mapped[str] = mapped_column(sa.String(20), nullable=False)  # wechat/alipay
    sub_mch_id: Mapped[str | None] = mapped_column(sa.String(64))  # 子商户号
    fee_rate: Mapped[Decimal] = mapped_column(sa.Numeric(6, 4), default=Decimal("0.006"))  # 0.6%
    is_active: Mapped[bool] = mapped_column(sa.Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime, server_default=sa.func.now(), nullable=True
    )

    __table_args__ = (
        sa.UniqueConstraint("merchant_id", "channel", name="uq_channel_per_merchant"),
    )


class ReconciliationTask(Base):
    """每日对账任务 — 系统订单 vs 渠道账单。"""

    __tablename__ = "reconciliation_tasks"

    id: Mapped[uuid.UUID] = mapped_column(sa.Uuid, primary_key=True, default=uuid.uuid4)
    merchant_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid, sa.ForeignKey("merchants.id"), nullable=False
    )
    channel: Mapped[str] = mapped_column(sa.String(20), nullable=False)
    date: Mapped[date] = mapped_column(sa.Date, nullable=False)
    status: Mapped[str] = mapped_column(
        sa.String(20), default="pending"
    )  # pending/running/balanced/exception/resolved
    system_total: Mapped[Decimal] = mapped_column(sa.Numeric(12, 2), default=Decimal("0"))
    channel_total: Mapped[Decimal] = mapped_column(sa.Numeric(12, 2), default=Decimal("0"))
    diff_amount: Mapped[Decimal] = mapped_column(sa.Numeric(12, 2), default=Decimal("0"))
    matched_count: Mapped[int] = mapped_column(sa.Integer, default=0)
    unmatched_system: Mapped[int] = mapped_column(sa.Integer, default=0)
    unmatched_channel: Mapped[int] = mapped_column(sa.Integer, default=0)
    fee_amount: Mapped[Decimal] = mapped_column(sa.Numeric(12, 2), default=Decimal("0"))
    note: Mapped[str | None] = mapped_column(sa.Text)
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime, server_default=sa.func.now(), nullable=True
    )

    __table_args__ = (
        sa.UniqueConstraint("merchant_id", "channel", "date", name="uq_recon_per_day_channel"),
    )


class ReconciliationDifference(Base):
    """单条对账差异明细。"""

    __tablename__ = "reconciliation_differences"

    id: Mapped[uuid.UUID] = mapped_column(sa.Uuid, primary_key=True, default=uuid.uuid4)
    task_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid, sa.ForeignKey("reconciliation_tasks.id"), nullable=False
    )
    merchant_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid, sa.ForeignKey("merchants.id"), nullable=False
    )
    diff_type: Mapped[str] = mapped_column(sa.String(30), nullable=False)
    # system_only: 系统有订单但无到账
    # channel_only: 有到账但无订单
    # amount_mismatch: 金额不一致
    # duplicate: 重复收款
    system_ref: Mapped[str | None] = mapped_column(sa.String(64))  # 我方订单号
    channel_ref: Mapped[str | None] = mapped_column(sa.String(64))  # 渠道交易号
    system_amount: Mapped[Decimal | None] = mapped_column(sa.Numeric(12, 2))
    channel_amount: Mapped[Decimal | None] = mapped_column(sa.Numeric(12, 2))
    status: Mapped[str] = mapped_column(sa.String(20), default="open")  # open/resolved/ignored
    resolution: Mapped[str | None] = mapped_column(sa.String(200))
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime, server_default=sa.func.now(), nullable=True
    )


class ChannelBillImport(Base):
    """An immutable import batch for a channel statement file."""

    __tablename__ = "channel_bill_imports"

    id: Mapped[uuid.UUID] = mapped_column(sa.Uuid, primary_key=True, default=uuid.uuid4)
    task_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid, sa.ForeignKey("reconciliation_tasks.id"), nullable=False, index=True
    )
    merchant_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid, sa.ForeignKey("merchants.id"), nullable=False, index=True
    )
    channel: Mapped[str] = mapped_column(sa.String(20), nullable=False)
    bill_date: Mapped[date] = mapped_column(sa.Date, nullable=False)
    file_name: Mapped[str] = mapped_column(sa.String(255), nullable=False)
    file_hash: Mapped[str] = mapped_column(sa.String(64), nullable=False)
    row_count: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=0)
    inserted_count: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=0)
    duplicate_count: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime, server_default=sa.func.now(), nullable=False
    )

    __table_args__ = (
        sa.UniqueConstraint(
            "merchant_id",
            "channel",
            "bill_date",
            "file_hash",
            name="uq_channel_bill_import_file",
        ),
    )


class ChannelBillEntry(Base):
    """Normalized payment/refund row imported from a channel statement."""

    __tablename__ = "channel_bill_entries"

    id: Mapped[uuid.UUID] = mapped_column(sa.Uuid, primary_key=True, default=uuid.uuid4)
    import_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid, sa.ForeignKey("channel_bill_imports.id"), nullable=False, index=True
    )
    task_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid, sa.ForeignKey("reconciliation_tasks.id"), nullable=False, index=True
    )
    merchant_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid, sa.ForeignKey("merchants.id"), nullable=False, index=True
    )
    channel: Mapped[str] = mapped_column(sa.String(20), nullable=False)
    bill_date: Mapped[date] = mapped_column(sa.Date, nullable=False)
    entry_key: Mapped[str] = mapped_column(sa.String(64), nullable=False)
    record_type: Mapped[str] = mapped_column(sa.String(20), nullable=False)
    channel_ref: Mapped[str | None] = mapped_column(sa.String(128), index=True)
    merchant_ref: Mapped[str | None] = mapped_column(sa.String(128), index=True)
    amount: Mapped[Decimal] = mapped_column(sa.Numeric(12, 2), nullable=False)
    fee_amount: Mapped[Decimal] = mapped_column(
        sa.Numeric(12, 2), nullable=False, default=Decimal("0")
    )
    occurred_at: Mapped[datetime | None] = mapped_column(sa.DateTime)
    channel_status: Mapped[str | None] = mapped_column(sa.String(50))
    matched_payment_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.Uuid, sa.ForeignKey("payments.id"), index=True
    )
    match_status: Mapped[str] = mapped_column(sa.String(30), nullable=False, default="unmatched")
    raw_data: Mapped[dict[str, Any] | None] = mapped_column(sa.JSON)
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime, server_default=sa.func.now(), nullable=False
    )

    __table_args__ = (
        sa.UniqueConstraint(
            "merchant_id",
            "channel",
            "bill_date",
            "entry_key",
            name="uq_channel_bill_entry_key",
        ),
        sa.Index(
            "ix_channel_bill_entry_task_match",
            "task_id",
            "match_status",
        ),
    )
