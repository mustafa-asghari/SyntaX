"""
CacheManager — facade over Redis (L1), Typesense (L2), ClickHouse, and Coalescer.

Provides:
- get_or_fetch(): generic cache-aside for all endpoints
- search_with_typesense_fallback(): search-specific with L2 Typesense fallback
- SWR (stale-while-revalidate) at configurable threshold
"""

import asyncio
import time
from typing import Any, Callable, Awaitable, Optional

from .config import CacheConfig
from .redis_cache import RedisCache, make_key
from .typesense_cache import TypesenseCache
from .clickhouse_writer import ClickHouseWriter
from .coalescer import Coalescer


class CacheManager:
    def __init__(self):
        self.redis = RedisCache()
        self.typesense = TypesenseCache()
        self.clickhouse = ClickHouseWriter()
        self.coalescer = Coalescer()

    async def connect(self) -> None:
        """Connect all cache backends. Non-fatal — degrades gracefully."""
        try:
            await self.redis.connect()
            print("[cache] Redis connected")
        except Exception as e:
            print(f"[cache] Redis unavailable: {e}")

        try:
            await self.typesense.connect()
        except Exception as e:
            print(f"[cache] Typesense unavailable: {e}")

        try:
            await self.clickhouse.connect()
        except Exception as e:
            print(f"[cache] ClickHouse unavailable: {e}")

    async def close(self) -> None:
        await self.redis.close()
        await self.typesense.close()
        await self.clickhouse.close()

    # ── Generic cache-aside ──────────────────────────────────

    async def get_or_fetch(
        self,
        cache_key: str,
        ttl: int,
        fetch_fn: Callable[[], Awaitable[Any]],
        fresh: bool = False,
    ) -> tuple[Any, str]:
        """
        Generic cache-aside with SWR.

        Returns:
            (data, cache_layer) where cache_layer is "redis", "live", or "swr"
        """
        if fresh:
            data = await self.coalescer.do(cache_key, fetch_fn)
            await self.redis.set(cache_key, data, ttl)
            return data, "live"

        # Try Redis L1
        envelope = await self.redis.get(cache_key)
        if envelope is not None:
            age = time.time() - envelope.get("stored_at", 0)
            if age < CacheConfig.SWR_THRESHOLD:
                return envelope["data"], "redis"
            # SWR: return stale, refresh in background
            asyncio.create_task(self._swr_refresh(cache_key, ttl, fetch_fn))
            return envelope["data"], "swr"

        # Cache miss — coalesce concurrent requests
        data = await self.coalescer.do(cache_key, fetch_fn)
        await self.redis.set(cache_key, data, ttl)
        return data, "live"

    async def _swr_refresh(
        self,
        cache_key: str,
        ttl: int,
        fetch_fn: Callable[[], Awaitable[Any]],
    ) -> None:
        """Background SWR refresh."""
        try:
            data = await fetch_fn()
            await self.redis.set(cache_key, data, ttl)
        except Exception as e:
            print(f"[cache] SWR refresh failed for {cache_key}: {e}")

    # ── Search-specific with Typesense L2 ────────────────────

    async def search_with_typesense_fallback(
        self,
        query: str,
        product: str,
        count: int,
        cursor: Optional[str],
        fetch_fn: Callable[[], Awaitable[tuple[list[dict], Optional[str]]]],
        fresh: bool = False,
    ) -> tuple[list[dict], Optional[str], str]:
        """
        Search with full cache hierarchy: Redis → Typesense → Live.

        fetch_fn should return (tweet_dicts, next_cursor).
        Returns (tweet_dicts, next_cursor, cache_layer).
        """
        cache_key = make_key("search", query, product, str(count), str(cursor or ""))
        start = time.time()

        if fresh:
            tweet_dicts, next_cursor = await self.coalescer.do(cache_key, fetch_fn)
            await self._write_through_search(cache_key, tweet_dicts, next_cursor)
            self._log_search_query(query, product, len(tweet_dicts), False, time.time() - start)
            return tweet_dicts, next_cursor, "live"

        # L1: Redis full response
        envelope = await self.redis.get(cache_key)
        if envelope is not None:
            age = time.time() - envelope.get("stored_at", 0)
            cached = envelope["data"]
            self._log_search_query(query, product, len(cached.get("tweets", [])), True, time.time() - start)
            if age < CacheConfig.SWR_THRESHOLD:
                return cached["tweets"], cached.get("next_cursor"), "redis"
            # SWR
            asyncio.create_task(
                self._swr_refresh_search(cache_key, query, product, count, cursor, fetch_fn)
            )
            return cached["tweets"], cached.get("next_cursor"), "swr"

        # L2: Typesense search → hydrate from Redis individual tweets
        if self.typesense.available and not cursor:
            tweet_ids = await self.typesense.search(query, limit=count)
            if tweet_ids:
                hydrated = await self._hydrate_tweets(tweet_ids)
                if hydrated and len(hydrated) >= len(tweet_ids) * 0.8:
                    # Good enough — cache the response and return
                    await self.redis.set(
                        cache_key,
                        {"tweets": hydrated, "next_cursor": None},
                        CacheConfig.TTL_SEARCH,
                    )
                    self._log_search_query(query, product, len(hydrated), True, time.time() - start)
                    return hydrated, None, "typesense"

        # L3: Live fetch
        tweet_dicts, next_cursor = await self.coalescer.do(cache_key, fetch_fn)
        await self._write_through_search(cache_key, tweet_dicts, next_cursor)
        self._log_search_query(query, product, len(tweet_dicts), False, time.time() - start)
        return tweet_dicts, next_cursor, "live"

    async def _hydrate_tweets(self, tweet_ids: list[str]) -> list[dict]:
        """Hydrate tweet IDs from Redis individual tweet cache."""
        keys = [make_key("tweet", tid) for tid in tweet_ids]
        envelopes = await self.redis.mget(keys)
        results = []
        for env in envelopes:
            if env is not None:
                results.append(env["data"])
        return results

    async def _write_through_search(
        self,
        cache_key: str,
        tweet_dicts: list[dict],
        next_cursor: Optional[str],
    ) -> None:
        """Write search results through all cache layers."""
        # Redis: full response
        await self.redis.set(
            cache_key,
            {"tweets": tweet_dicts, "next_cursor": next_cursor},
            CacheConfig.TTL_SEARCH,
        )

        # Redis: individual tweets
        if tweet_dicts:
            items = [
                (make_key("tweet", td["id"]), td, CacheConfig.TTL_TWEET)
                for td in tweet_dicts
                if td.get("id")
            ]
            if items:
                await self.redis.pipeline_set(items)

        # Typesense: index
        if tweet_dicts:
            asyncio.create_task(self.typesense.index_tweets(tweet_dicts))

        # ClickHouse: buffer
        if tweet_dicts:
            self.clickhouse.buffer_tweets(tweet_dicts)

    async def _swr_refresh_search(
        self,
        cache_key: str,
        query: str,
        product: str,
        count: int,
        cursor: Optional[str],
        fetch_fn: Callable[[], Awaitable[tuple[list[dict], Optional[str]]]],
    ) -> None:
        """Background SWR refresh for search."""
        try:
            tweet_dicts, next_cursor = await fetch_fn()
            await self._write_through_search(cache_key, tweet_dicts, next_cursor)
        except Exception as e:
            print(f"[cache] SWR search refresh failed: {e}")

    def _log_search_query(
        self,
        query: str,
        product: str,
        result_count: int,
        cache_hit: bool,
        elapsed_s: float,
    ) -> None:
        self.clickhouse.buffer_search_query(
            query=query,
            product=product,
            result_count=result_count,
            cache_hit=cache_hit,
            response_time_ms=elapsed_s * 1000,
        )
