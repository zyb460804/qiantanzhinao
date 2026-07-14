"""登录限流 — 防止暴力破解。

支持两种后端:
  - memory:  进程内内存（默认，单 worker 开发/测试环境）
  - redis:   集中式 Redis（多 worker/多实例生产环境）

配置通过环境变量 RATE_LIMIT_BACKEND=redis 切换。
Redis 配置: RATE_LIMIT_REDIS_URL=redis://localhost:6379/0

安全增强:
  - 从 X-Forwarded-For / X-Real-IP 获取真实客户端 IP（生产反向代理必备）
  - 全局账号级限流（跨 IP）：同一邮箱 15 分钟内最多 10 次尝试
  - 设备指纹可选支持（cookie 或 header）
"""

from __future__ import annotations

import hashlib
import os
import time
from abc import ABC, abstractmethod
from collections import defaultdict
from typing import Any

from fastapi import HTTPException, Request, status


# ═══════════════════════════════════════════
# 配置
# ═══════════════════════════════════════════

MAX_ATTEMPTS = 5  # 单 IP + 邮箱 最大尝试次数
WINDOW_SECONDS = 300  # 5 分钟滑动窗口
LOCK_SECONDS = 900  # 锁定 15 分钟

# 全局账号级限流（跨 IP）
GLOBAL_MAX_ATTEMPTS = 10
GLOBAL_WINDOW_SECONDS = 900  # 15 分钟
GLOBAL_LOCK_SECONDS = 3600  # 锁定 1 小时

BACKEND = os.getenv("RATE_LIMIT_BACKEND", "memory")
REDIS_URL = os.getenv("RATE_LIMIT_REDIS_URL", "redis://localhost:6379/0")


# ═══════════════════════════════════════════
# 抽象后端
# ═══════════════════════════════════════════


class RateLimitBackend(ABC):
    """限流后端抽象基类。"""

    @abstractmethod
    def check(self, key: str, max_attempts: int, window: int, lock: int) -> None:
        """检查是否超限，超限抛 429。"""
        ...

    @abstractmethod
    def record(self, key: str, max_attempts: int, window: int, lock: int) -> None:
        """记录一次失败尝试。"""
        ...

    @abstractmethod
    def clear(self, key: str) -> None:
        """清除记录（登录成功后）。"""
        ...

    @abstractmethod
    def status(self, key: str, max_attempts: int, window: int) -> dict:
        """获取当前状态。"""
        ...


# ═══════════════════════════════════════════
# 内存后端（单进程）
# ═══════════════════════════════════════════


class MemoryBackend(RateLimitBackend):
    """进程内内存限流。

    注意: 多 worker/多实例不共享状态，仅适用于开发/测试或单 worker 部署。
    生产多实例请使用 RedisBackend。
    """

    def __init__(self):
        self._attempts: dict[str, list[float]] = defaultdict(list)
        self._locked: dict[str, float] = {}

    def _gc(self, key: str, window: int) -> None:
        now = time.time()
        self._attempts[key] = [t for t in self._attempts.get(key, []) if now - t < window]

    def check(self, key: str, max_attempts: int, window: int, lock: int) -> None:
        now = time.time()
        if key in self._locked:
            if now < self._locked[key]:
                remaining = int(self._locked[key] - now)
                raise HTTPException(
                    status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                    detail=f"登录尝试过于频繁，请 {remaining} 秒后再试",
                )
            else:
                del self._locked[key]
                self._attempts[key] = []
        self._gc(key, window)
        if len(self._attempts.get(key, [])) >= max_attempts:
            self._locked[key] = now + lock
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail=f"登录失败次数过多，账号已锁定 {lock // 60} 分钟",
            )

    def record(self, key: str, max_attempts: int, window: int, lock: int) -> None:
        now = time.time()
        if key not in self._attempts:
            self._attempts[key] = []
        self._attempts[key].append(now)
        recent = [t for t in self._attempts[key] if now - t < window]
        if len(recent) >= max_attempts:
            self._locked[key] = now + lock

    def clear(self, key: str) -> None:
        self._attempts.pop(key, None)
        self._locked.pop(key, None)

    def status(self, key: str, max_attempts: int, window: int) -> dict:
        now = time.time()
        if key in self._locked and now < self._locked[key]:
            return {
                "locked": True,
                "remaining_seconds": int(self._locked[key] - now),
                "attempts": len(self._attempts.get(key, [])),
            }
        recent = [t for t in self._attempts.get(key, []) if now - t < window]
        return {
            "locked": False,
            "attempts": len(recent),
            "max_attempts": max_attempts,
            "remaining_attempts": max(0, max_attempts - len(recent)),
        }


# ═══════════════════════════════════════════
# Redis 后端（多实例生产环境）
# ═══════════════════════════════════════════


class RedisBackend(RateLimitBackend):
    """Redis 集中式限流后端。

    使用 Redis 的 ZSET 滑动窗口 + TTL key 实现多实例共享限流状态。
    依赖: pip install redis[hiredis]
    """

    def __init__(self, redis_url: str = REDIS_URL):
        try:
            import redis
        except ImportError as exc:
            raise ImportError(
                "Redis 限流后端需要 redis 包。安装: pip install redis[hiredis]"
            ) from exc
        self._client = redis.from_url(redis_url, decode_responses=True)

    def check(self, key: str, max_attempts: int, window: int, lock: int) -> None:
        lock_key = f"ratelimit:lock:{key}"
        now = time.time()
        lock_until = self._client.get(lock_key)
        if lock_until and float(lock_until) > now:
            remaining = int(float(lock_until) - now)
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail=f"登录尝试过于频繁，请 {remaining} 秒后再试",
            )
        # 清理过期 + 计数
        data_key = f"ratelimit:data:{key}"
        self._client.zremrangebyscore(data_key, 0, now - window)
        count = self._client.zcard(data_key)
        if count >= max_attempts:
            self._client.setex(lock_key, lock, now + lock)
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail=f"登录失败次数过多，账号已锁定 {lock // 60} 分钟",
            )

    def record(self, key: str, max_attempts: int, window: int, lock: int) -> None:
        now = time.time()
        data_key = f"ratelimit:data:{key}"
        lock_key = f"ratelimit:lock:{key}"
        self._client.zadd(data_key, {str(now): now})
        self._client.expire(data_key, window)
        count = self._client.zcard(data_key)
        if count >= max_attempts:
            self._client.setex(lock_key, lock, now + lock)

    def clear(self, key: str) -> None:
        self._client.delete(f"ratelimit:data:{key}", f"ratelimit:lock:{key}")

    def status(self, key: str, max_attempts: int, window: int) -> dict:
        now = time.time()
        lock_key = f"ratelimit:lock:{key}"
        lock_until = self._client.get(lock_key)
        if lock_until and float(lock_until) > now:
            return {
                "locked": True,
                "remaining_seconds": int(float(lock_until) - now),
                "attempts": max_attempts,
            }
        data_key = f"ratelimit:data:{key}"
        self._client.zremrangebyscore(data_key, 0, now - window)
        count = self._client.zcard(data_key)
        return {
            "locked": False,
            "attempts": count,
            "max_attempts": max_attempts,
            "remaining_attempts": max(0, max_attempts - count),
        }


# ═══════════════════════════════════════════
# 后端选择
# ═══════════════════════════════════════════

_backend: RateLimitBackend | None = None


def _get_backend() -> RateLimitBackend:
    global _backend
    if _backend is None:
        if BACKEND == "redis":
            _backend = RedisBackend(REDIS_URL)
        else:
            _backend = MemoryBackend()
    return _backend


# ═══════════════════════════════════════════
# 工具函数
# ═══════════════════════════════════════════

# 受信任代理 IP 列表（逗号分隔）
_TRUSTED_PROXIES = set(
    ip.strip() for ip in os.getenv("TRUSTED_PROXIES", "").split(",") if ip.strip()
)


def _get_client_ip(request: Request) -> str:
    """获取真实客户端 IP。

    优先从 X-Forwarded-For / X-Real-IP 获取（生产反向代理）。
    仅在 TRUSTED_PROXIES 匹配时才信任代理头。
    """
    client_ip = request.client.host if request.client else "unknown"

    if _TRUSTED_PROXIES and client_ip in _TRUSTED_PROXIES:
        forwarded = request.headers.get("X-Forwarded-For", "")
        if forwarded:
            # X-Forwarded-For: client, proxy1, proxy2
            return forwarded.split(",")[0].strip()
        real_ip = request.headers.get("X-Real-IP", "")
        if real_ip:
            return real_ip.strip()

    return client_ip


def _get_key(request: Request, email: str) -> str:
    """生成限流 key。"""
    return f"{_get_client_ip(request)}:{email}"


def _get_global_key(email: str) -> str:
    """生成全局限流 key（跨 IP 的账号级限流）。"""
    return f"global:{hashlib.sha256(email.encode()).hexdigest()[:16]}"


# ═══════════════════════════════════════════
# 公共 API（保持向后兼容）
# ═══════════════════════════════════════════


def check_rate_limit(request: Request, email: str) -> None:
    """检查登录限流（IP + 邮箱）。"""
    backend = _get_backend()
    # 1. 先检查全局账号级限流（跨 IP）
    backend.check(
        _get_global_key(email), GLOBAL_MAX_ATTEMPTS, GLOBAL_WINDOW_SECONDS, GLOBAL_LOCK_SECONDS
    )
    # 2. 再检查 IP + 邮箱限流
    backend.check(_get_key(request, email), MAX_ATTEMPTS, WINDOW_SECONDS, LOCK_SECONDS)


def record_failed_attempt(request: Request, email: str) -> None:
    """记录失败尝试。"""
    backend = _get_backend()
    backend.record(_get_key(request, email), MAX_ATTEMPTS, WINDOW_SECONDS, LOCK_SECONDS)
    backend.record(
        _get_global_key(email), GLOBAL_MAX_ATTEMPTS, GLOBAL_WINDOW_SECONDS, GLOBAL_LOCK_SECONDS
    )


def clear_attempts(request: Request, email: str) -> None:
    """登录成功后清除记录。"""
    backend = _get_backend()
    backend.clear(_get_key(request, email))
    backend.clear(_get_global_key(email))


def get_rate_limit_status(request: Request, email: str) -> dict[str, Any]:
    """获取当前限流状态。"""
    backend = _get_backend()
    return backend.status(_get_key(request, email), MAX_ATTEMPTS, WINDOW_SECONDS)
