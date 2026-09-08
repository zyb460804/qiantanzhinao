"""时区工具 — 避免代码中散落 datetime.now() 导致 UTC/本地时间混用。

当前约定（2026-07-12 统一 UTC 收口，2026-08 审计 C4/V2-C2 复核）：
- DB 中所有时间列（created_at / voided_at / purchased_at / paid_at / closed_at /
  synced_at / event_time 等）一律为 naive UTC（TIMESTAMP without time zone），
  写入端统一传 naive UTC：服务端时间戳走 utc_now()（返回 naive），客户端
  ISO 串经 parse_iso_datetime() 归一成 naive UTC。asyncpg 对 naive 列不做
  aware→naive 转换，写入 aware datetime 会直接 TypeError（生产 PG 写路径 500；
  SQLite 会静默丢 offset 掩盖该问题），因此本模块对外的时间获取/解析全部
  返回 naive，需要 aware 或对外 ISO 输出时用 format_utc_iso() 或自行
  .replace(tzinfo=UTC)。
- 读端业务日界/窗口一律按 CST（Asia/Shanghai, UTC+8，无夏令时）业务日切分：
  先把 CST 业务日的 [00:00, 24:00) 换算成 naive UTC 区间（cst_day_bounds_utc
  及其衍生 helper），再与 DB 比较——转完 strip tzinfo，避免 aware/naive 混比。
  否则 CST 凌晨 0-8 点（= 前一 UTC 日 16-24 点）的销售会被归入前一天。
- local_* 函数依赖服务器本地时区，任意部署时区下必有一侧错，已标记废弃，
  仅供尚未迁移的旧链路（pos.py 日结锁定）过渡使用；新代码一律改用
  cst_*/utc_* helper。
"""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta, timezone


# 业务时区：Asia/Shanghai 固定 UTC+8（无夏令时）
CST = timezone(timedelta(hours=8))


def cst_now() -> datetime:
    """业务当前时间（CST, aware）。"""
    return datetime.now(CST)


def cst_today() -> date:
    """业务「今天」的 CST 日期——摊贩认知中的今天，与部署服务器时区无关。"""
    return cst_now().date()


def cst_day_bounds_utc(d: date) -> tuple[datetime, datetime]:
    """CST 业务日 d 的 [00:00, 24:00) 换算为 naive UTC 区间。

    DB 时间列为 naive UTC，查询直接用返回值比较（>= start, < end）。
    """
    start = datetime.combine(d, time.min, tzinfo=CST).astimezone(UTC).replace(tzinfo=None)
    end = datetime.combine(d + timedelta(days=1), time.min, tzinfo=CST)
    return start, end.astimezone(UTC).replace(tzinfo=None)


def cst_days_ago_bounds_utc(days: int) -> tuple[datetime, datetime]:
    """含今天在内最近 N 个 CST 业务日的 naive UTC 查询区间。

    返回 (N-1 天前 CST 日始, 明天 CST 日始)，即「近 N 天报表窗口」；
    相比滚动的 now-N 多了不足一天的边缘对齐，保证整业务日计入。
    """
    return cst_day_bounds_utc(cst_today() - timedelta(days=days - 1))


def cst_month_bounds_utc(year: int, month: int) -> tuple[datetime, datetime]:
    """CST 业务月 [月初, 次月初) 换算为 naive UTC 区间（用于 created_at 等时间列）。"""
    first = date(year, month, 1)
    next_first = date(year + 1, 1, 1) if month == 12 else date(year, month + 1, 1)
    return cst_day_bounds_utc(first)[0], cst_day_bounds_utc(next_first)[0]


def cst_date_of_utc_naive(dt: datetime) -> date:
    """把 DB 里的 naive UTC 时间映射回它所属的 CST 业务日。"""
    return dt.replace(tzinfo=UTC).astimezone(CST).date()


def utc_now() -> datetime:
    """服务端当前时间（naive UTC）。

    DB 时间列全部为 naive UTC，写入端统一 naive（见模块 docstring）；
    需要 aware 时自行 .replace(tzinfo=UTC)，需要对外 ISO 串时用 format_utc_iso()。
    """
    return datetime.now(UTC).replace(tzinfo=None)


def local_now() -> datetime:
    """本地当前时间。

    .. deprecated:: 依赖服务器本地时区，任意部署时区下必有一侧错。
        仅 pos.py 日结锁定等旧链路在用；新代码改用 cst_now()（业务时刻）
        或 utc_now()（落库时间戳）。
    """
    return datetime.now()


def utc_today_start() -> datetime:
    """UTC 今天 00:00:00（naive，与 DB 列/utc_now() 同体系，可直接比较）。"""
    return datetime.combine(utc_now().date(), time.min)


def utc_today_end() -> datetime:
    """UTC 今天 23:59:59.999999（naive，与 DB 列/utc_now() 同体系）。"""
    return datetime.combine(utc_now().date(), time.max)


def local_today_start() -> datetime:
    """本地今天 00:00:00。

    .. deprecated:: 依赖服务器本地时区；新代码按业务日界用
        cst_day_bounds_utc(cst_today())[0]。
    """
    return datetime.combine(local_now().date(), time.min)


def local_today_end() -> datetime:
    """本地今天 23:59:59.999999。

    .. deprecated:: 依赖服务器本地时区；新代码按业务日界用
        cst_day_bounds_utc(cst_today() + timedelta(days=1))[0]。
    """
    return datetime.combine(local_now().date(), time.max)


def utc_days_ago(days: int) -> datetime:
    """UTC 当前时间往前推 N 天（naive；用作 naive UTC 列的 WHERE 下界）。"""
    return utc_now() - timedelta(days=days)


def local_days_ago(days: int) -> datetime:
    """本地当前时间往前推 N 天。

    .. deprecated:: 依赖服务器本地时区；新代码按业务日窗口用
        cst_days_ago_bounds_utc(n)[0]。
    """
    return local_now() - timedelta(days=days)


def parse_iso_datetime(value: str | None) -> datetime | None:
    """解析 ISO 时间字符串，返回 naive UTC datetime。

    客户端未带时区时按 UTC 解析；带任意时区偏移一律换算成 UTC 后 strip
    tzinfo —— 该值主要用于写入 naive UTC 的 DB 列（offline_sync 等），
    返回 aware 会在生产 PG（asyncpg）写路径直接 TypeError。
    """
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            return dt
        return dt.astimezone(UTC).replace(tzinfo=None)
    except (ValueError, TypeError):
        return None


def format_utc_iso(dt: datetime | None) -> str | None:
    """将 datetime 转为 UTC ISO 字符串（返回 None 给 None 输入）。"""
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC).isoformat()
