"""SQLAlchemy models package."""

from app.models.accounts import CustomerCreditProfile, CustomerReceivable, SupplierPayable
from app.models.admin_audit import AdminAuditLog
from app.models.ai_action import AIAction
from app.models.audit import AuditLog
from app.models.auth import AuthRevokedToken
from app.models.batch import BatchLifecycle
from app.models.catalog import (
    PriceHistory,
    ProductAlias,
    ProductSKU,
    ProductSpecification,
    Supplier,
    SupplierProduct,
    Unit,
    UnitConversion,
)
from app.models.dead_letter import DeadLetterEvent
from app.models.device import (
    Device,
    DeviceFirmware,
    DeviceModelVersion,
    DeviceRemoteLog,
    PriceDisplay,
)
from app.models.edge_event import EdgeEvent
from app.models.environment import EnvironmentRecord
from app.models.expense import Expense as ExpenseRecord
from app.models.expense import Invoice as ExpenseInvoice
from app.models.feedback import MerchantFeedback
from app.models.idempotency import IdempotencyRecord
from app.models.inventory import CurrentInventory, InventoryRecord
from app.models.market import (
    Market,
    MarketComplaint,
    MarketInspection,
    MarketMerchant,
    MarketNotice,
)
from app.models.media import MediaFile
from app.models.merchant import Merchant
from app.models.payment import (
    ChannelBillEntry,
    ChannelBillImport,
    PaymentChannel,
    ReconciliationDifference,
    ReconciliationTask,
)
from app.models.pos import DailySettlement, Payment, Reconciliation, SaleOrder, SaleOrderItem
from app.models.preference import MerchantPreference
from app.models.product import ProductCategory
from app.models.purchase import PurchaseItem, PurchaseList
from app.models.recommendation import Recommendation
from app.models.saas import (
    ApiKey,
    Invoice,
    Plan,
    PlatformAdmin,
    Subscription,
    Tenant,
    UsageRecord,
)
from app.models.simulation import SimulationRecord
from app.models.staff import SensitiveOperation, StaffMember
from app.models.stocktake import StocktakeItem, StocktakeSession
from app.models.voice import VoiceLog


__all__ = [
    "Merchant",
    "ProductCategory",
    "InventoryRecord",
    "CurrentInventory",
    "VoiceLog",
    "EnvironmentRecord",
    "Recommendation",
    "SimulationRecord",
    "BatchLifecycle",
    "MerchantPreference",
    "AuditLog",
    "AuthRevokedToken",
    "StocktakeSession",
    "StocktakeItem",
    "PurchaseList",
    "PurchaseItem",
    "SupplierPayable",
    "CustomerReceivable",
    "CustomerCreditProfile",
    "MerchantFeedback",
    "IdempotencyRecord",
    "SaleOrder",
    "SaleOrderItem",
    "Payment",
    "DailySettlement",
    "Reconciliation",
    "AIAction",
    "DeadLetterEvent",
    "ProductSKU",
    "ProductAlias",
    "ProductSpecification",
    "Unit",
    "UnitConversion",
    "Supplier",
    "SupplierProduct",
    "PriceHistory",
    # 设备
    "Device",
    "DeviceFirmware",
    "DeviceModelVersion",
    "DeviceRemoteLog",
    "PriceDisplay",
    "EdgeEvent",
    # 费用与发票
    "ExpenseRecord",
    "ExpenseInvoice",
    # 市场管理
    "Market",
    "MarketMerchant",
    "MarketInspection",
    "MarketComplaint",
    "MarketNotice",
    # 媒体
    "MediaFile",
    # 支付对账
    "PaymentChannel",
    "ReconciliationTask",
    "ReconciliationDifference",
    "ChannelBillImport",
    "ChannelBillEntry",
    # 员工
    "StaffMember",
    "SensitiveOperation",
    # SaaS 多租户
    "Tenant",
    "Plan",
    "Subscription",
    "Invoice",
    "UsageRecord",
    "ApiKey",
    "PlatformAdmin",
    "AdminAuditLog",
]
