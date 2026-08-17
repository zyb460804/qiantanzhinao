"""市场管理后台 API (section 4.18)."""

import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import get_current_merchant
from app.core.timezone import utc_now
from app.database import get_db
from app.models.market import (
    Market,
    MarketComplaint,
    MarketInspection,
    MarketMerchant,
    MarketNotice,
)
from app.models.merchant import Merchant
from app.schemas.common import AnyResponse


router = APIRouter(prefix="/api/v1/market-admin", tags=["market-admin"])


class CreateInspectionRequest(BaseModel):
    market_id: uuid.UUID
    inspector: str
    inspection_type: str = "food_safety"
    result: str = "pass"
    notes: str | None = None
    photos: str | None = None
    merchant_id: uuid.UUID | None = None


class CreateComplaintRequest(BaseModel):
    market_id: uuid.UUID
    complaint_type: str
    description: str
    complainant: str | None = None
    merchant_id: uuid.UUID | None = None


class ResolveComplaintRequest(BaseModel):
    resolution: str = ""


async def _require_market_admin_role(merchant: Merchant) -> None:
    """校验当前操作者具备市场/租户/平台管理员角色。"""
    role = getattr(merchant, "_token_role", None) or "owner"
    if role not in ("market_admin", "tenant_admin", "platform_admin"):
        raise HTTPException(status_code=403, detail="仅市场/租户管理员可操作")


# ═══ 市场 ═══


@router.get("/markets", response_model=AnyResponse)
async def list_markets(
    merchant: Merchant = Depends(get_current_merchant), db: AsyncSession = Depends(get_db)
):
    """获取市场列表 — 限定为当前商户已入场（MarketMerchant 关联）的市场。

    修复（审计 P1-加载通知列表数据泄露）：
    原实现返回系统中所有 active 市场，导致前端拉到全平台无关市场的通知。
    现通过 MarketMerchant 关联表只返回与当前商户关联的市场。
    """
    rows = (
        (
            await db.execute(
                select(Market)
                .join(MarketMerchant, MarketMerchant.market_id == Market.id)
                .where(
                    MarketMerchant.merchant_id == merchant.id,
                    Market.is_active.is_(True),
                )
            )
        )
        .scalars()
        .all()
    )  # noqa: E712
    return {
        "code": 0,
        "data": [{"market_id": str(m.id), "name": m.name, "address": m.address} for m in rows],
    }


async def _require_market_member(
    db: AsyncSession, merchant_id: uuid.UUID, market_id: uuid.UUID
) -> None:
    """校验商户是否属于指定市场（通过 MarketMerchant 关联表）。

    修复（审计 P1-市场管理 API 权限语义错位）：原 /market-admin 写操作
    仅校验 get_current_merchant，任何登录摊贩都能创建市场通知；现对涉及
    market_id 的写操作（notices / inspections / complaints）校验该商户
    确实属于目标市场。create_market 因尚无"市场管理员"角色概念，暂加 TODO。
    """
    mm = (
        await db.execute(
            select(MarketMerchant).where(
                MarketMerchant.merchant_id == merchant_id,
                MarketMerchant.market_id == market_id,
            )
        )
    ).scalar_one_or_none()
    if mm is None:
        raise HTTPException(status_code=403, detail="该商户不属于此市场，无权操作")


@router.post("/markets", response_model=AnyResponse)
async def create_market(
    body: dict,
    merchant: Merchant = Depends(get_current_merchant),
    db: AsyncSession = Depends(get_db),
):
    """创建市场 — 当前仅允许 owner 创建。

    TODO（审计 P1-市场管理 API 权限语义错位）：路由前缀叫 /market-admin，
    但目前没有独立的"市场管理员"角色与平台运营方 SaaS 上下文。在引入
    platform_admin / market_admin 角色与权限校验前，临时限制为 owner 才能
    创建市场，避免任意摊贩随手创建脏数据。后续应：
      1. 新增 platform_admin / market_admin 角色与登录链路
      2. 通过 OAuth/邀请码校验后才能创建市场
    """
    # 修复（审计 C-3）：角色从 token claim 读取（_token_role），不再依赖 DB 列。
    # wechat_login 硬编码 role="owner"，原 getattr(merchant, "role", ...) 对所有
    # 微信登录用户恒为 "owner" → 等价于无权限校验。摊主不是市场管理员，故将
    # "owner" 从允许角色中移除。
    role = getattr(merchant, "_token_role", None) or "owner"
    if role not in ("market_admin", "tenant_admin", "platform_admin"):
        raise HTTPException(status_code=403, detail="仅市场/租户管理员可操作")
    m = Market(name=body["name"], address=body.get("address"), contact=body.get("contact"))
    db.add(m)
    await db.commit()
    await db.refresh(m)
    return {"code": 0, "data": {"market_id": str(m.id), "name": m.name}}


# ═══ 商户入场 ═══


@router.get("/merchants", response_model=AnyResponse)
async def list_market_merchants(
    market_id: uuid.UUID,
    merchant: Merchant = Depends(get_current_merchant),
    db: AsyncSession = Depends(get_db),
):
    # 校验当前商户属于该市场（审计 P0-1：原实现任意商户可枚举任意市场商户）
    await _require_market_member(db, merchant.id, market_id)
    rows = (
        (await db.execute(select(MarketMerchant).where(MarketMerchant.market_id == market_id)))
        .scalars()
        .all()
    )
    return {
        "code": 0,
        "data": [
            {
                "id": str(mm.id),
                "merchant_id": str(mm.merchant_id),
                "stall_number": mm.stall_number,
                "category": mm.category,
                "food_safety_score": mm.food_safety_score,
                "status": mm.status,
            }
            for mm in rows
        ],
    }


@router.post("/merchants", response_model=AnyResponse)
async def register_merchant(
    body: dict,
    merchant: Merchant = Depends(get_current_merchant),
    db: AsyncSession = Depends(get_db),
):
    # 修复（审计 C-3）：角色从 token claim 读取，owner 不在允许列表中。
    role = getattr(merchant, "_token_role", None) or "owner"
    if role not in ("market_admin", "tenant_admin", "platform_admin"):
        raise HTTPException(status_code=403, detail="仅市场/租户管理员可操作")
    market_id = uuid.UUID(body["market_id"])
    await _require_market_member(db, merchant.id, market_id)
    # 修复（审计 C-10）：跨租户保护 — 目标商户必须与操作者同租户。
    # 原实现允许 market_admin 把任意租户的商户塞进自己市场，破坏 SaaS 行级隔离。
    target = await db.get(Merchant, uuid.UUID(body["merchant_id"]))
    if target is None:
        raise HTTPException(status_code=404, detail="目标商户不存在")
    # 修复（F4 双 None 误判）：原 `!=` 比较在双方 tenant_id 均为 None 时返回 False
    # （None == None 为 True），校验通过。存量数据 tenant_id 可为 None，破坏隔离。
    # 改为 fail-closed：任一方为 None 即拒绝。
    merchant_tid = getattr(merchant, "tenant_id", None)
    target_tid = getattr(target, "tenant_id", None)
    if merchant_tid is None or target_tid is None or merchant_tid != target_tid:
        raise HTTPException(status_code=403, detail="跨租户操作禁止")
    mm = MarketMerchant(
        market_id=uuid.UUID(body["market_id"]),
        merchant_id=uuid.UUID(body["merchant_id"]),
        stall_number=body.get("stall_number"),
        category=body.get("category"),
        license_number=body.get("license_number"),
    )
    db.add(mm)
    await db.commit()
    return {"code": 0, "data": {"id": str(mm.id), "stall_number": mm.stall_number}}


# ═══ 巡检 ═══


@router.get("/inspections", response_model=AnyResponse)
async def list_inspections(
    market_id: uuid.UUID,
    limit: int = 30,
    merchant: Merchant = Depends(get_current_merchant),
    db: AsyncSession = Depends(get_db),
):
    # 校验当前商户属于该市场（审计 P0-1）
    await _require_market_member(db, merchant.id, market_id)
    rows = (
        (
            await db.execute(
                select(MarketInspection)
                .where(MarketInspection.market_id == market_id)
                .order_by(MarketInspection.created_at.desc())
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
                "id": str(i.id),
                "inspector": i.inspector,
                "type": i.inspection_type,
                "result": i.result,
                "notes": i.notes,
                "created_at": i.created_at.isoformat() if i.created_at else None,
            }
            for i in rows
        ],
    }


@router.post("/inspections", response_model=AnyResponse)
async def create_inspection(
    body: CreateInspectionRequest,
    merchant: Merchant = Depends(get_current_merchant),
    db: AsyncSession = Depends(get_db),
):
    # 修复（审计 R1）：写巡检必须具有市场/租户/平台管理员角色。
    await _require_market_admin_role(merchant)
    market_id = body.market_id
    # 校验当前商户属于该市场（审计 P1-权限语义错位）
    await _require_market_member(db, merchant.id, market_id)
    # 修复（审计 R1）：若指定目标商户，必须校验其属于同一市场。
    if body.merchant_id is not None:
        await _require_market_member(db, body.merchant_id, market_id)
    i = MarketInspection(
        market_id=market_id,
        inspector=body.inspector,
        inspection_type=body.inspection_type,
        result=body.result,
        notes=body.notes,
        photos=body.photos,
        merchant_id=body.merchant_id,
    )
    db.add(i)
    await db.commit()
    return {"code": 0, "data": {"id": str(i.id), "result": i.result}}


# ═══ 投诉 ═══


@router.get("/complaints", response_model=AnyResponse)
async def list_complaints(
    market_id: uuid.UUID,
    status: str | None = None,
    merchant: Merchant = Depends(get_current_merchant),
    db: AsyncSession = Depends(get_db),
):
    # 校验当前商户属于该市场（审计 P0-1）
    await _require_market_member(db, merchant.id, market_id)
    filters = [MarketComplaint.market_id == market_id]
    if status:
        filters.append(MarketComplaint.status == status)
    rows = (
        (
            await db.execute(
                select(MarketComplaint)
                .where(*filters)
                .order_by(MarketComplaint.created_at.desc())
                .limit(50)
            )
        )
        .scalars()
        .all()
    )
    return {
        "code": 0,
        "data": [
            {
                "id": str(c.id),
                "complainant": c.complainant,
                "type": c.complaint_type,
                "description": c.description,
                "status": c.status,
                "resolution": c.resolution,
                "created_at": c.created_at.isoformat() if c.created_at else None,
            }
            for c in rows
        ],
    }


@router.post("/complaints", response_model=AnyResponse)
async def create_complaint(
    body: CreateComplaintRequest,
    merchant: Merchant = Depends(get_current_merchant),
    db: AsyncSession = Depends(get_db),
):
    # 修复（审计 R1）：写投诉必须具有市场/租户/平台管理员角色。
    await _require_market_admin_role(merchant)
    market_id = body.market_id
    # 校验当前商户属于该市场（审计 P1-权限语义错位）
    await _require_market_member(db, merchant.id, market_id)
    # 修复（审计 R1）：若指定被投诉商户，必须校验其属于同一市场。
    if body.merchant_id is not None:
        await _require_market_member(db, body.merchant_id, market_id)
    c = MarketComplaint(
        market_id=market_id,
        complainant=body.complainant,
        complaint_type=body.complaint_type,
        description=body.description,
        merchant_id=body.merchant_id,
    )
    db.add(c)
    await db.commit()
    return {"code": 0, "data": {"id": str(c.id), "status": "open"}}


@router.put("/complaints/{complaint_id}/resolve", response_model=AnyResponse)
async def resolve_complaint(
    complaint_id: uuid.UUID,
    body: ResolveComplaintRequest,
    merchant: Merchant = Depends(get_current_merchant),
    db: AsyncSession = Depends(get_db),
):
    # 修复（审计 R1）：处置投诉必须具有市场/租户/平台管理员角色。
    await _require_market_admin_role(merchant)
    c = await db.get(MarketComplaint, complaint_id)
    if not c:
        raise HTTPException(status_code=404, detail="投诉不存在")
    # 校验当前商户属于投诉所在市场（审计 P0-1：原实现任意商户可处置他人投诉）
    await _require_market_member(db, merchant.id, c.market_id)
    c.status = "resolved"
    c.resolution = body.resolution
    c.resolved_at = utc_now()
    await db.commit()
    return {"code": 0, "message": "投诉已处理"}


# ═══ 通知 ═══


@router.get("/notices", response_model=AnyResponse)
async def list_notices(
    market_id: uuid.UUID,
    merchant: Merchant = Depends(get_current_merchant),
    db: AsyncSession = Depends(get_db),
):
    # 校验当前商户属于该市场（审计 P0-1）
    await _require_market_member(db, merchant.id, market_id)
    rows = (
        (
            await db.execute(
                select(MarketNotice)
                .where(MarketNotice.market_id == market_id, MarketNotice.is_active.is_(True))
                .order_by(MarketNotice.created_at.desc())
                .limit(20)
            )
        )
        .scalars()
        .all()
    )  # noqa: E712
    return {
        "code": 0,
        "data": [
            {
                "id": str(n.id),
                "title": n.title,
                "content": n.content,
                "notice_type": n.notice_type,
                "created_at": n.created_at.isoformat() if n.created_at else None,
            }
            for n in rows
        ],
    }


@router.post("/notices", response_model=AnyResponse)
async def create_notice(
    body: dict,
    merchant: Merchant = Depends(get_current_merchant),
    db: AsyncSession = Depends(get_db),
):
    # 修复（审计 C-3）：角色从 token claim 读取，owner 不在允许列表中。
    role = getattr(merchant, "_token_role", None) or "owner"
    if role not in ("market_admin", "tenant_admin", "platform_admin"):
        raise HTTPException(status_code=403, detail="仅市场/租户管理员可操作")
    market_id = uuid.UUID(body["market_id"])
    await _require_market_member(db, merchant.id, market_id)
    n = MarketNotice(
        market_id=market_id,
        title=body["title"],
        content=body["content"],
        notice_type=body.get("notice_type", "info"),
    )
    db.add(n)
    await db.commit()
    return {"code": 0, "data": {"id": str(n.id), "title": n.title}}
