"""设备管理 + 价目屏 API (sections 4.15, 4.16)."""

import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import get_current_merchant
from app.core.timezone import utc_now
from app.database import get_db
from app.models.catalog import ProductSKU
from app.models.device import Device, PriceDisplay
from app.models.merchant import Merchant
from app.schemas.common import AnyResponse
from app.schemas.device import (
    DeviceHeartbeatRequest,
    RegisterDeviceRequest,
    SyncPriceDisplayRequest,
)


router = APIRouter(prefix="/api/v1/devices", tags=["devices"])


# ═══ 设备 ═══


@router.get("", response_model=AnyResponse)
async def list_devices(
    merchant: Merchant = Depends(get_current_merchant), db: AsyncSession = Depends(get_db)
):
    rows = (
        (await db.execute(select(Device).where(Device.merchant_id == merchant.id))).scalars().all()
    )
    return {
        "code": 0,
        "data": [
            {
                "device_id": str(d.id),
                "device_type": d.device_type,
                "device_name": d.device_name,
                "serial_number": d.serial_number,
                "is_active": d.is_active,
                "last_heartbeat": d.last_heartbeat.isoformat() if d.last_heartbeat else None,
                "firmware_version": d.firmware_version,
                "last_error": d.last_error,
            }
            for d in rows
        ],
    }


@router.post("", response_model=AnyResponse)
async def register_device(
    body: RegisterDeviceRequest,
    merchant: Merchant = Depends(get_current_merchant),
    db: AsyncSession = Depends(get_db),
):
    if body.serial_number:
        duplicate = await db.scalar(
            select(Device).where(
                Device.merchant_id == merchant.id,
                Device.serial_number == body.serial_number,
            )
        )
        if duplicate:
            raise HTTPException(status_code=409, detail="该序列号已注册")

    d = Device(
        merchant_id=merchant.id,
        device_type=body.device_type,
        device_name=body.device_name,
        serial_number=body.serial_number,
        firmware_version=body.firmware_version,
        config=body.config,
    )
    db.add(d)
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(status_code=409, detail="该序列号已注册") from exc
    await db.refresh(d)
    return {"code": 0, "data": {"device_id": str(d.id), "device_type": d.device_type}}


@router.post("/{device_id}/heartbeat", response_model=AnyResponse)
async def device_heartbeat(
    device_id: uuid.UUID,
    body: DeviceHeartbeatRequest | None = None,
    merchant: Merchant = Depends(get_current_merchant),
    db: AsyncSession = Depends(get_db),
):
    d = await db.get(Device, device_id)
    if not d or d.merchant_id != merchant.id:
        raise HTTPException(status_code=404, detail="设备不存在")
    if not d.is_active:
        raise HTTPException(status_code=409, detail="设备已停用，不能发送心跳")
    d.last_heartbeat = utc_now()
    if body and body.error is not None:
        d.last_error = body.error
    if body and body.firmware_version:
        d.firmware_version = body.firmware_version
    await db.commit()
    return {"code": 0, "message": "heartbeat received"}


@router.delete("/{device_id}", response_model=AnyResponse)
async def deactivate_device(
    device_id: uuid.UUID,
    merchant: Merchant = Depends(get_current_merchant),
    db: AsyncSession = Depends(get_db),
):
    d = await db.get(Device, device_id)
    if not d or d.merchant_id != merchant.id:
        raise HTTPException(status_code=404, detail="设备不存在")
    d.is_active = False
    await db.commit()
    return {"code": 0, "message": "设备已停用"}


# ═══ 价目屏 (section 4.16) ═══


@router.get("/price-display", response_model=AnyResponse)
async def list_price_displays(
    merchant: Merchant = Depends(get_current_merchant), db: AsyncSession = Depends(get_db)
):
    rows = (
        (await db.execute(select(PriceDisplay).where(PriceDisplay.merchant_id == merchant.id)))
        .scalars()
        .all()
    )
    sku_ids = {r.sku_id for r in rows}
    sku_map = {}
    if sku_ids:
        skus = (
            (await db.execute(select(ProductSKU).where(ProductSKU.id.in_(sku_ids)))).scalars().all()
        )
        sku_map = {s.id: s.name for s in skus}
    return {
        "code": 0,
        "data": [
            {
                "display_id": str(r.id),
                "sku_name": sku_map.get(r.sku_id, str(r.sku_id)),
                "current_price": r.current_price,
                "sync_status": r.sync_status,
                "last_synced": r.last_synced.isoformat() if r.last_synced else None,
            }
            for r in rows
        ],
    }


@router.post("/price-display/sync", response_model=AnyResponse)
async def sync_price_display(
    body: SyncPriceDisplayRequest,
    merchant: Merchant = Depends(get_current_merchant),
    db: AsyncSession = Depends(get_db),
):
    """Sync current prices to customer display. Call after price changes."""
    sku_ids = body.sku_ids
    if not sku_ids:
        skus = (
            (
                await db.execute(
                    select(ProductSKU).where(
                        ProductSKU.merchant_id == merchant.id, ProductSKU.is_active.is_(True)
                    )
                )
            )
            .scalars()
            .all()
        )  # noqa: E712
        sku_ids = [s.id for s in skus]
    synced = 0
    now = utc_now()
    for sid in sku_ids:
        sku = await db.get(ProductSKU, sid)
        if not sku or sku.merchant_id != merchant.id:
            continue
        display = await db.scalar(
            select(PriceDisplay).where(
                PriceDisplay.merchant_id == merchant.id, PriceDisplay.sku_id == sid
            )
        )
        if not display:
            display = PriceDisplay(merchant_id=merchant.id, sku_id=sid)
            db.add(display)
        display.current_price = float(sku.default_sale_price or 0)
        display.price_source = body.source
        display.sync_status = "synced"
        display.last_synced = now
        synced += 1
    await db.commit()
    return {"code": 0, "message": f"已同步 {synced} 个价格到价目屏"}
