"""Environment record model."""

from datetime import date, datetime

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class EnvironmentRecord(Base):
    __tablename__ = "environment_records"

    id: Mapped[int] = mapped_column(sa.Integer, primary_key=True, autoincrement=True)
    date: Mapped[date] = mapped_column(sa.Date, nullable=False)
    city: Mapped[str] = mapped_column(sa.String(50), default="上海")
    temp_high: Mapped[float | None] = mapped_column(sa.Numeric(5, 1))
    temp_low: Mapped[float | None] = mapped_column(sa.Numeric(5, 1))
    weather_type: Mapped[str | None] = mapped_column(sa.String(30))
    rainfall_prob: Mapped[float | None] = mapped_column(sa.Numeric(5, 1))
    is_holiday: Mapped[bool] = mapped_column(sa.Boolean, default=False)
    holiday_name: Mapped[str | None] = mapped_column(sa.String(50))
    day_of_week: Mapped[int | None] = mapped_column(sa.Integer)
    is_weekend: Mapped[bool] = mapped_column(sa.Boolean, default=False)
    special_event: Mapped[str | None] = mapped_column(sa.String(100))
    fetched_at: Mapped[datetime] = mapped_column(sa.DateTime, server_default=sa.func.now())

    __table_args__ = (sa.UniqueConstraint("date", "city"),)
