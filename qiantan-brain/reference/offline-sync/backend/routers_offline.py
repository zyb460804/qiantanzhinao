"""
routers_offline.py — 离线同步接收接口（参考实现 / 教学样例）
────────────────────────────────────────────────────────────────────────
对应 PRD §4.1.5 / P0-4：小程序联网后把离线队列批量上送，后端逐条幂等落库。

设计要点：
  - 入参用 Pydantic 模型（不要裸 dict！见 team-code-quality-guidance §三.4），
    缺失字段由 Pydantic 直接返回 422，而不是在业务逻辑里 KeyError → 500。
  - 逐条处理、逐条返回结果（id/duplicate/conflict），前端据此更新队列状态。
  - 跨商户的 client_id 在本商户查不到 → 返回 404（隔离生效），绝不返回他人数据。
  - 全程要求 merchant_id 与调用方身份一致（生产应来自鉴权 token，而非客户端自报）。
"""

from __future__ import annotations

import uuid
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.audit import AuditLog
from .offline_sync_service import upsert_offline_record, ConflictError


router = APIRouter(prefix="/api/v1/offline", tags=["offline"])


class OfflineItemIn(BaseModel):
    client_id: str = Field(..., min_length=1, description="客户端生成的 UUID v4 幂等键")
    merchant_id: uuid.UUID
    kind: str = Field(..., pattern="^(voice_text|voice_audio|cashier|vision|purchase_confirm)$")
    payload: dict


class OfflineSyncIn(BaseModel):
    items: list[OfflineItemIn]


class ItemResult(BaseModel):
    client_id: str
    status: str  # created | duplicate | conflict
    record_id: Optional[str] = None
    message: str = ""


@router.post("/sync", response_model=dict)
async def sync_offline(body: OfflineSyncIn, db: AsyncSession = Depends(get_db)):
    results: list[ItemResult] = []
    for item in body.items:
        try:
            res = await upsert_offline_record(
                db,
                merchant_id=item.merchant_id,
                client_id=item.client_id,
                kind=item.kind,
                payload=item.payload,
                source="offline",
            )
            results.append(ItemResult(
                client_id=res.client_id, status=res.status,
                record_id=str(res.record_id) if res.record_id else None,
                message=res.message,
            ))
        except ConflictError as e:
            # 业务冲突：返回 conflict，前端标记该条并提示用户（R6 冲突中心）
            results.append(ItemResult(client_id=e.client_id, status="conflict", message=e.message))

    return {"code": 0, "data": {"results": [r.model_dump() for r in results]}}


# 冲突中心（R6）数据源：列出本商户所有处于 conflict 的离线项
@router.get("/conflicts", response_model=dict)
async def list_conflicts(merchant_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    # 真实实现：查离线冲突表 / 队列中 status='conflict' 的项（此处省略存储细节）
    return {"code": 0, "data": {"conflicts": []}}
