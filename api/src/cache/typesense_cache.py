"""
L2 Typesense cache — indexes tweets for full-text search fallback.

Auto-creates the `tweets` collection on startup. Provides:
- index_tweets(): upsert tweet dicts into Typesense
- search(): text search returning ranked tweet IDs
"""

import time
from typing import Any, Optional

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


def _tweet_to_document(tweet_dict: dict) -> dict:
    """Convert a tweet dict (from Tweet.to_dict()) to a Typesense document."""
    # Parse created_at string to unix timestamp
    created_at_ts = 0
    if raw := tweet_dict.get("created_at"):
        try:
            from email.utils import parsedate_to_datetime
            created_at_ts = int(parsedate_to_datetime(raw).timestamp())
        except Exception:
            pass

    return {
        "id": str(tweet_dict.get("id", "")),
        "text": tweet_dict.get("text", ""),
        "author_username": tweet_dict.get("author_username", ""),
        "author_name": tweet_dict.get("author_name", ""),
        "author_id": str(tweet_dict.get("author_id", "")),
        "created_at_ts": created_at_ts,
        "like_count": tweet_dict.get("like_count", 0),
        "retweet_count": tweet_dict.get("retweet_count", 0),
        "view_count": tweet_dict.get("view_count", 0),
        "language": tweet_dict.get("language", ""),
        "is_reply": tweet_dict.get("is_reply", False),
        "is_retweet": tweet_dict.get("is_retweet", False),
        "is_quote": tweet_dict.get("is_quote", False),
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
        except Exception as e:
            print(f"[cache] Typesense unavailable: {e}")
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
