"""Staff / Employee / Role models — 多角色权限 (section 4.17).

Roles: owner, manager, cashier, purchaser, stocker, market_admin
Permissions: view_profit, change_price, purchase_confirm, supplier_payment,
             credit_sale, order_refund, inventory_adjust, record_waste,
             daily_settle, void_record, export_data
"""

import uuid
from datetime import datetime

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


ROLE_PERMISSIONS: dict[str, set[str]] = {
    "owner": {
        "view_profit",
        "change_price",
        "purchase_confirm",
        "supplier_payment",
        "credit_sale",
        "order_refund",
        "inventory_adjust",
        "record_waste",
        "daily_settle",
        "void_record",
        "export_data",
        "manage_staff",
        "batch_lock",
        "batch_destroy",
    },
    "manager": {
        "view_profit",
        "change_price",
        "purchase_confirm",
        "supplier_payment",
        "credit_sale",
        "order_refund",
        "inventory_adjust",
        "record_waste",
        "daily_settle",
        "export_data",
    },
    "cashier": {
        "credit_sale",
        "order_refund",
    },
    "purchaser": {
        "purchase_confirm",
    },
    "stocker": {
        "inventory_adjust",
        "record_waste",
    },
    "market_admin": {
        "batch_lock",
        "batch_destroy",
        "export_data",
    },
}


class StaffMember(Base):
    """Employee linked to a merchant account."""

    __tablename__ = "staff_members"

    id: Mapped[uuid.UUID] = mapped_column(sa.Uuid, primary_key=True, default=uuid.uuid4)
    merchant_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid, sa.ForeignKey("merchants.id"), nullable=False
    )
    name: Mapped[str] = mapped_column(sa.String(50), nullable=False)
    phone: Mapped[str | None] = mapped_column(sa.String(20))
    role: Mapped[str] = mapped_column(sa.String(20), nullable=False, default="cashier")
    is_active: Mapped[bool] = mapped_column(sa.Boolean, default=True)
    # P1-4 修复：PIN 以 bcrypt 哈希存储（$2b$… 60 字符）。旧明文由登录时
    # 透明升级（_verify_pin 命中 legacy 明文后即时重哈希落库）。
    pin_code: Mapped[str | None] = mapped_column(sa.String(128))
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime, server_default=sa.func.now(), nullable=False
    )

    __table_args__ = (
        sa.UniqueConstraint("merchant_id", "phone", name="uq_staff_phone_per_merchant"),
        {"comment": "员工档案：角色权限、启用状态与 PIN bcrypt 哈希（登录用）"},
    )


class SensitiveOperation(Base):
    """Audit log extension for sensitive operations requiring authorization."""

    __tablename__ = "sensitive_operations"
    __table_args__ = {"comment": "敏感操作审计：改价/退款/删除等需授权操作的前后快照与授权人"}

    id: Mapped[uuid.UUID] = mapped_column(sa.Uuid, primary_key=True, default=uuid.uuid4)
    merchant_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid, sa.ForeignKey("merchants.id"), nullable=False
    )
    staff_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.Uuid, sa.ForeignKey("staff_members.id", ondelete="SET NULL")
    )
    action: Mapped[str] = mapped_column(sa.String(50), nullable=False)
    target_type: Mapped[str] = mapped_column(sa.String(50), nullable=False)
    target_id: Mapped[str] = mapped_column(sa.String(64), nullable=False)
    before_snapshot: Mapped[str | None] = mapped_column(sa.Text)  # JSON
    after_snapshot: Mapped[str | None] = mapped_column(sa.Text)  # JSON
    authorized_by: Mapped[str | None] = mapped_column(sa.String(50))
    reason: Mapped[str | None] = mapped_column(sa.String(500))
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime, server_default=sa.func.now(), nullable=False
    )
