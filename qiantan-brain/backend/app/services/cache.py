"""
Lightweight in-memory cache layer.
Designed as a Redis-compatible abstraction — currently uses dictionary-backed
cache (zero external dependency). Swap to Redis by implementing the same interface.

Usage:
    from app.services.cache import cache
    cache.set("key", value, ttl_seconds=300)
    value = cache.get("key")
"""

import logging
import threading
import time
from typing import Any


logger = logging.getLogger(__name__)


# Sentinel 用于区分「未查到」与「查到空值（None / [] / 0 / False / ""）」。
# 直接传给 cache.get(default=_MISSING)，命中时返回真实值（含 falsy），未命中返回 _MISSING。
_MISSING: Any = object()
# 内部哨兵：标记 `get` 的 default 参数「未显式传入」，此时未命中返回 None（向后兼容）。
# 不能复用 _MISSING，否则调用方传 default=_MISSING 时无法与「未传」区分。
_UNSET: Any = object()


class MemoryCache:
    """Thread-safe in-memory cache with TTL support.

    Production: replace with Redis via same method signatures.
    """

    def __init__(self):
        self._store: dict[str, tuple[float, any]] = {}
        self._lock = threading.Lock()
        self._hits = 0
        self._misses = 0

    def get(self, key: str, default: Any = _UNSET) -> Any:
        """Get a cached value.

        - 不传 `default`：未命中或过期时返回 None（向后兼容）。
        - 传 `default=_MISSING`（模块级哨兵）并用 `is` 判定：可区分「未命中」与
          「缓存了 None/falsy 值」，避免 [] / 0 / False / "" 被当成未命中反复回查 DB。
        - 传任意其他 `default`：未命中时返回该默认值。
        """
        with self._lock:
            entry = self._store.get(key)
            if entry is None:
                self._misses += 1
                return None if default is _UNSET else default
            expires_at, value = entry
            if expires_at > 0 and time.monotonic() > expires_at:
                del self._store[key]
                self._misses += 1
                return None if default is _UNSET else default
            self._hits += 1
            return value

    def set(self, key: str, value, ttl_seconds: int = 300):
        """Set a cached value with TTL in seconds (default 5 min)."""
        expires_at = time.monotonic() + ttl_seconds if ttl_seconds > 0 else 0
        with self._lock:
            self._store[key] = (expires_at, value)

    def delete(self, key: str):
        """Remove a key from cache."""
        with self._lock:
            self._store.pop(key, None)

    def clear(self):
        """Clear all cached entries."""
        with self._lock:
            self._store.clear()

    def stats(self) -> dict:
        """Return cache hit/miss statistics."""
        return {
            "size": len(self._store),
            "hits": self._hits,
            "misses": self._misses,
            "hit_rate": round(self._hits / (self._hits + self._misses) * 100, 1)
            if (self._hits + self._misses) > 0
            else 0,
        }


# Global singleton cache instance
cache = MemoryCache()


# ── Convenience cached getter helpers ────────────────────────────────────


async def cached_get(db_session_factory, cache_key: str, ttl: int, fetch_fn):
    """Generic cached async fetch pattern.

    使用 sentinel 区分「未查到」与「查到空值」：DB 返回的 None / [] / 0 / False / ""
    都会被缓存，避免空结果反复回查数据库。

    Args:
        db_session_factory: AsyncSession factory
        cache_key: Unique key for this data
        ttl: Cache time-to-live in seconds
        fetch_fn: Async function(session) → value to cache

    Returns:
        Cached or freshly-fetched value（含 None / falsy）。
    """
    result = cache.get(cache_key, default=_MISSING)
    if result is not _MISSING:
        return result

    async with db_session_factory() as session:
        result = await fetch_fn(session)

    # 空值也缓存：sentinel 已在外层区分「未查」与「查到空」。
    cache.set(cache_key, result, ttl)
    return result


def invalidate_product_cache():
    """Invalidate all product-related cache entries."""
    cache.delete("products:active")
    cache.delete("products:categories")
    logger.info("Product cache invalidated")
