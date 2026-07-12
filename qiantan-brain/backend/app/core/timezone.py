"""时区工具 — 避免代码中散落 datetime.now() 导致 UTC/本地时间混用。

当前约定（2026-07-12 时区统一收口）：
- 服务端时间戳（created_at, voided_at, purchased_at, paid_at, closed_at, synced_at 等）
  统一用 UTC。这些字段用户无直接感知，且数据库 server_default 已用 UTC now()，
  代码层写入也应保持一致。
- 业务事件时间（event_time）当前保留本地时间（数据库历史数据均为本地时间，
  统一迁移到 UTC 需要单独数据迁移）。
- 查询时：created_at / voided_at / purchased_at 等服务端时间戳相关用 UTC 日期基准；
  event_time 相关用本地日期基准。

全栈统一 UTC 的下一步：迁移历史 event_time 到 UTC 后，将 local_* 函数逐步替换
为 utc_* 函数，并删除 local_now / local_today_start。
"""

from __future__ import annotations

from datetime import UTC, datetime, time, timedelta


def utc_now() -> datetime:
    """服务端当前时间（UTC）。"""
    return datetime.now(UTC)


def local_now() -> datetime:
    """本地当前时间（业务事件时间 event_time 当前用）。"""
    return datetime.now()


def utc_today_start() -> datetime:
    """UTC 今天 00:00:00。"""
    return datetime.combine(utc_now().date(), time.min, tzinfo=UTC)


def utc_today_end() -> datetime:
    """UTC 今天 23:59:59.999999。"""
    return datetime.combine(utc_now().date(), time.max, tzinfo=UTC)


def local_today_start() -> datetime:
    """本地今天 00:00:00。"""
    return datetime.combine(local_now().date(), time.min)


def local_today_end() -> datetime:
    """本地今天 23:59:59.999999。"""
    return datetime.combine(local_now().date(), time.max)


def utc_days_ago(days: int) -> datetime:
    """UTC 当前时间往前推 N 天。"""
    return utc_now() - timedelta(days=days)


def local_days_ago(days: int) -> datetime:
    """本地当前时间往前推 N 天。"""
    return local_now() - timedelta(days=days)


def parse_iso_datetime(value: str | None) -> datetime | None:
    """解析 ISO 时间字符串，返回 UTC 时区 datetime。

    客户端未带时区时按 UTC 解析（当前统一 UTC 过渡阶段的约定）。
    """
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        return dt.astimezone(UTC)
    except (ValueError, TypeError):
        return None


def format_utc_iso(dt: datetime | None) -> str | None:
    """将 datetime 转为 UTC ISO 字符串（返回 None 给 None 输入）。"""
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC).isoformat()
