"""
SyntaX API
High-performance X/Twitter data API.
"""

import asyncio
import os
import sys
import time
from typing import Optional, List
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import ORJSONResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

# Add scraper/src to path so absolute imports (from config, from client, etc.) work
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'scraper', 'src'))

from client import XClient, create_token_set
from token_pool import get_pool, AnyTokenPool
from proxy_manager import get_proxy_manager
from endpoints.user import get_user_by_username, get_user_by_id
from endpoints.tweet import get_tweet_by_id, get_tweet_detail, get_user_tweets
from endpoints.search import search_tweets
from endpoints.social import get_followers, get_following

from cache import CacheManager
from cache.redis_cache import make_key
from cache.config import CacheConfig


# Response models
class APIResponse(BaseModel):
    success: bool
    data: Optional[dict | list] = None
    error: Optional[str] = None
    meta: dict = {}


# ── Session Pool ───────────────────────────────────────────
import random
import threading
from collections import deque
from curl_cffi import requests as curl_requests


class SessionPool:
    """
    Reuses curl-cffi sessions across API requests to skip the 200-400ms
    TLS handshake on every call.
    """

    def __init__(self, max_size: int = 8):
        self._pool: deque = deque()
        self._max_size = max_size
        self._lock = threading.Lock()

    def acquire(self, browser: str = "chrome131", proxy: Optional[dict] = None) -> curl_requests.Session:
        with self._lock:
            if self._pool:
                session = self._pool.popleft()
                session.cookies.clear()
                return session
        # Create a new session outside the lock
        session = curl_requests.Session(impersonate=browser)
        if proxy:
            session.proxies = proxy
        return session

    def release(self, session: curl_requests.Session) -> None:
        session.cookies.clear()
        with self._lock:
            if len(self._pool) < self._max_size:
                self._pool.append(session)
                return
        # Pool full — close the session
        session.close()

    def close_all(self) -> None:
        with self._lock:
            while self._pool:
                self._pool.pop().close()


# Globals
pool: Optional[AnyTokenPool] = None
session_pool: Optional[SessionPool] = None
_proxy_manager = None
cache_mgr: Optional[CacheManager] = None


def _get_client():
    """Get an XClient with a token from the pool or on-demand."""
    token_set = pool.get_token() if pool else None

    # Determine proxy
    proxy = None
    if _proxy_manager and _proxy_manager.has_proxies:
        proxy_cfg = _proxy_manager.get_proxy()
        if proxy_cfg:
            proxy = proxy_cfg.to_curl_cffi_format()

    if not token_set:
        token_set = create_token_set(proxy=proxy)
        if not token_set:
            raise HTTPException(status_code=503, detail="Unable to create authentication token")

    client = XClient(token_set=token_set, proxy=proxy, token_pool_ref=pool)
    return client, token_set


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup and shutdown events."""
    global pool, session_pool, _proxy_manager, cache_mgr

    print("Starting SyntaX API...")

    pool = get_pool()  # auto-detects Redis vs in-memory
    session_pool = SessionPool()
    _proxy_manager = get_proxy_manager()

    if _proxy_manager.has_proxies:
        print(f"Proxy manager loaded ({_proxy_manager.count} proxies)")

    print(f"Token pool initialized (size: {pool.pool_size()})")

    if pool.pool_size() == 0:
        print("Pool empty, creating initial tokens...")
        for i in range(5):
            proxy = None
            if _proxy_manager.has_proxies:
                pcfg = _proxy_manager.get_proxy()
                if pcfg:
                    proxy = pcfg.to_curl_cffi_format()
            token_set = create_token_set(proxy=proxy)
            if token_set:
                pool.add_token(token_set)
                print(f"  Created token {i+1}/5")
        print(f"Pool size: {pool.pool_size()}")

    # Initialize cache
    cache_mgr = CacheManager()
    await cache_mgr.connect()

    yield

    print("Shutting down SyntaX API...")
    if cache_mgr:
        await cache_mgr.close()
    if session_pool:
        session_pool.close_all()
    if pool:
        pool.close()


app = FastAPI(
    title="SyntaX API",
    description="High-performance X/Twitter data API. 10x faster than competitors.",
    version="0.1.0",
    default_response_class=ORJSONResponse,
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/")
async def root():
    return {
        "name": "SyntaX API",
        "version": "0.1.0",
        "status": "running",
        "docs": "/docs",
    }


@app.get("/health")
async def health():
    return {
        "status": "healthy",
        "pool_size": pool.pool_size() if pool else 0,
        "cache_redis": cache_mgr.redis.connected if cache_mgr else False,
        "cache_typesense": cache_mgr.typesense.available if cache_mgr else False,
        "cache_clickhouse": cache_mgr.clickhouse.available if cache_mgr else False,
    }


# ── User Endpoints ──────────────────────────────────────────


@app.get("/v1/users/{username}", response_model=APIResponse)
async def get_user(
    username: str,
    fresh: bool = Query(default=False, description="Bypass cache"),
):
    """Get user profile by username."""
    start_time = time.perf_counter()
    cache_key = make_key("profile", username.lower())

    async def _fetch():
        client, token_set = _get_client()
        try:
            user, api_time = await asyncio.to_thread(get_user_by_username, username, client)
            client.close()
            if pool:
                pool.return_token(token_set, success=True)
            if not user:
                raise HTTPException(status_code=404, detail=f"User @{username} not found")
            return user.to_dict()
        except HTTPException:
            raise
        except Exception as e:
            if pool:
                pool.return_token(token_set, success=False)
            raise HTTPException(status_code=500, detail=str(e))

    data, cache_layer = await cache_mgr.get_or_fetch(
        cache_key, CacheConfig.TTL_PROFILE, _fetch, fresh=fresh,
    )
    total_time = (time.perf_counter() - start_time) * 1000

    return APIResponse(
        success=True,
        data=data,
        meta={
            "response_time_ms": round(total_time, 1),
            "cache_hit": cache_layer != "live",
            "cache_layer": cache_layer,
        },
    )


@app.get("/v1/users/id/{user_id}", response_model=APIResponse)
async def get_user_by_rest_id(
    user_id: str,
    fresh: bool = Query(default=False, description="Bypass cache"),
):
    """Get user profile by numeric user ID."""
    start_time = time.perf_counter()
    cache_key = make_key("profile", user_id)

    async def _fetch():
        client, token_set = _get_client()
        try:
            user, api_time = await asyncio.to_thread(get_user_by_id, user_id, client)
            client.close()
            if pool:
                pool.return_token(token_set, success=True)
            if not user:
                raise HTTPException(status_code=404, detail=f"User {user_id} not found")
            return user.to_dict()
        except HTTPException:
            raise
        except Exception as e:
            if pool:
                pool.return_token(token_set, success=False)
            raise HTTPException(status_code=500, detail=str(e))

    data, cache_layer = await cache_mgr.get_or_fetch(
        cache_key, CacheConfig.TTL_PROFILE, _fetch, fresh=fresh,
    )
    total_time = (time.perf_counter() - start_time) * 1000

    return APIResponse(
        success=True,
        data=data,
        meta={
            "response_time_ms": round(total_time, 1),
            "cache_hit": cache_layer != "live",
            "cache_layer": cache_layer,
        },
    )


# ── Tweet Endpoints ─────────────────────────────────────────


@app.get("/v1/tweets/{tweet_id}", response_model=APIResponse)
async def get_tweet(
    tweet_id: str,
    fresh: bool = Query(default=False, description="Bypass cache"),
):
    """Get a single tweet by ID."""
    start_time = time.perf_counter()
    cache_key = make_key("tweet", tweet_id)

    async def _fetch():
        client, token_set = _get_client()
        try:
            tweet, api_time = await asyncio.to_thread(get_tweet_by_id, tweet_id, client)
            client.close()
            if pool:
                pool.return_token(token_set, success=True)
            if not tweet:
                raise HTTPException(status_code=404, detail=f"Tweet {tweet_id} not found")
            return tweet.to_dict()
        except HTTPException:
            raise
        except Exception as e:
            if pool:
                pool.return_token(token_set, success=False)
            raise HTTPException(status_code=500, detail=str(e))

    data, cache_layer = await cache_mgr.get_or_fetch(
        cache_key, CacheConfig.TTL_TWEET, _fetch, fresh=fresh,
    )
    total_time = (time.perf_counter() - start_time) * 1000

    return APIResponse(
        success=True,
        data=data,
        meta={
            "response_time_ms": round(total_time, 1),
            "cache_hit": cache_layer != "live",
            "cache_layer": cache_layer,
        },
    )


@app.get("/v1/tweets/{tweet_id}/detail", response_model=APIResponse)
async def get_tweet_with_replies(
    tweet_id: str,
    fresh: bool = Query(default=False, description="Bypass cache"),
):
    """Get tweet detail with conversation thread."""
    start_time = time.perf_counter()
    cache_key = make_key("tweet_detail", tweet_id)

    async def _fetch():
        client, token_set = _get_client()
        try:
            main_tweet, replies, api_time = await asyncio.to_thread(get_tweet_detail, tweet_id, client)
            client.close()
            if pool:
                pool.return_token(token_set, success=True)
            if not main_tweet:
                raise HTTPException(status_code=404, detail=f"Tweet {tweet_id} not found")
            return {
                "tweet": main_tweet.to_dict(),
                "replies": [r.to_dict() for r in replies],
                "reply_count": len(replies),
            }
        except HTTPException:
            raise
        except Exception as e:
            if pool:
                pool.return_token(token_set, success=False)
            raise HTTPException(status_code=500, detail=str(e))

    data, cache_layer = await cache_mgr.get_or_fetch(
        cache_key, CacheConfig.TTL_TWEET_DETAIL, _fetch, fresh=fresh,
    )
    total_time = (time.perf_counter() - start_time) * 1000

    return APIResponse(
        success=True,
        data=data,
        meta={
            "response_time_ms": round(total_time, 1),
            "cache_hit": cache_layer != "live",
            "cache_layer": cache_layer,
        },
    )


@app.get("/v1/users/{user_id}/tweets", response_model=APIResponse)
async def get_tweets_by_user(
    user_id: str,
    count: int = Query(default=20, le=40),
    cursor: Optional[str] = Query(default=None),
    fresh: bool = Query(default=False, description="Bypass cache"),
):
    """Get tweets from a user's timeline. Requires numeric user_id."""
    start_time = time.perf_counter()
    cache_key = make_key("user_tweets", user_id, str(count), str(cursor or ""))

    async def _fetch():
        client, token_set = _get_client()
        try:
            tweets, next_cursor, api_time = await asyncio.to_thread(
                get_user_tweets, user_id, client, count, cursor,
            )
            client.close()
            if pool:
                pool.return_token(token_set, success=True)
            return {
                "tweets": [t.to_dict() for t in tweets],
                "next_cursor": next_cursor,
            }
        except HTTPException:
            raise
        except Exception as e:
            if pool:
                pool.return_token(token_set, success=False)
            raise HTTPException(status_code=500, detail=str(e))

    data, cache_layer = await cache_mgr.get_or_fetch(
        cache_key, CacheConfig.TTL_USER_TWEETS, _fetch, fresh=fresh,
    )
    total_time = (time.perf_counter() - start_time) * 1000

    return APIResponse(
        success=True,
        data=data["tweets"],
        meta={
            "response_time_ms": round(total_time, 1),
            "count": len(data["tweets"]),
            "next_cursor": data.get("next_cursor"),
            "cache_hit": cache_layer != "live",
            "cache_layer": cache_layer,
        },
    )


# ── Search Endpoints ────────────────────────────────────────


@app.get("/v1/search", response_model=APIResponse)
async def search(
    q: str = Query(..., description="Search query"),
    count: int = Query(default=20, le=40),
    product: str = Query(default="Top", description="Top, Latest, People, Photos, Videos"),
    cursor: Optional[str] = Query(default=None),
    fresh: bool = Query(default=False, description="Bypass cache"),
):
    """Search for tweets."""
    start_time = time.perf_counter()

    async def _fetch():
        client, token_set = _get_client()
        try:
            tweets, next_cursor, api_time = await asyncio.to_thread(
                search_tweets, q, client, count, product, cursor,
            )
            client.close()
            if pool:
                pool.return_token(token_set, success=True)
            return [t.to_dict() for t in tweets], next_cursor
        except HTTPException:
            raise
        except Exception as e:
            if pool:
                pool.return_token(token_set, success=False)
            raise HTTPException(status_code=500, detail=str(e))

    tweet_dicts, next_cursor, cache_layer = await cache_mgr.search_with_typesense_fallback(
        query=q,
        product=product,
        count=count,
        cursor=cursor,
        fetch_fn=_fetch,
        fresh=fresh,
    )
    total_time = (time.perf_counter() - start_time) * 1000

    return APIResponse(
        success=True,
        data=tweet_dicts,
        meta={
            "response_time_ms": round(total_time, 1),
            "count": len(tweet_dicts),
            "next_cursor": next_cursor,
            "cache_hit": cache_layer != "live",
            "cache_layer": cache_layer,
        },
    )


# ── Social Endpoints ────────────────────────────────────────


@app.get("/v1/users/{user_id}/followers", response_model=APIResponse)
async def get_user_followers(
    user_id: str,
    count: int = Query(default=20, le=40),
    cursor: Optional[str] = Query(default=None),
    fresh: bool = Query(default=False, description="Bypass cache"),
):
    """Get a user's followers. Requires numeric user_id. Auth-gated."""
    start_time = time.perf_counter()
    cache_key = make_key("social", "followers", user_id, str(count), str(cursor or ""))

    async def _fetch():
        client, token_set = _get_client()
        try:
            users, next_cursor, api_time = await asyncio.to_thread(
                get_followers, user_id, client, count, cursor,
            )
            client.close()
            if pool:
                pool.return_token(token_set, success=True)
            return {
                "users": [u.to_dict() for u in users],
                "next_cursor": next_cursor,
            }
        except HTTPException:
            raise
        except Exception as e:
            if pool:
                pool.return_token(token_set, success=False)
            raise HTTPException(status_code=500, detail=str(e))

    data, cache_layer = await cache_mgr.get_or_fetch(
        cache_key, CacheConfig.TTL_SOCIAL, _fetch, fresh=fresh,
    )
    total_time = (time.perf_counter() - start_time) * 1000

    return APIResponse(
        success=True,
        data=data["users"],
        meta={
            "response_time_ms": round(total_time, 1),
            "count": len(data["users"]),
            "next_cursor": data.get("next_cursor"),
            "cache_hit": cache_layer != "live",
            "cache_layer": cache_layer,
        },
    )


@app.get("/v1/users/{user_id}/following", response_model=APIResponse)
async def get_user_following(
    user_id: str,
    count: int = Query(default=20, le=40),
    cursor: Optional[str] = Query(default=None),
    fresh: bool = Query(default=False, description="Bypass cache"),
):
    """Get users that a user follows. Requires numeric user_id. Auth-gated."""
    start_time = time.perf_counter()
    cache_key = make_key("social", "following", user_id, str(count), str(cursor or ""))

    async def _fetch():
        client, token_set = _get_client()
        try:
            users, next_cursor, api_time = await asyncio.to_thread(
                get_following, user_id, client, count, cursor,
            )
            client.close()
            if pool:
                pool.return_token(token_set, success=True)
            return {
                "users": [u.to_dict() for u in users],
                "next_cursor": next_cursor,
            }
        except HTTPException:
            raise
        except Exception as e:
            if pool:
                pool.return_token(token_set, success=False)
            raise HTTPException(status_code=500, detail=str(e))

    data, cache_layer = await cache_mgr.get_or_fetch(
        cache_key, CacheConfig.TTL_SOCIAL, _fetch, fresh=fresh,
    )
    total_time = (time.perf_counter() - start_time) * 1000

    return APIResponse(
        success=True,
        data=data["users"],
        meta={
            "response_time_ms": round(total_time, 1),
            "count": len(data["users"]),
            "next_cursor": data.get("next_cursor"),
            "cache_hit": cache_layer != "live",
            "cache_layer": cache_layer,
        },
    )


# ── Admin Endpoints ─────────────────────────────────────────


@app.get("/v1/pool/stats")
async def pool_stats():
    """Get token pool statistics."""
    if not pool:
        return {"error": "Pool not initialized"}
    return pool.pool_stats()


# Run with: uvicorn api.src.main:app --reload
if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
