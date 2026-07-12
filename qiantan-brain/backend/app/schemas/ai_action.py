"""AI Actions 路由的 Pydantic 请求/响应模型。"""

from __future__ import annotations

from pydantic import BaseModel

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


# ── 响应信封类型别名 ──────────────────────────────────────

PendingActionsResponse = ApiResponse[list[ActionItem]]
ExecuteActionResponse = ApiResponse[ActionExecuted]
ActionHistoryResponse = PaginatedResponse[ActionHistoryItem]
