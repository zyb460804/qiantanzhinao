"""设备管理与价目屏请求模型。"""

import uuid
from typing import Literal

from pydantic import BaseModel, Field, field_validator


DeviceType = Literal["scale", "camera", "esl", "printer"]
PriceSource = Literal["manual", "ai_discount", "clearance"]


class RegisterDeviceRequest(BaseModel):
    device_type: DeviceType
    device_name: str = Field(min_length=1, max_length=50)
    serial_number: str | None = Field(default=None, max_length=64)
    firmware_version: str | None = Field(default=None, max_length=20)
    config: str | None = Field(default=None, max_length=10000)

    @field_validator("device_name", "serial_number", "firmware_version", mode="before")
    @classmethod
    def strip_text(cls, value):
        if value is None:
            return None
        if not isinstance(value, str):
            return value
        value = value.strip()
        return value or None


class DeviceHeartbeatRequest(BaseModel):
    error: str | None = Field(default=None, max_length=200)
    firmware_version: str | None = Field(default=None, max_length=20)


class SyncPriceDisplayRequest(BaseModel):
    sku_ids: list[uuid.UUID] = Field(default_factory=list, max_length=1000)
    source: PriceSource = "manual"
