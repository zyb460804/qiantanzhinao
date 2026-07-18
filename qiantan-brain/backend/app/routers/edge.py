"""Edge device sync API router — receives offline-cached records.

支持两种鉴权方式：
  1. 商户 Bearer Token（用于小程序/开发调试）—— 走 get_merchant_id
  2. 设备 API Key（用于生产边缘端）—— 走 DeviceAuth.require("edge:ingest")

每条事件必须携带全局唯一 event_id，后端通过唯一约束实现幂等去重。
仅在事务提交成功后返回 accepted，确保 Edge 不会误标记未持久化数据为已同步。
"""

import json
import logging
import uuid
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Body, Depends, HTTPException, Request, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.device_auth import DeviceAuth
from app.core.security import get_merchant_id
from app.database import get_db
from app.models.edge_event import EdgeEvent
from app.schemas.edge import EdgeIngestResponse


logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/edge", tags=["edge"])


async def _persist_ingest(
    db: AsyncSession,
    body: dict,
    merchant_id: uuid.UUID,
    tenant_id: uuid.UUID | None = None,
    allow_generated_event_id: bool = False,
) -> dict:
    """持久化边缘设备摄入事件（幂等去重）。"""
    body_merchant_id = body.get("merchant_id")
    if body_merchant_id:
        try:
            if uuid.UUID(body_merchant_id) != merchant_id:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="merchant_id in body does not match authenticated merchant",
                )
        except (ValueError, TypeError) as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="invalid merchant_id in body",
            ) from exc

    event_id = body.get("event_id")
    if not event_id:
        if not allow_generated_event_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="missing required field: event_id",
            )
        event_id = str(uuid.uuid4())

    # 幂等去重：相同 event_id 直接返回已接受
    existing = await db.scalar(select(EdgeEvent.id).where(EdgeEvent.event_id == event_id))
    if existing:
        logger.info("edge ingest: duplicate event_id=%s, skipped", event_id)
        return {
            "code": 0,
            "data": {
                "accepted": True,
                "merchant_id": str(merchant_id),
                "event_id": event_id,
                "duplicate": True,
            },
        }

    detections = body.get("detections", [])
    weight = body.get("weight_g")
    device_id = body.get("device") or body.get("device_id")
    occurred_at_str = body.get("timestamp") or body.get("occurred_at")
    try:
        occurred_at = (
            datetime.fromisoformat(str(occurred_at_str).replace("Z", "+00:00"))
            if occurred_at_str
            else datetime.now(UTC)
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="invalid occurred_at/timestamp",
        ) from exc

    event = EdgeEvent(
        event_id=event_id,
        device_id=str(device_id)[:64] if device_id else None,
        merchant_id=merchant_id,
        tenant_id=tenant_id,
        event_type=body.get("event_type", "weight"),
        occurred_at=occurred_at,
        payload=json.dumps(
            {
                "detections": detections,
                "weight_g": weight,
                "image_sha256": body.get("image_sha256"),
            },
            ensure_ascii=False,
        ),
        model_version=body.get("model_version"),
        sequence=body.get("sequence"),
    )
    db.add(event)
    await db.commit()
    await db.refresh(event)

    logger.info(
        "edge ingest: merchant=%s event_id=%s detections=%d weight_g=%s",
        merchant_id,
        event_id,
        len(detections),
        weight,
    )
    return {
        "code": 0,
        "data": {
            "accepted": True,
            "merchant_id": str(merchant_id),
            "event_id": event_id,
            "detection_count": len(detections),
            "weight_g": weight,
        },
    }


@router.post("/ingest", response_model=EdgeIngestResponse)
async def ingest_edge_record(
    request: Request,
    merchant_id: uuid.UUID = Depends(get_merchant_id),
    db: AsyncSession = Depends(get_db),
    body: dict = Body(...),
):
    """
    商户 Bearer Token 鉴权 — 接收边缘设备记录。

    适用于小程序触发的数据上报或开发调试场景。
    """
    return await _persist_ingest(db, body, merchant_id, allow_generated_event_id=True)


@router.post("/ingest/device", response_model=EdgeIngestResponse)
async def ingest_edge_record_device(
    request: Request,
    device: dict = Depends(DeviceAuth.require("edge:ingest")),
    db: AsyncSession = Depends(get_db),
    body: dict = Body(...),
):
    """
    设备 API Key 鉴权 — 接收边缘设备记录。

    适用于生产环境树莓派自主上报。需要设备预先在管理后台创建 API Key，
    并在边缘端配置 X-Api-Key / X-Device-Id / X-Timestamp / X-Nonce 请求头。

    每条事件必须包含全局唯一 event_id，后端通过唯一约束自动去重。
    ACK 仅在事务持久化成功后返回。
    """
    merchant_id = uuid.UUID(str(device["merchant_id"]))
    tenant_id = uuid.UUID(str(device["tenant_id"]))
    body = dict(body)
    body["device_id"] = device["device_id"]
    return await _persist_ingest(db, body, merchant_id, tenant_id)


@router.post("/heartbeat")
async def device_heartbeat(
    device: dict = Depends(DeviceAuth.require("edge:heartbeat")),
    db: AsyncSession = Depends(get_db),
):
    """设备心跳上报 — 记录设备在线状态并更新 last_heartbeat。"""
    device_id = device.get("device_id")
    merchant_id = device["merchant_id"]

    logger.info(
        "device heartbeat: tenant=%s merchant=%s device=%s",
        device.get("tenant_id"),
        merchant_id,
        device_id,
    )

    # 鉴权阶段已校验设备归属，按内部主键更新，避免同序列号跨商户串写。
    from app.models.device import Device as DeviceModel

    dev = await db.get(DeviceModel, uuid.UUID(str(device["registered_device_id"])))
    if dev:
        dev.last_heartbeat = datetime.now(UTC).replace(tzinfo=None)
        await db.commit()

    return {
        "code": 0,
        "data": {
            "ack": True,
            "server_time": __import__("time").time(),
        },
    }


# ── OTA 固件 / 模型版本管理 ──


@router.get("/ota/check")
async def ota_check(
    device_type: str,
    current_version: str,
    device: dict = Depends(DeviceAuth.require("edge:ingest")),
    db: AsyncSession = Depends(get_db),
):
    """设备检查固件/模型更新。

    Query params:
        device_type: scale/camera/esl/all
        current_version: 当前版本号（语义版本比较）
    """
    from app.models.device import DeviceFirmware

    # 查找当前设备类型或 all 类型的最新激活固件
    result = await db.execute(
        select(DeviceFirmware)
        .where(
            DeviceFirmware.is_active.is_(True),
            DeviceFirmware.device_type.in_([device_type, "all"]),
        )
        .order_by(DeviceFirmware.created_at.desc())
        .limit(1)
    )
    firmware = result.scalar()

    if firmware is None:
        return {"update_available": False}

    # 简单语义版本比较：仅当 latest > current 时推送
    if _version_gt(firmware.version, current_version):
        return {
            "update_available": True,
            "latest_version": firmware.version,
            "file_url": firmware.file_url,
            "file_hash": firmware.file_hash,
            "file_size": firmware.file_size,
            "changelog": firmware.changelog,
        }

    return {"update_available": False}


@router.post("/ota/report")
async def ota_report(
    body: dict = Body(...),
    device: dict = Depends(DeviceAuth.require("edge:ingest")),
    db: AsyncSession = Depends(get_db),
):
    """设备上报 OTA 升级结果。

    Body: {device_id, firmware_version, status: "success"|"failed", error?: str}
    """
    from app.models.device import Device as DeviceModel

    device_id = body.get("device_id")
    firmware_version = body.get("firmware_version", "")
    status_value = body.get("status", "failed")
    error = body.get("error")

    if status_value == "success":
        # 尝试通过内部主键更新固件版本
        registered_id = uuid.UUID(str(device["registered_device_id"]))
        dev = await db.get(DeviceModel, registered_id)
        if dev:
            dev.firmware_version = firmware_version
            await db.commit()
        logger.info("OTA success: device=%s version=%s", device_id, firmware_version)
    else:
        logger.warning(
            "OTA failed: device=%s version=%s error=%s", device_id, firmware_version, error
        )

    return {"code": 0, "data": {"ack": True}}


@router.post("/model-version")
async def report_model_version(
    body: dict = Body(...),
    device: dict = Depends(DeviceAuth.require("edge:ingest")),
    db: AsyncSession = Depends(get_db),
):
    """设备上报模型版本。

    Body: {model_type, model_version, metadata?}
    """
    from app.models.device import DeviceModelVersion

    registered_id = uuid.UUID(str(device["registered_device_id"]))
    model_type = body.get("model_type", "")
    model_version = body.get("model_version", "")
    metadata_val = body.get("metadata")

    # Upsert: 按 device_id + model_type 查找最新记录，若版本相同则跳过
    existing = await db.scalar(
        select(DeviceModelVersion)
        .where(
            DeviceModelVersion.device_id == registered_id,
            DeviceModelVersion.model_type == model_type,
        )
        .order_by(DeviceModelVersion.reported_at.desc())
        .limit(1)
    )

    if existing and existing.model_version == model_version:
        # 版本无变化，仅更新时间戳
        existing.reported_at = datetime.now(UTC).replace(tzinfo=None)
        if metadata_val:
            existing.metadata_ = metadata_val
        await db.commit()
    else:
        record = DeviceModelVersion(
            device_id=registered_id,
            model_type=model_type,
            model_version=model_version,
            reported_at=datetime.now(UTC).replace(tzinfo=None),
            metadata_=metadata_val,
        )
        db.add(record)
        await db.commit()

    logger.info(
        "model version: device=%s model=%s version=%s",
        device.get("device_id"),
        model_type,
        model_version,
    )
    return {"code": 0, "data": {"ack": True}}


@router.post("/logs")
async def upload_device_logs(
    body: dict = Body(...),
    device: dict = Depends(DeviceAuth.require("edge:ingest")),
    db: AsyncSession = Depends(get_db),
):
    """设备批量上传远程日志。

    Body: {logs: [{level, message, source?, timestamp?}, ...]}
    限流：每设备每小时最多 1000 条。
    """
    from app.models.device import DeviceRemoteLog

    registered_id = uuid.UUID(str(device["registered_device_id"]))
    entries = body.get("logs", [])
    if not entries:
        return {"code": 0, "data": {"accepted": 0, "rejected": 0}}

    # 限流检查：过去一小时内该设备已上传的日志数量
    one_hour_ago = datetime.now(UTC).replace(tzinfo=None) - timedelta(hours=1)
    count_result = await db.execute(
        select(func.count(DeviceRemoteLog.id)).where(
            DeviceRemoteLog.device_id == registered_id,
            DeviceRemoteLog.created_at >= one_hour_ago,
        )
    )
    recent_count = count_result.scalar() or 0

    max_per_hour = 1000
    remaining = max(0, max_per_hour - recent_count)

    accepted = 0
    rejected = 0
    for entry in entries[:remaining]:
        try:
            device_ts = None
            ts_str = entry.get("timestamp")
            if ts_str:
                try:
                    device_ts = datetime.fromisoformat(str(ts_str).replace("Z", "+00:00"))
                except (ValueError, TypeError):
                    pass

            log_entry = DeviceRemoteLog(
                device_id=registered_id,
                level=str(entry.get("level", "INFO"))[:10],
                message=str(entry.get("message", ""))[:10000],
                source=str(entry.get("source", ""))[:50] if entry.get("source") else None,
                device_timestamp=device_ts,
            )
            db.add(log_entry)
            accepted += 1
        except Exception as exc:
            logger.warning(
                "device log insert failed: device=%s error=%s", device.get("device_id"), exc
            )
            rejected += 1

    rejected += max(0, len(entries) - remaining)

    await db.commit()
    logger.info(
        "device logs: device=%s accepted=%d rejected=%d",
        device.get("device_id"),
        accepted,
        rejected,
    )
    return {"code": 0, "data": {"accepted": accepted, "rejected": rejected}}


def _version_gt(a: str, b: str) -> bool:
    """Compare two semver-like strings; returns True if a > b."""
    try:
        parts_a = [int(x) for x in a.split(".")]
        parts_b = [int(x) for x in b.split(".")]
    except (ValueError, AttributeError):
        return a > b  # fallback to string compare

    max_len = max(len(parts_a), len(parts_b))
    parts_a.extend([0] * (max_len - len(parts_a)))
    parts_b.extend([0] * (max_len - len(parts_b)))

    for pa, pb in zip(parts_a, parts_b, strict=True):
        if pa > pb:
            return True
        if pa < pb:
            return False
    return False
