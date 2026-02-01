"""
L1 Redis cache with envelope format for SWR age checks.

All values stored as: {"data": <payload>, "stored_at": <unix_timestamp>}
"""

import hashlib
import time
import asyncio
from typing import Any, Optional

import orjson
import redis.asyncio as aioredis

from .config import CacheConfig


def make_key(prefix: str, *parts: str) -> str:
    """Build a cache key. For variable-length parts, uses sha1 truncated to 16 chars."""
    raw = "|".join(str(p) for p in parts)
    if len(parts) > 1:
        hashed = hashlib.sha1(raw.encode()).hexdigest()[:16]
        return f"{prefix}:v1:{hashed}"
    return f"{prefix}:v1:{raw}"


class RedisCache:
    def __init__(self, redis_url: str = CacheConfig.REDIS_URL):
        self._redis: Optional[aioredis.Redis] = None
        self._url = redis_url

    async def connect(self) -> None:
        self._redis = aioredis.from_url(
            self._url,
            decode_responses=False,  # we handle bytes via orjson
            socket_connect_timeout=CacheConfig.CONNECT_TIMEOUT,
            socket_timeout=CacheConfig.CONNECT_TIMEOUT,
        )
        # Verify connectivity
        await self._redis.ping()

    async def close(self) -> None:
        if self._redis:
            await self._redis.aclose()
            self._redis = None

    @property
    def connected(self) -> bool:
        return self._redis is not None

    async def ping(self) -> bool:
        """Ping Redis to verify connectivity."""
        if not self._redis:
            return False
        await self._redis.ping()
        return True

    async def get(self, key: str) -> Optional[dict]:
        """Get a cached envelope. Returns {"data": ..., "stored_at": ...} or None."""
        if not self._redis:
            return None
        raw = await self._redis.get(key)
        if raw is None:
            return None
        try:
            return orjson.loads(raw)
        except Exception:
            return None

    async def set(self, key: str, data: Any, ttl: int) -> None:
        """Store data wrapped in an envelope with stored_at timestamp."""
        if not self._redis:
            return
        envelope = {"data": data, "stored_at": time.time()}
        raw = orjson.dumps(envelope)
        await self._redis.set(key, raw, ex=ttl)

    async def mget(self, keys: list[str]) -> list[Optional[dict]]:
        """Pipeline GET for multiple keys. Returns list of envelopes (or None)."""
        if not self._redis or not keys:
            return [None] * len(keys)
        raw_values = await self._redis.mget(keys)
        results = []
        for raw in raw_values:
            if raw is None:
                results.append(None)
            else:
                try:
                    results.append(orjson.loads(raw))
                except Exception:
                    results.append(None)
        return results

    async def pipeline_set(self, items: list[tuple[str, Any, int]]) -> None:
        """Batch SET via pipeline. items = [(key, data, ttl), ...]."""
        if not self._redis or not items:
            return
        now = time.time()
        pipe = self._redis.pipeline(transaction=False)
        for key, data, ttl in items:
            envelope = {"data": data, "stored_at": now}
            pipe.set(key, orjson.dumps(envelope), ex=ttl)
        await pipe.execute()

    async def delete(self, key: str) -> None:
        if self._redis:
            await self._redis.delete(key)

    async def try_lock(self, key: str, ttl: int) -> bool:
        """Acquire a short-lived lock (NX)."""
        if not self._redis:
            return False
        return bool(await self._redis.set(key, "1", nx=True, ex=ttl))

    async def release_lock(self, key: str) -> None:
        """Release a lock key."""
        if self._redis:
            await self._redis.delete(key)

    async def wait_for_key(self, key: str, timeout: float, interval: float = 0.05) -> Optional[dict]:
        """Poll for a key to appear, returning the envelope or None on timeout."""
        if not self._redis:
            return None
        deadline = time.time() + timeout
        while time.time() < deadline:
            envelope = await self.get(key)
            if envelope is not None:
                return envelope
            await asyncio.sleep(interval)
        return None
