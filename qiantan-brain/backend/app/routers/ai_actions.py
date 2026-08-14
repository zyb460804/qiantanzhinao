"""AI 行动执行 API (section 4.11) — 一键改价/生成采购单/清货/锁定批次。

核心升级：执行动作时调用真实业务服务，不只改状态。每个执行写 PriceHistory + AuditLog。
执行入口按 action_type 映射员工权限点（审计 M-1）：无权限角色 403。
"""

from __future__ import annotations

import logging
import uuid
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import get_current_merchant
from app.core.timezone import utc_now
from app.database import get_db
from app.models.ai_action import AIAction
from app.models.audit import AuditLog
from app.models.catalog import PriceHistory, ProductSKU
from app.models.merchant import Merchant
from app.models.purchase import PurchaseItem, PurchaseList
from app.models.staff import ROLE_PERMISSIONS
from app.schemas.ai_action import GenerateActionsRequest
from app.schemas.common import AnyResponse


logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/ai-actions", tags=["ai-actions"])

# 审计 M-1：action_type → 员工权限点映射（权限常量见 app/models/staff.py ROLE_PERMISSIONS）。
# 执行 AI 建议等同于直接做对应业务操作，必须过同一套角色权限门禁：
#   price/clearance 都是改价 → change_price；purchase 生成采购单 → purchase_confirm；
#   lock_batch 锁定批次扣减可用库存 → inventory_adjust；
#   未知类型兜底 view_profit（含经营数据查看的最弱权限，宁拒勿放）。
_ACTION_PERMISSION: dict[str, str] = {
    "price": "change_price",
    "clearance": "change_price",
    "purchase": "purchase_confirm",
    "lock_batch": "inventory_adjust",
}
_DEFAULT_ACTION_PERMISSION = "view_profit"


def _required_permission_for(action_type: str) -> str:
    """返回执行某类 AI 动作所需的权限点。"""
    return _ACTION_PERMISSION.get(action_type, _DEFAULT_ACTION_PERMISSION)


def _ensure_action_permission(merchant: Merchant, action_type: str) -> None:
    """校验当前角色可执行该 action_type，否则 403。

    role 取自 get_current_merchant 注入的 token claim（_token_role），
    owner/manager 拥有全部映射权限点，不受影响；cashier 等低权角色被拦截。
    """
    role = getattr(merchant, "_token_role", None) or "owner"
    permission = _required_permission_for(action_type)
    if permission not in ROLE_PERMISSIONS.get(role, set()):
        raise HTTPException(
            status_code=403,
            detail=f"当前角色（{role}）无权执行「{action_type}」类动作，需要「{permission}」权限",
        )


@router.get("/pending", response_model=AnyResponse)
async def list_pending(
    merchant: Merchant = Depends(get_current_merchant),
    db: AsyncSession = Depends(get_db),
):
    actions = (
        (
            await db.execute(
                select(AIAction)
                .where(AIAction.merchant_id == merchant.id, AIAction.status == "pending")
                .order_by(AIAction.created_at.desc())
            )
        )
        .scalars()
        .all()
    )
    return {
        "code": 0,
        "data": [
            {
                "id": str(a.id),
                "action_type": a.action_type,
                "title": a.title,
                "payload": a.payload,
                "created_at": a.created_at.isoformat() if a.created_at else None,
            }
            for a in actions
        ],
    }


@router.get("/history", response_model=AnyResponse)
async def list_history(
    page: int = 1,
    limit: int = 20,
    merchant: Merchant = Depends(get_current_merchant),
    db: AsyncSession = Depends(get_db),
):
    offset = (page - 1) * limit
    actions = (
        (
            await db.execute(
                select(AIAction)
                .where(AIAction.merchant_id == merchant.id)
                .order_by(AIAction.created_at.desc())
                .offset(offset)
                .limit(limit)
            )
        )
        .scalars()
        .all()
    )
    return {
        "code": 0,
        "data": [
            {
                "id": str(a.id),
                "action_type": a.action_type,
                "title": a.title,
                "status": a.status,
                "payload": a.payload,
                "result": a.result,
                "created_at": a.created_at.isoformat() if a.created_at else None,
                "executed_at": a.executed_at.isoformat() if a.executed_at else None,
            }
            for a in actions
        ],
    }


@router.post("/{action_id}/execute", response_model=AnyResponse)
async def execute_action(
    action_id: uuid.UUID,
    body: dict | None = None,
    merchant: Merchant = Depends(get_current_merchant),
    db: AsyncSession = Depends(get_db),
):
    """Execute an AI action with real business side effects.

    action_type determines the actual operation:
      - price: update SKU default_sale_price + write PriceHistory
      - purchase: generate purchase list from payload
      - clearance: apply discount to SKU price
      - lock_batch: lock a batch via food_safety service
    """
    body = body or {}
    action = await db.scalar(
        select(AIAction).where(AIAction.id == action_id, AIAction.merchant_id == merchant.id)
    )
    if not action:
        raise HTTPException(status_code=404, detail="动作不存在")
    if action.status != "pending":
        raise HTTPException(status_code=409, detail=f"动作状态为 {action.status}，不可执行")

    # 审计 M-1：按 action_type 过员工权限门禁（拒绝/执行都会变更动作状态，一并拦截）
    _ensure_action_permission(merchant, action.action_type)

    status = body.get("status", "executed")
    if status == "rejected":
        action.status = "rejected"
        action.executed_at = utc_now()
        await db.commit()
        return {
            "code": 0,
            "message": "已拒绝",
            "data": {"id": str(action.id), "status": "rejected"},
        }

    result_data: dict = {}
    payload = action.payload or {}

    try:
        if action.action_type == "price":
            # 一键改价：更新 SKU 售价 + 写 PriceHistory
            sku_id = uuid.UUID(payload["sku_id"]) if payload.get("sku_id") else None
            new_price = Decimal(str(payload.get("new_price", 0)))
            if not sku_id or new_price <= 0:
                raise ValueError("缺少 sku_id 或 new_price")

            sku = await db.get(ProductSKU, sku_id)
            if not sku or sku.merchant_id != merchant.id:
                raise ValueError("SKU 不存在")
            old_price = sku.default_sale_price or Decimal("0")
            sku.default_sale_price = new_price
            db.add(
                PriceHistory(
                    merchant_id=merchant.id,
                    sku_id=sku.id,
                    old_price=old_price,
                    new_price=new_price,
                    reason="ai_discount",
                    source="ai",
                    changed_by="ai_action",
                )
            )
            result_data = {
                "sku_id": str(sku_id),
                "old_price": float(old_price),
                "new_price": float(new_price),
                "sku_name": sku.name,
            }

        elif action.action_type == "purchase":
            # 生成采购单
            items = payload.get("items", [])
            if not items:
                raise ValueError("采购清单为空")
            plist = PurchaseList(
                merchant_id=merchant.id,
                status="draft",
                total_estimated_cost=Decimal(str(payload.get("total_cost", 0))),
                item_count=len(items),
            )
            db.add(plist)
            await db.flush()
            for item in items:
                db.add(
                    PurchaseItem(
                        list_id=plist.id,
                        merchant_id=merchant.id,
                        product_id=item["product_id"],
                        actual_qty=Decimal(str(item.get("qty", 0))),
                        unit=item.get("unit", "斤"),
                        estimated_unit_cost=Decimal(str(item.get("cost", 0))),
                        status="pending",
                    )
                )
            await db.flush()
            result_data = {"list_id": str(plist.id), "item_count": len(items)}

        elif action.action_type == "clearance":
            # 临期清货：批量为多个 SKU 降价
            sku_updates = payload.get("skus", [])
            updated = []
            for s in sku_updates:
                sku = await db.get(ProductSKU, uuid.UUID(s["sku_id"]))
                if sku and sku.merchant_id == merchant.id:
                    old = sku.default_sale_price or Decimal("0")
                    new = Decimal(str(s["new_price"]))
                    sku.default_sale_price = new
                    db.add(
                        PriceHistory(
                            merchant_id=merchant.id,
                            sku_id=sku.id,
                            old_price=old,
                            new_price=new,
                            reason="clearance",
                            source="ai",
                            changed_by="ai_action",
                        )
                    )
                    updated.append(
                        {
                            "sku_id": str(sku.id),
                            "name": sku.name,
                            "old_price": float(old),
                            "new_price": float(new),
                        }
                    )
            result_data = {"updated": len(updated), "skus": updated}

        elif action.action_type == "lock_batch":
            batch_id = uuid.UUID(payload["batch_id"])
            from app.services.batch import lock_batch as do_lock

            batch = await do_lock(
                db,
                batch_id,
                merchant.id,
                reason=payload.get("reason", "AI检测到食品安全风险"),
                locked_by="ai_action",
            )
            result_data = {
                "batch_id": str(batch.id),
                "status": "locked",
                "remaining_qty": float(batch.remaining_qty),
            }

        else:
            raise ValueError(f"不支持的动作类型: {action.action_type}")

        action.status = "executed"
        action.result = result_data
        # 身份只来自可信凭证（token）：不再从 body 读取 executed_by，
        # 防止客户端伪造操作人。staff_id 在 get_current_merchant 中由 JWT 注入。
        staff_id = getattr(merchant, "_token_staff_id", None)
        action.executed_by = str(staff_id) if staff_id else "merchant"
        action.executed_at = utc_now()

        db.add(
            AuditLog(
                merchant_id=merchant.id,
                action=f"ai_{action.action_type}",
                target_table="ai_actions",
                target_id=str(action.id),
                after_data=result_data,
                reason=action.title,
                operator="ai",
            )
        )
        await db.commit()

        return {
            "code": 0,
            "message": f"已执行: {action.title}",
            "data": {"id": str(action.id), "status": "executed", "result": result_data},
        }

    except ValueError as e:
        action.status = "failed"
        action.result = {"error": str(e)}
        action.executed_at = utc_now()
        await db.commit()
        raise HTTPException(status_code=400, detail=str(e)) from e
    except Exception as e:
        # 审计 M-1：内部异常不回传客户端（堆栈/驱动信息可被用于探测）。
        # 详情固定文案 + request_id 供客服对账；完整堆栈只进服务端日志与动作记录。
        request_id = uuid.uuid4().hex[:12]
        action.status = "failed"
        action.result = {"error": str(e), "request_id": request_id}
        action.executed_at = utc_now()
        await db.commit()
        logger.exception(
            "ai action execute failed: request_id=%s action=%s merchant=%s",
            request_id,
            action.id,
            merchant.id,
        )
        raise HTTPException(
            status_code=500,
            detail=f"执行失败，请稍后重试；如需反馈请提供追踪号 {request_id}",
        ) from e


@router.post("/generate", response_model=AnyResponse)
async def generate_actions(
    body: GenerateActionsRequest,
    merchant: Merchant = Depends(get_current_merchant),
    db: AsyncSession = Depends(get_db),
):
    """Generate AI actions from analysis (called by the advice engine).

    Body: {actions: [{action_type, title, payload}]} — 强类型校验，
    action_type 仅允许 price/purchase/clearance/lock_batch，title 长度 1-100，
    单次最多 20 条。
    """
    created = []
    for a in body.actions:
        action = AIAction(
            merchant_id=merchant.id,
            action_type=a.action_type,
            title=a.title,
            payload=a.payload,
        )
        db.add(action)
        created.append(action)
    await db.commit()
    return {
        "code": 0,
        "message": f"已生成 {len(created)} 个动作",
        "data": [
            {"id": str(a.id), "action_type": a.action_type, "title": a.title} for a in created
        ],
    }
