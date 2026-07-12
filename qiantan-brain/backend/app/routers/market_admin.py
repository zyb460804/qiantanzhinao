"""市场管理后台 API (section 4.18)."""

import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import get_current_merchant
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


# ═══ 市场 ═══

@router.get("/markets", response_model=AnyResponse)
async def list_markets(merchant: Merchant = Depends(get_current_merchant), db: AsyncSession = Depends(get_db)):
    rows = (await db.execute(select(Market).where(Market.is_active == True))).scalars().all()  # noqa: E712
    return {"code": 0, "data": [{"market_id": str(m.id), "name": m.name, "address": m.address} for m in rows]}


@router.post("/markets", response_model=AnyResponse)
async def create_market(body: dict, merchant: Merchant = Depends(get_current_merchant), db: AsyncSession = Depends(get_db)):
    m = Market(name=body["name"], address=body.get("address"), contact=body.get("contact"))
    db.add(m); await db.commit(); await db.refresh(m)
    return {"code": 0, "data": {"market_id": str(m.id), "name": m.name}}


# ═══ 商户入场 ═══

@router.get("/merchants", response_model=AnyResponse)
async def list_market_merchants(market_id: uuid.UUID, merchant: Merchant = Depends(get_current_merchant), db: AsyncSession = Depends(get_db)):
    rows = (await db.execute(select(MarketMerchant).where(MarketMerchant.market_id == market_id))).scalars().all()
    return {"code": 0, "data": [{"id": str(mm.id), "merchant_id": str(mm.merchant_id), "stall_number": mm.stall_number, "category": mm.category, "food_safety_score": mm.food_safety_score, "status": mm.status} for mm in rows]}


@router.post("/merchants", response_model=AnyResponse)
async def register_merchant(body: dict, merchant: Merchant = Depends(get_current_merchant), db: AsyncSession = Depends(get_db)):
    mm = MarketMerchant(market_id=uuid.UUID(body["market_id"]), merchant_id=uuid.UUID(body["merchant_id"]),
                        stall_number=body.get("stall_number"), category=body.get("category"),
                        license_number=body.get("license_number"))
    db.add(mm); await db.commit()
    return {"code": 0, "data": {"id": str(mm.id), "stall_number": mm.stall_number}}


# ═══ 巡检 ═══

@router.get("/inspections", response_model=AnyResponse)
async def list_inspections(market_id: uuid.UUID, limit: int = 30, merchant: Merchant = Depends(get_current_merchant), db: AsyncSession = Depends(get_db)):
    rows = (await db.execute(select(MarketInspection).where(MarketInspection.market_id == market_id).order_by(MarketInspection.created_at.desc()).limit(limit))).scalars().all()
    return {"code": 0, "data": [{"id": str(i.id), "inspector": i.inspector, "type": i.inspection_type, "result": i.result, "notes": i.notes, "created_at": i.created_at.isoformat() if i.created_at else None} for i in rows]}


@router.post("/inspections", response_model=AnyResponse)
async def create_inspection(body: dict, merchant: Merchant = Depends(get_current_merchant), db: AsyncSession = Depends(get_db)):
    i = MarketInspection(market_id=uuid.UUID(body["market_id"]), inspector=body["inspector"],
                         inspection_type=body.get("inspection_type", "food_safety"),
                         result=body.get("result", "pass"), notes=body.get("notes"), photos=body.get("photos"))
    if body.get("merchant_id"): i.merchant_id = uuid.UUID(body["merchant_id"])
    db.add(i); await db.commit()
    return {"code": 0, "data": {"id": str(i.id), "result": i.result}}


# ═══ 投诉 ═══

@router.get("/complaints", response_model=AnyResponse)
async def list_complaints(market_id: uuid.UUID, status: str | None = None, merchant: Merchant = Depends(get_current_merchant), db: AsyncSession = Depends(get_db)):
    filters = [MarketComplaint.market_id == market_id]
    if status: filters.append(MarketComplaint.status == status)
    rows = (await db.execute(select(MarketComplaint).where(*filters).order_by(MarketComplaint.created_at.desc()).limit(50))).scalars().all()
    return {"code": 0, "data": [{"id": str(c.id), "complainant": c.complainant, "type": c.complaint_type, "description": c.description, "status": c.status, "resolution": c.resolution, "created_at": c.created_at.isoformat() if c.created_at else None} for c in rows]}


@router.post("/complaints", response_model=AnyResponse)
async def create_complaint(body: dict, merchant: Merchant = Depends(get_current_merchant), db: AsyncSession = Depends(get_db)):
    c = MarketComplaint(market_id=uuid.UUID(body["market_id"]), complainant=body.get("complainant"),
                        complaint_type=body["complaint_type"], description=body["description"])
    if body.get("merchant_id"): c.merchant_id = uuid.UUID(body["merchant_id"])
    db.add(c); await db.commit()
    return {"code": 0, "data": {"id": str(c.id), "status": "open"}}


@router.put("/complaints/{complaint_id}/resolve", response_model=AnyResponse)
async def resolve_complaint(complaint_id: uuid.UUID, body: dict, merchant: Merchant = Depends(get_current_merchant), db: AsyncSession = Depends(get_db)):
    c = await db.get(MarketComplaint, complaint_id)
    if not c: raise HTTPException(status_code=404, detail="投诉不存在")
    c.status = "resolved"; c.resolution = body.get("resolution", ""); c.resolved_at = None  # use server time
    await db.commit()
    return {"code": 0, "message": "投诉已处理"}


# ═══ 通知 ═══

@router.get("/notices", response_model=AnyResponse)
async def list_notices(market_id: uuid.UUID, merchant: Merchant = Depends(get_current_merchant), db: AsyncSession = Depends(get_db)):
    rows = (await db.execute(select(MarketNotice).where(MarketNotice.market_id == market_id, MarketNotice.is_active == True).order_by(MarketNotice.created_at.desc()).limit(20))).scalars().all()  # noqa: E712
    return {"code": 0, "data": [{"id": str(n.id), "title": n.title, "content": n.content, "notice_type": n.notice_type, "created_at": n.created_at.isoformat() if n.created_at else None} for n in rows]}


@router.post("/notices", response_model=AnyResponse)
async def create_notice(body: dict, merchant: Merchant = Depends(get_current_merchant), db: AsyncSession = Depends(get_db)):
    n = MarketNotice(market_id=uuid.UUID(body["market_id"]), title=body["title"], content=body["content"], notice_type=body.get("notice_type", "info"))
    db.add(n); await db.commit()
    return {"code": 0, "data": {"id": str(n.id), "title": n.title}}
