"""Health & monitoring utilities (§5.14).

Tracks operational metrics that are critical for production:
- Login failures (rate-limited check)
- Sync dead-letter queue depth
- Device heartbeats (stale detection)
- Database connectivity
"""

from __future__ import annotations

import uuid
from datetime import timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.timezone import utc_now


async def get_sync_dead_letter_count(db: AsyncSession, merchant_id: uuid.UUID | None = None) -> int:
    """Count dead-letter events that are still pending, retrying, or permanently failed."""
    from app.models.dead_letter import DeadLetterEvent

    filters: list = [DeadLetterEvent.status.in_(["pending", "retrying", "permanent_failure"])]
    if merchant_id:
        filters.append(DeadLetterEvent.merchant_id == merchant_id)
    result = await db.execute(select(func.count(DeadLetterEvent.id)).where(*filters))
    return result.scalar() or 0


async def get_stale_devices(db: AsyncSession, threshold_hours: int = 1) -> int:
    """Count devices that haven't sent heartbeat in threshold_hours."""
    from app.models.device import Device

    threshold = utc_now() - timedelta(hours=threshold_hours)
    result = await db.execute(
        select(func.count(Device.id)).where(
            Device.is_active == True,  # noqa: E712
            Device.last_heartbeat < threshold,
        )
    )
    return result.scalar() or 0


async def get_db_connectivity(db: AsyncSession) -> bool:
    """Check database is reachable and responsive."""
    try:
        await db.execute(select(func.count()).select_from(func.now()))
        return True
    except Exception:
        return False


async def build_health_report(db: AsyncSession) -> dict:
    """Build a comprehensive health report for operators."""
    from app.services.device_monitor import detect_device_faults

    db_ok = await get_db_connectivity(db)
    stale_devices = await get_stale_devices(db)
    dead_letters = await get_sync_dead_letter_count(db)
    device_faults = await detect_device_faults(db)

    status = "healthy"
    issues = []
    if not db_ok:
        status = "unhealthy"
        issues.append("数据库连接失败")
    if stale_devices > 0:
        status = "degraded" if status == "healthy" else status
        issues.append(f"{stale_devices} 台设备心跳超时")
    if dead_letters > 0:
        status = "degraded" if status == "healthy" else status
        issues.append(f"{dead_letters} 条同步死信待处理")

    fault_alert_count = sum(1 for f in device_faults if f["severity"] == "alert")
    fault_warning_count = sum(1 for f in device_faults if f["severity"] == "warning")
    if fault_alert_count > 0:
        status = "degraded" if status == "healthy" else status
        issues.append(f"{fault_alert_count} 个设备故障告警")
    if fault_warning_count > 0:
        issues.append(f"{fault_warning_count} 个设备故障警告")

    return {
        "status": status,
        "timestamp": utc_now().isoformat(),
        "checks": {
            "database": "ok" if db_ok else "fail",
            "stale_devices": stale_devices,
            "dead_letters": dead_letters,
            "device_faults": {
                "total": len(device_faults),
                "alert": fault_alert_count,
                "warning": fault_warning_count,
            },
        },
        "issues": issues,
    }
