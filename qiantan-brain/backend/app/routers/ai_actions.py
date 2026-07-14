"""AI 行动执行 API (section 4.11) — 一键改价/生成采购单/清货/锁定批次。

核心升级：执行动作时调用真实业务服务，不只改状态。每个执行写 PriceHistory + AuditLog。
"""

from __future__ import annotations

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
from app.schemas.common import AnyResponse


router = APIRouter(prefix="/api/v1/ai-actions", tags=["ai-actions"])


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
        action.executed_by = body.get("executed_by", "merchant")
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
        action.status = "failed"
        action.result = {"error": str(e)}
        action.executed_at = utc_now()
        await db.commit()
        raise HTTPException(status_code=500, detail=f"执行失败: {e}") from e


@router.post("/generate", response_model=AnyResponse)
async def generate_actions(
    body: dict,
    merchant: Merchant = Depends(get_current_merchant),
    db: AsyncSession = Depends(get_db),
):
    """Generate AI actions from analysis (called by the advice engine).

    Body: {actions: [{action_type, title, payload}]}
    """
    actions_data = body.get("actions", [])
    created = []
    for a in actions_data:
        action = AIAction(
            merchant_id=merchant.id,
            action_type=a["action_type"],
            title=a["title"],
            payload=a.get("payload"),
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
