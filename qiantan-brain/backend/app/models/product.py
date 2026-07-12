"""Product category model."""

from decimal import Decimal

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class ProductCategory(Base):
    __tablename__ = "product_categories"

    id: Mapped[int] = mapped_column(sa.Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(sa.String(50), unique=True, nullable=False)
    unit: Mapped[str] = mapped_column(sa.String(20), nullable=False, default="斤")
    default_price: Mapped[Decimal | None] = mapped_column(sa.Numeric(10, 2))
    shelf_life_hours: Mapped[int] = mapped_column(sa.Integer, nullable=False)
    category_group: Mapped[str | None] = mapped_column(sa.String(30))
    is_active: Mapped[bool] = mapped_column(sa.Boolean, default=True)
