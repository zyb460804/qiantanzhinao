"""Pydantic schemas for simulation."""

from uuid import UUID

from pydantic import BaseModel


class ScenarioRequest(BaseModel):
    merchant_id: UUID
    simulations: list[dict]  # multiple what-if scenarios


class SimulationHistoryItem(BaseModel):
    id: UUID
    product_name: str
    input_params: dict
    output_result: dict
    created_at: str
