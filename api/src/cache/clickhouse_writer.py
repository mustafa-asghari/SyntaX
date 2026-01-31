"""
Background batched ClickHouse writer.

Buffers tweets and search query logs, flushes every N seconds.
Uses clickhouse-connect for async-compatible HTTP inserts.
"""

import asyncio
import time
from typing import Any

import clickhouse_connect

from .config import CacheConfig


class ClickHouseWriter:
    def __init__(self):
        self._client = None
        self._tweet_buffer: list[dict] = []
        self._query_buffer: list[dict] = []
        self._flush_task: asyncio.Task | None = None
        self._available = False

    async def connect(self) -> None:
        try:
            self._client = clickhouse_connect.get_client(
                host=CacheConfig.CLICKHOUSE_HOST,
                port=int(CacheConfig.CLICKHOUSE_PORT),
                username=CacheConfig.CLICKHOUSE_USER,
                password=CacheConfig.CLICKHOUSE_PASSWORD,
                database=CacheConfig.CLICKHOUSE_DB,
            )
            # Verify
            self._client.query("SELECT 1")
            self._available = True
            self._flush_task = asyncio.create_task(self._flush_loop())
            print("[cache] ClickHouse writer connected")
        except Exception as e:
            print(f"[cache] ClickHouse unavailable: {e}")
            self._available = False

    async def close(self) -> None:
        if self._flush_task:
            self._flush_task.cancel()
            try:
                await self._flush_task
            except asyncio.CancelledError:
                pass
        # Final flush
        if self._available:
            await self._flush()
        if self._client:
            self._client.close()
            self._client = None
        self._available = False

    @property
    def available(self) -> bool:
        return self._available

    def buffer_tweets(self, tweet_dicts: list[dict]) -> None:
        """Add tweet dicts to the write buffer."""
        if not self._available:
            return
        self._tweet_buffer.extend(tweet_dicts)

    def buffer_search_query(
        self,
        query: str,
        product: str,
        result_count: int,
        cache_hit: bool,
        response_time_ms: float,
    ) -> None:
        """Log a search query for analytics."""
        if not self._available:
            return
        self._query_buffer.append({
            "query": query,
            "product": product,
            "result_count": result_count,
            "cache_hit": 1 if cache_hit else 0,
            "response_time_ms": response_time_ms,
        })

    async def _flush_loop(self) -> None:
        while True:
            await asyncio.sleep(CacheConfig.CH_FLUSH_INTERVAL)
            await self._flush()

    async def _flush(self) -> None:
        await self._flush_tweets()
        await self._flush_queries()

    async def _flush_tweets(self) -> None:
        if not self._tweet_buffer or not self._client:
            return
        batch = self._tweet_buffer[:]
        self._tweet_buffer.clear()
        try:
            rows = []
            columns = [
                "tweet_id", "author_id", "author_username", "text",
                "likes", "retweets", "replies", "quotes", "views", "bookmarks",
                "is_reply", "is_retweet", "is_quote", "language",
            ]
            for td in batch:
                rows.append([
                    str(td.get("id", "")),
                    str(td.get("author_id", "")),
                    td.get("author_username", ""),
                    td.get("text", ""),
                    td.get("like_count", 0),
                    td.get("retweet_count", 0),
                    td.get("reply_count", 0),
                    td.get("quote_count", 0),
                    td.get("view_count", 0),
                    td.get("bookmark_count", 0),
                    1 if td.get("is_reply") else 0,
                    1 if td.get("is_retweet") else 0,
                    1 if td.get("is_quote") else 0,
                    td.get("language", ""),
                ])
            await asyncio.to_thread(
                self._client.insert, "tweets", rows, column_names=columns,
            )
        except Exception as e:
            print(f"[cache] ClickHouse tweet flush error: {e}")

    async def _flush_queries(self) -> None:
        if not self._query_buffer or not self._client:
            return
        batch = self._query_buffer[:]
        self._query_buffer.clear()
        try:
            columns = ["query", "product", "result_count", "cache_hit", "response_time_ms"]
            rows = [
                [q["query"], q["product"], q["result_count"], q["cache_hit"], q["response_time_ms"]]
                for q in batch
            ]
            await asyncio.to_thread(
                self._client.insert, "search_queries", rows, column_names=columns,
            )
        except Exception as e:
            print(f"[cache] ClickHouse query flush error: {e}")
