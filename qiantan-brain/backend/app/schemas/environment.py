"""Environment 路由的 Pydantic 响应模型。"""

from __future__ import annotations

from pydantic import BaseModel


class EnvRecordData(BaseModel):
    date: str
    city: str
    temp_high: float | None = None
    temp_low: float | None = None
    weather_type: str | None = None
    rainfall_prob: float | None = None
    is_holiday: bool = False
    holiday_name: str | None = None
    day_of_week: int | None = None
    is_weekend: bool = False


class TodayEnvEnvelope(BaseModel):
    code: int = 0
    data: EnvRecordData
    source: str = "unknown"


class ForecastEnvelope(BaseModel):
    code: int = 0
    data: list[dict]
    source: str = "unknown"


class HistoryEnvelope(BaseModel):
    code: int = 0
    data: list[dict]
    count: int = 0
