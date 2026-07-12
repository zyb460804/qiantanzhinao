"""商品 / 单位 / 供应商 目录模型 — 真实经营闭环的基座（对应战略文档第一节）。

为什么需要它（而不是继续用扁平的 product_categories）：
- 同一商品多个叫法：番茄 / 西红柿 / 洋柿子  → ProductAlias
- 不同规格：大果 / 小果 / 精品 / 次品          → ProductSpecification
- 不同单位与换算：斤 / 公斤 / 筐 / 袋 / 件      → Unit + UnitConversion
- 采购按筐、销售按斤                            → 记录始终以「标准单位」入账，
                                                换算只在边界（语音/导入）完成
- 不同供应商、不同批次、不同成本                → Supplier + SupplierProduct

设计原则：
- 这些是新增表，不改动现有 product_categories / inventory_records，向后兼容。
- ProductSKU 取代 ProductCategory 成为库存/账本的真正主键（category 仅保留兼容）。
- 所有金额使用 sa.Numeric，数量同理；时间统一服务端时区。
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class ProductSKU(Base):
    """可经营的最小商品单元（标准名）。库存、批次、账本都挂在 SKU 上。"""

    __tablename__ = "product_skus"

    id: Mapped[uuid.UUID] = mapped_column(sa.Uuid, primary_key=True, default=uuid.uuid4)
    merchant_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid, sa.ForeignKey("merchants.id"), nullable=False
    )
    name: Mapped[str] = mapped_column(sa.String(50), nullable=False)  # 标准名：番茄
    category_group: Mapped[str | None] = mapped_column(sa.String(30))
    canonical_unit: Mapped[str] = mapped_column(
        sa.String(10), nullable=False, default="斤"
    )  # 该 SKU 的账本标准单位（斤/个/盒…）
    shelf_life_hours: Mapped[int] = mapped_column(sa.Integer, default=72)
    default_sale_price: Mapped[Decimal | None] = mapped_column(sa.Numeric(10, 2))
    is_active: Mapped[bool] = mapped_column(sa.Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(sa.DateTime, server_default=sa.func.now())


class ProductAlias(Base):
    """商品别名 → SKU 映射。番茄/西红柿/洋柿子 都指向同一个 SKU。"""

    __tablename__ = "product_aliases"

    id: Mapped[uuid.UUID] = mapped_column(sa.Uuid, primary_key=True, default=uuid.uuid4)
    merchant_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid, sa.ForeignKey("merchants.id"), nullable=False
    )
    sku_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid, sa.ForeignKey("product_skus.id"), nullable=False
    )
    alias: Mapped[str] = mapped_column(sa.String(50), nullable=False)
    is_system: Mapped[bool] = mapped_column(
        sa.Boolean, default=False
    )  # True=系统内置（如 西红柿→番茄）
    __table_args__ = (sa.UniqueConstraint("merchant_id", "alias", name="uq_alias_per_merchant"),)


class ProductSpecification(Base):
    """同一 SKU 的不同规格，影响售价与识别。"""

    __tablename__ = "product_specifications"

    id: Mapped[uuid.UUID] = mapped_column(sa.Uuid, primary_key=True, default=uuid.uuid4)
    merchant_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid, sa.ForeignKey("merchants.id"), nullable=False
    )
    sku_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid, sa.ForeignKey("product_skus.id"), nullable=False
    )
    name: Mapped[str] = mapped_column(sa.String(30), nullable=False)  # 大果/精品
    price_delta: Mapped[Decimal] = mapped_column(sa.Numeric(10, 2), default=0)  # 相对标准售价的加价
    is_active: Mapped[bool] = mapped_column(sa.Boolean, default=True)


class Unit(Base):
    """单位字典。kind 用于区分重量/包装/计件。"""

    __tablename__ = "units"

    id: Mapped[uuid.UUID] = mapped_column(sa.Uuid, primary_key=True, default=uuid.uuid4)
    merchant_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid, sa.ForeignKey("merchants.id"), nullable=False
    )
    code: Mapped[str] = mapped_column(sa.String(10), nullable=False)  # 斤/筐/件
    name: Mapped[str] = mapped_column(sa.String(20), nullable=False)
    kind: Mapped[str] = mapped_column(sa.String(10), default="weight")  # weight / package / count
    is_base: Mapped[bool] = mapped_column(sa.Boolean, default=False)
    __table_args__ = (sa.UniqueConstraint("merchant_id", "code", name="uq_unit_code_per_merchant"),)


class UnitConversion(Base):
    """换算因子：to_base = quantity * factor。

    例：筐→斤，因子随商品/商家不同（一筐西红柿约 45 斤，一筐土豆约 60 斤）。
    sku_id 为 NULL 表示通用换算（如 公斤→斤 = 2）。
    """

    __tablename__ = "unit_conversions"

    id: Mapped[uuid.UUID] = mapped_column(sa.Uuid, primary_key=True, default=uuid.uuid4)
    merchant_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid, sa.ForeignKey("merchants.id"), nullable=False
    )
    from_unit: Mapped[str] = mapped_column(sa.String(10), nullable=False)
    to_unit: Mapped[str] = mapped_column(sa.String(10), nullable=False)
    factor: Mapped[Decimal] = mapped_column(sa.Numeric(12, 4), nullable=False)
    sku_id: Mapped[uuid.UUID | None] = mapped_column(sa.Uuid)
    created_at: Mapped[datetime] = mapped_column(sa.DateTime, server_default=sa.func.now())
    __table_args__ = (
        sa.UniqueConstraint("merchant_id", "from_unit", "to_unit", "sku_id", name="uq_unit_conv"),
    )


class Supplier(Base):
    """供应商档案。支撑采购闭环、欠款、比价、质量评分。

    §4.2: 供应商档案、联系方式、地址、经营品类、历史报价、最小起订量、
    预计到货时间、默认账期、证照和凭证、缺斤率、退货率、质量问题率、准时率、
    综合评分、停用和黑名单。
    """

    __tablename__ = "suppliers"

    id: Mapped[uuid.UUID] = mapped_column(sa.Uuid, primary_key=True, default=uuid.uuid4)
    merchant_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid, sa.ForeignKey("merchants.id"), nullable=False
    )
    name: Mapped[str] = mapped_column(sa.String(50), nullable=False)  # 老王
    contact: Mapped[str | None] = mapped_column(sa.String(50))
    address: Mapped[str | None] = mapped_column(sa.String(200))
    business_category: Mapped[str | None] = mapped_column(sa.String(100))  # 经营品类
    min_order_qty: Mapped[Decimal | None] = mapped_column(sa.Numeric(10, 2))
    lead_time_hours: Mapped[int | None] = mapped_column(sa.Integer)
    default_credit_days: Mapped[int | None] = mapped_column(sa.Integer)  # 默认账期(天)
    certificates: Mapped[str | None] = mapped_column(sa.Text)  # 证照/凭证 JSON
    # --- 质量评分 (§4.2) ---
    shortage_rate: Mapped[Decimal | None] = mapped_column(
        sa.Numeric(5, 2)
    )  # 缺斤率 0.00-100.00
    return_rate: Mapped[Decimal | None] = mapped_column(
        sa.Numeric(5, 2)
    )  # 退货率 0.00-100.00
    quality_issue_rate: Mapped[Decimal | None] = mapped_column(
        sa.Numeric(5, 2)
    )  # 质量问题率 0.00-100.00
    on_time_rate: Mapped[Decimal | None] = mapped_column(
        sa.Numeric(5, 2)
    )  # 准时率 0.00-100.00
    composite_score: Mapped[Decimal | None] = mapped_column(
        sa.Numeric(5, 2)
    )  # 综合评分 0.00-100.00
    total_orders: Mapped[int] = mapped_column(sa.Integer, default=0)  # 累计采购批次数
    is_active: Mapped[bool] = mapped_column(sa.Boolean, default=True)
    is_blacklisted: Mapped[bool] = mapped_column(sa.Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(sa.DateTime, server_default=sa.func.now())


class SupplierProduct(Base):
    """某供应商对某 SKU 的近期报价与起订量，支撑比价与采购预测。"""

    __tablename__ = "supplier_products"

    id: Mapped[uuid.UUID] = mapped_column(sa.Uuid, primary_key=True, default=uuid.uuid4)
    merchant_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid, sa.ForeignKey("merchants.id"), nullable=False
    )
    supplier_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid, sa.ForeignKey("suppliers.id"), nullable=False
    )
    sku_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid, sa.ForeignKey("product_skus.id"), nullable=False
    )
    last_price: Mapped[Decimal | None] = mapped_column(sa.Numeric(10, 2))
    min_order_qty: Mapped[Decimal | None] = mapped_column(sa.Numeric(10, 2))
    updated_at: Mapped[datetime] = mapped_column(
        sa.DateTime, server_default=sa.func.now(), onupdate=sa.func.now()
    )


class PriceHistory(Base):
    """售价变更流水 — 支撑 AI 一键改价追踪（战略文档 #7）与历史比价。

    为什么需要独立流水而不是只改 ProductSKU.default_sale_price：
    - 「改价」本身是经营动作，必须可审计、可复盘（改了没？改了多少？为什么？）。
    - AI 建议「立即改价」点击后，应落一条 PriceHistory(reason='ai_discount')，
      否则建议系统永远不知道自己的建议到底有没有被执行、效果如何。
    - 金额用 Decimal/NUMERIC，禁止 float（红线 #7）。
    """

    __tablename__ = "price_history"

    id: Mapped[uuid.UUID] = mapped_column(sa.Uuid, primary_key=True, default=uuid.uuid4)
    merchant_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid, sa.ForeignKey("merchants.id"), nullable=False
    )
    sku_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid, sa.ForeignKey("product_skus.id"), nullable=False
    )
    old_price: Mapped[Decimal] = mapped_column(sa.Numeric(10, 2), nullable=False)
    new_price: Mapped[Decimal] = mapped_column(sa.Numeric(10, 2), nullable=False)
    reason: Mapped[str | None] = mapped_column(
        sa.String(50)
    )  # ai_discount / manual / clear_stock / supplier_cost
    source: Mapped[str] = mapped_column(sa.String(20), default="manual")
    changed_by: Mapped[str | None] = mapped_column(sa.String(50))  # merchant / employee / ai
    created_at: Mapped[datetime] = mapped_column(sa.DateTime, server_default=sa.func.now())
