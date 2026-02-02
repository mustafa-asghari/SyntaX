"""
Background batched ClickHouse writer.

Buffers tweets and search query logs, flushes every N seconds.
Uses clickhouse-connect for async-compatible HTTP inserts.
"""

import asyncio
import time
from pathlib import Path
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
            def _init_client():
                client = clickhouse_connect.get_client(
                    host=CacheConfig.CLICKHOUSE_HOST,
                    port=int(CacheConfig.CLICKHOUSE_PORT),
                    username=CacheConfig.CLICKHOUSE_USER,
                    password=CacheConfig.CLICKHOUSE_PASSWORD,
                    database=CacheConfig.CLICKHOUSE_DB,
                    connect_timeout=CacheConfig.CONNECT_TIMEOUT,
                    send_receive_timeout=CacheConfig.CONNECT_TIMEOUT,
                )
                client.query("SELECT 1")
                return client

            self._client = await asyncio.wait_for(
                asyncio.to_thread(_init_client),
                timeout=CacheConfig.CONNECT_TIMEOUT,
            )
            await self._bootstrap_schema()
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

    async def health(self) -> bool:
        """Check ClickHouse connectivity with a lightweight query."""
        if not self._client:
            return False
        await asyncio.to_thread(self._client.query, "SELECT 1")
        return True

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

    async def _bootstrap_schema(self) -> None:
        """Run init SQL to ensure required tables exist."""
        if not CacheConfig.CLICKHOUSE_BOOTSTRAP or not self._client:
            return

        sql_path = Path(CacheConfig.CLICKHOUSE_INIT_SQL_PATH)
        if not sql_path.exists():
            print(f"[cache] ClickHouse init SQL not found: {sql_path}")
            return

        def _run_sql():
            raw = sql_path.read_text()
            lines = []
            for line in raw.splitlines():
                stripped = line.strip()
                if not stripped or stripped.startswith("--"):
                    continue
                lines.append(line)
            cleaned = "\n".join(lines)
            statements = [s.strip() for s in cleaned.split(";") if s.strip()]
            for stmt in statements:
                self._client.command(stmt)

        try:
            await asyncio.to_thread(_run_sql)
            print(f"[cache] ClickHouse schema ensured via {sql_path}")
        except Exception as e:
            print(f"[cache] ClickHouse schema init error: {e}")

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
                # Support both raw X API format and old to_dict() format
                legacy = td.get("legacy") or {}
                core = (td.get("core") or {}).get("user_results", {}).get("result", {})
                user_legacy = core.get("legacy") or {}
                user_core = core.get("core") or {}
                views = td.get("views") or {}
                view_raw = views.get("count")
                rows.append([
                    str(td.get("rest_id") or td.get("id") or legacy.get("id_str", "")),
                    str(legacy.get("user_id_str") or core.get("rest_id") or td.get("author_id", "")),
                    user_core.get("screen_name") or user_legacy.get("screen_name") or td.get("author_username", ""),
                    legacy.get("full_text") or td.get("text", ""),
                    legacy.get("favorite_count") or td.get("like_count", 0),
                    legacy.get("retweet_count") or td.get("retweet_count", 0),
                    legacy.get("reply_count") or td.get("reply_count", 0),
                    legacy.get("quote_count") or td.get("quote_count", 0),
                    int(view_raw) if view_raw else td.get("view_count", 0),
                    legacy.get("bookmark_count") or td.get("bookmark_count", 0),
                    1 if legacy.get("in_reply_to_status_id_str") or td.get("is_reply") else 0,
                    1 if legacy.get("retweeted_status_result") or td.get("is_retweet") else 0,
                    1 if legacy.get("is_quote_status") or td.get("is_quote") else 0,
                    legacy.get("lang") or td.get("language", ""),
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
