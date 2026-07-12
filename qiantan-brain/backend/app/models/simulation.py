"""Simulation record model."""

import uuid
from datetime import datetime

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class SimulationRecord(Base):
    __tablename__ = "simulation_records"

    id: Mapped[uuid.UUID] = mapped_column(sa.Uuid, primary_key=True, default=uuid.uuid4)
    merchant_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid, sa.ForeignKey("merchants.id"), nullable=False
    )
    product_id: Mapped[int] = mapped_column(
        sa.Integer, sa.ForeignKey("product_categories.id"), nullable=False
    )
    input_params: Mapped[dict] = mapped_column(sa.JSON, default=dict)
    output_result: Mapped[dict] = mapped_column(sa.JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(sa.DateTime, server_default=sa.func.now())
