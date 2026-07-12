"""通用 API 响应信封 — 所有路由统一使用，替代 response_model=dict。"""

from typing import Any, Generic, TypeVar

from pydantic import BaseModel, Field


T = TypeVar("T")


class ApiResponse(BaseModel, Generic[T]):
    """标准 API 响应信封 — data 强类型，用于响应结构固定的路由。"""

    code: int = 0
    message: str = ""
    data: T | None = None


class AnyResponse(BaseModel):
    """灵活响应信封 — data 可以是任意类型（dict/list/str/None）。

    用于响应结构不固定或渐进迁移中的路由。
    """

    code: int = 0
    message: str = ""
    data: Any = None


class PaginatedMeta(BaseModel):
    page: int
    limit: int
    total: int | None = None


class PaginatedResponse(BaseModel, Generic[T]):
    """带分页元数据的响应信封。"""

    code: int = 0
    message: str = ""
    data: list[T] = Field(default_factory=list)
    meta: PaginatedMeta | None = None
