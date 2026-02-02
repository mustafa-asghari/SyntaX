"""
L2 Typesense cache — indexes tweets for full-text search fallback.

Auto-creates the `tweets` collection on startup. Provides:
- index_tweets(): upsert tweet dicts into Typesense
- search(): text search returning ranked tweet IDs
"""

import time
import os
from typing import Any, Optional
from urllib.parse import urlparse

import httpx

from .config import CacheConfig

TWEETS_SCHEMA = {
    "name": "tweets",
    "fields": [
        {"name": "id", "type": "string"},
        {"name": "text", "type": "string"},
        {"name": "author_username", "type": "string", "facet": True},
        {"name": "author_name", "type": "string"},
        {"name": "author_id", "type": "string", "facet": True},
        {"name": "created_at_ts", "type": "int64", "sort": True},
        {"name": "like_count", "type": "int32", "sort": True},
        {"name": "retweet_count", "type": "int32", "sort": True},
        {"name": "view_count", "type": "int64", "sort": True},
        {"name": "language", "type": "string", "facet": True},
        {"name": "is_reply", "type": "bool"},
        {"name": "is_retweet", "type": "bool"},
        {"name": "is_quote", "type": "bool"},
    ],
    "token_separators": ["@", "#"],
}


def _tweet_to_document(td: dict) -> dict:
    """Convert a tweet dict (raw X API or legacy to_dict format) to a Typesense document."""
    # Support both raw X API format and old to_dict() format
    legacy = td.get("legacy") or {}
    core = (td.get("core") or {}).get("user_results", {}).get("result", {})
    user_legacy = core.get("legacy") or {}
    user_core = core.get("core") or {}

    # ID
    tid = td.get("rest_id") or td.get("id") or legacy.get("id_str", "")

    # Text
    text = legacy.get("full_text") or td.get("text", "")

    # Author
    author_username = user_core.get("screen_name") or user_legacy.get("screen_name") or td.get("author_username", "")
    author_name = user_core.get("name") or user_legacy.get("name") or td.get("author_name", "")
    author_id = legacy.get("user_id_str") or core.get("rest_id") or td.get("author_id", "")

    # Timestamp
    created_at_ts = 0
    raw_date = legacy.get("created_at") or td.get("created_at")
    if raw_date:
        try:
            from email.utils import parsedate_to_datetime
            created_at_ts = int(parsedate_to_datetime(raw_date).timestamp())
        except Exception:
            pass

    # Counts
    views = td.get("views") or {}
    view_raw = views.get("count")
    view_count = int(view_raw) if view_raw else td.get("view_count", 0)

    return {
        "id": str(tid),
        "text": text,
        "author_username": author_username,
        "author_name": author_name,
        "author_id": str(author_id),
        "created_at_ts": created_at_ts,
        "like_count": legacy.get("favorite_count") or td.get("like_count", 0),
        "retweet_count": legacy.get("retweet_count") or td.get("retweet_count", 0),
        "view_count": view_count,
        "language": legacy.get("lang") or td.get("language", ""),
        "is_reply": bool(legacy.get("in_reply_to_status_id_str")) if legacy else td.get("is_reply", False),
        "is_retweet": bool(legacy.get("retweeted_status_result")) if legacy else td.get("is_retweet", False),
        "is_quote": legacy.get("is_quote_status", False) if legacy else td.get("is_quote", False),
    }


class TypesenseCache:
    def __init__(self):
        self._base_url = (
            f"{CacheConfig.TYPESENSE_PROTOCOL}://"
            f"{CacheConfig.TYPESENSE_HOST}:{CacheConfig.TYPESENSE_PORT}"
        )
        self._api_key = CacheConfig.TYPESENSE_API_KEY
        self._client: Optional[httpx.AsyncClient] = None
        self._available = False

    async def connect(self) -> None:
        if not CacheConfig.TYPESENSE_ENABLED or not CacheConfig.TYPESENSE_HOST:
            self._available = False
            print("[cache] Typesense disabled")
            return
        raw_url = os.getenv("TYPESENSE_URL", "").strip()
        if raw_url:
            parsed = urlparse(raw_url if "://" in raw_url else f"http://{raw_url}")
            if not parsed.hostname or parsed.scheme not in ("http", "https"):
                self._available = False
                print(f"[cache] Typesense URL invalid: {raw_url}")
                return
        if CacheConfig.TYPESENSE_PROTOCOL not in ("http", "https"):
            self._available = False
            print(f"[cache] Typesense protocol invalid: {CacheConfig.TYPESENSE_PROTOCOL}")
            return
        print(f"[cache] Typesense connecting to {self._base_url}")
        self._client = httpx.AsyncClient(
            base_url=self._base_url,
            headers={"X-TYPESENSE-API-KEY": self._api_key},
            timeout=CacheConfig.CONNECT_TIMEOUT,
        )
        try:
            resp = await self._client.get("/health")
            if resp.status_code == 200:
                self._available = True
                await self._ensure_collection()
            else:
                print(f"[cache] Typesense health check failed: {resp.status_code}")
        except Exception as e:
            print(f"[cache] Typesense unavailable ({type(e).__name__}): {e!r}")
            self._available = False

    async def close(self) -> None:
        if self._client:
            await self._client.aclose()
            self._client = None
        self._available = False

    @property
    def available(self) -> bool:
        return self._available

    async def health(self) -> bool:
        """Check Typesense health endpoint."""
        if not self._client:
            return False
        resp = await self._client.get("/health")
        return resp.status_code == 200

    async def _ensure_collection(self) -> None:
        """Create the tweets collection if it doesn't exist."""
        if not self._client:
            return
        resp = await self._client.get("/collections/tweets")
        if resp.status_code == 200:
            return  # already exists
        resp = await self._client.post("/collections", json=TWEETS_SCHEMA)
        if resp.status_code in (200, 201):
            print("[cache] Typesense 'tweets' collection created")
        else:
            print(f"[cache] Typesense collection create failed: {resp.status_code} {resp.text}")

    async def index_tweets(self, tweet_dicts: list[dict]) -> None:
        """Upsert tweets into Typesense for search indexing."""
        if not self._available or not self._client or not tweet_dicts:
            return
        try:
            import orjson
            # Use import action=upsert via JSONL
            lines = []
            for td in tweet_dicts:
                doc = _tweet_to_document(td)
                lines.append(orjson.dumps(doc).decode())
            body = "\n".join(lines)
            await self._client.post(
                "/collections/tweets/documents/import",
                content=body,
                params={"action": "upsert"},
                headers={"Content-Type": "text/plain"},
            )
        except Exception as e:
            print(f"[cache] Typesense index error: {e}")

    async def search(self, query: str, limit: int = 20) -> list[str]:
        """
        Search tweets by text. Returns a list of tweet IDs ranked by relevance.
        """
        if not self._available or not self._client:
            return []
        try:
            resp = await self._client.get(
                "/collections/tweets/documents/search",
                params={
                    "q": query,
                    "query_by": "text,author_username,author_name",
                    "sort_by": "_text_match:desc,like_count:desc",
                    "per_page": limit,
                },
            )
            if resp.status_code != 200:
                return []
            data = resp.json()
            return [hit["document"]["id"] for hit in data.get("hits", [])]
        except Exception as e:
            print(f"[cache] Typesense search error: {e}")
            return []
