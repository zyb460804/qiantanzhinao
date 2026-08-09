"""AI Actions 路由的 Pydantic 请求/响应模型。"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from app.schemas.common import ApiResponse, PaginatedResponse


class ActionItem(BaseModel):
    id: str
    action_type: str
    title: str
    payload: dict | None = None
    created_at: str | None = None

    model_config = {"from_attributes": True}


class ActionHistoryItem(ActionItem):
    status: str
    result: dict | None = None
    executed_at: str | None = None


class ActionExecuted(BaseModel):
    id: str
    status: str
    executed_at: str | None = None


class ExecuteActionRequest(BaseModel):
    status: str = "executed"  # executed / failed / rejected
    result: dict | None = None
    executed_by: str = "merchant"


class ActionInput(BaseModel):
    """单个 AI 动作的强类型入参 —— 取代 generate_actions 端点的裸 dict。"""

    action_type: Literal["price", "purchase", "clearance", "lock_batch"]
    title: str = Field(min_length=1, max_length=100)
    payload: dict | None = Field(default=None)


class GenerateActionsRequest(BaseModel):
    """批量生成 AI 动作的请求信封。"""

    actions: list[ActionInput] = Field(default_factory=list, max_length=20)


# ── 响应信封类型别名 ──────────────────────────────────────

PendingActionsResponse = ApiResponse[list[ActionItem]]
ExecuteActionResponse = ApiResponse[ActionExecuted]
ActionHistoryResponse = PaginatedResponse[ActionHistoryItem]
