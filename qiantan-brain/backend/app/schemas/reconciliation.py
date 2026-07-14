"""Schemas for payment-channel bill reconciliation."""

from typing import Literal

from pydantic import BaseModel, Field


class ResolveDifferenceRequest(BaseModel):
    status: Literal["resolved", "ignored"] = "resolved"
    resolution: str = Field(min_length=2, max_length=200)
