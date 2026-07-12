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


logger = logging.getLogger(__name__)


class MemoryCache:
    """Thread-safe in-memory cache with TTL support.

    Production: replace with Redis via same method signatures.
    """

    def __init__(self):
        self._store: dict[str, tuple[float, any]] = {}
        self._lock = threading.Lock()
        self._hits = 0
        self._misses = 0

    def get(self, key: str):
        """Get a cached value. Returns None if key is missing or expired."""
        with self._lock:
            entry = self._store.get(key)
            if entry is None:
                self._misses += 1
                return None
            expires_at, value = entry
            if expires_at > 0 and time.monotonic() > expires_at:
                del self._store[key]
                self._misses += 1
                return None
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

    Args:
        db_session_factory: AsyncSession factory
        cache_key: Unique key for this data
        ttl: Cache time-to-live in seconds
        fetch_fn: Async function(session) → value to cache

    Returns:
        Cached or freshly-fetched value.
    """
    result = cache.get(cache_key)
    if result is not None:
        return result

    async with db_session_factory() as session:
        result = await fetch_fn(session)

    if result is not None:
        cache.set(cache_key, result, ttl)
    return result


def invalidate_product_cache():
    """Invalidate all product-related cache entries."""
    cache.delete("products:active")
    cache.delete("products:categories")
    logger.info("Product cache invalidated")
