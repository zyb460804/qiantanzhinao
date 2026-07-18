"""设备故障检测服务。

检测维度：
  1. 心跳超时：>2h → warning，>24h → alert
  2. 同步失败率：24h 内 >10% → alert（框架已搭，按需接入）
  3. 连续同步失败：>5 次 → alert（框架已搭，按需接入）
  4. 模型版本过期：落后最新 2 个版本以上 → warning（框架已搭，按需接入）
"""

from __future__ import annotations

from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.timezone import utc_now


async def detect_device_faults(db: AsyncSession) -> list[dict]:
    """检测全系统设备故障，返回故障列表。"""
    faults: list[dict] = []

    now = utc_now()

    # ── 1. 心跳超时检测 ──
    from app.models.device import Device

    # >24h → alert
    alert_threshold = now - timedelta(hours=24)
    stale_alert_result = await db.execute(
        select(Device).where(
            Device.is_active.is_(True),
            Device.last_heartbeat < alert_threshold.replace(tzinfo=None),
        )
    )
    for dev in stale_alert_result.scalars().all():
        faults.append(
            {
                "device_id": str(dev.id),
                "device_name": dev.device_name,
                "device_type": dev.device_type,
                "severity": "alert",
                "type": "heartbeat_timeout",
                "detail": "超过 24 小时未上报心跳",
                "last_heartbeat": (dev.last_heartbeat.isoformat() if dev.last_heartbeat else None),
            }
        )

    # >2h → warning（排除已计入 alert 的）
    warn_threshold = now - timedelta(hours=2)
    stale_warn_result = await db.execute(
        select(Device).where(
            Device.is_active.is_(True),
            Device.last_heartbeat < warn_threshold.replace(tzinfo=None),
            Device.last_heartbeat >= alert_threshold.replace(tzinfo=None),
        )
    )
    for dev in stale_warn_result.scalars().all():
        faults.append(
            {
                "device_id": str(dev.id),
                "device_name": dev.device_name,
                "device_type": dev.device_type,
                "severity": "warning",
                "type": "heartbeat_timeout",
                "detail": "超过 2 小时未上报心跳",
                "last_heartbeat": (dev.last_heartbeat.isoformat() if dev.last_heartbeat else None),
            }
        )

    # ── 2. 设备上报错误检测 ──
    error_result = await db.execute(
        select(Device).where(
            Device.is_active.is_(True),
            Device.last_error.isnot(None),
        )
    )
    for dev in error_result.scalars().all():
        faults.append(
            {
                "device_id": str(dev.id),
                "device_name": dev.device_name,
                "device_type": dev.device_type,
                "severity": "warning",
                "type": "device_error",
                "detail": dev.last_error,
                "last_heartbeat": (dev.last_heartbeat.isoformat() if dev.last_heartbeat else None),
            }
        )

    return faults


async def get_device_fault_summary(db: AsyncSession) -> dict:
    """返回按严重程度分类的故障汇总。"""
    faults = await detect_device_faults(db)
    alert_count = sum(1 for f in faults if f["severity"] == "alert")
    warning_count = sum(1 for f in faults if f["severity"] == "warning")
    return {
        "total": len(faults),
        "alert": alert_count,
        "warning": warning_count,
    }
