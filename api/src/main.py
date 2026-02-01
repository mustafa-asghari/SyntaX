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
from account_pool import get_account_pool

from .cache import CacheManager
from .cache.redis_cache import make_key
from .cache.config import CacheConfig


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


def _proxy_key(proxy: Optional[dict]) -> str:
    """Stable string key for a proxy dict (or '' for direct)."""
    if not proxy:
        return ""
    return proxy.get("https") or proxy.get("http") or ""


class SessionPool:
    """
    Proxy-aware pool of curl-cffi sessions.

    Sessions are bucketed by proxy URL so a session warmed through proxy A
    is never handed out for proxy B.  libcurl keeps a per-handle connection
    cache (DNS → TCP → TLS); reusing the same handle for the same proxy
    skips all three setup steps.
    """

    _PREWARM_URL = "https://api.x.com/"
    _PREWARM_TIMEOUT = (5, 2)

    def __init__(self, max_per_proxy: int = 0):
        # max_per_proxy=0 means "read from env at first use"
        self._max_per_proxy = max_per_proxy or int(
            os.environ.get("SESSION_POOL_SIZE", "8")
        )
        # proxy_key → deque[Session]
        self._buckets: dict[str, deque] = {}
        self._lock = threading.Lock()

    def _create_warm_session(
        self, browser: str = "chrome131", proxy: Optional[dict] = None,
    ) -> curl_requests.Session:
        """Create a session and TLS-handshake to api.x.com."""
        session = curl_requests.Session(impersonate=browser)
        if proxy:
            session.proxies = proxy
        try:
            session.head(
                self._PREWARM_URL,
                headers={"User-Agent": "Mozilla/5.0"},
                timeout=self._PREWARM_TIMEOUT,
            )
            session.cookies.clear()
        except Exception:
            pass  # best-effort
        return session

    def prewarm(self, count: int = 4, browser: str = "chrome131",
                proxy: Optional[dict] = None) -> None:
        """Pre-warm *count* sessions for a specific proxy (or direct)."""
        key = _proxy_key(proxy)
        for _ in range(count):
            session = self._create_warm_session(browser, proxy)
            with self._lock:
                bucket = self._buckets.setdefault(key, deque())
                if len(bucket) < self._max_per_proxy:
                    bucket.append(session)
                else:
                    session.close()
        print(f"[SessionPool] Pre-warmed {count} sessions (proxy={'direct' if not key else key[:40]})")

    def acquire(self, browser: str = "chrome131", proxy: Optional[dict] = None) -> curl_requests.Session:
        key = _proxy_key(proxy)
        with self._lock:
            bucket = self._buckets.get(key)
            if bucket:
                session = bucket.popleft()
                if not bucket:
                    del self._buckets[key]
                session.cookies.clear()
                return session
        # Pool empty for this proxy — plain session, TLS on first real request
        session = curl_requests.Session(impersonate=browser)
        if proxy:
            session.proxies = proxy
        return session

    def release(self, session: curl_requests.Session, proxy: Optional[dict] = None) -> None:
        session.cookies.clear()
        key = _proxy_key(proxy)
        with self._lock:
            bucket = self._buckets.setdefault(key, deque())
            if len(bucket) < self._max_per_proxy:
                bucket.append(session)
                return
        session.close()

    def close_all(self) -> None:
        with self._lock:
            for bucket in self._buckets.values():
                while bucket:
                    bucket.pop().close()
            self._buckets.clear()


# Globals
pool: Optional[AnyTokenPool] = None
session_pool: Optional[SessionPool] = None
_proxy_manager = None
cache_mgr: Optional[CacheManager] = None


def _get_client():
    """Get an XClient with a token from the pool or on-demand.

    Returns (client, token_set, session, proxy).  The caller MUST
    release the session back via ``session_pool.release(session, proxy=proxy)``.

    Kept synchronous — pool.get_token() is a single Redis ZPOPMAX (~0.1ms
    local) so thread-dispatch overhead would cost more than it saves.
    The only heavy path (create_token_set) is guarded behind pool-empty.
    """
    token_set = pool.get_token() if pool else None

    if token_set and token_set.proxy:
        proxy = token_set.proxy
    else:
        proxy = None
        if _proxy_manager and _proxy_manager.has_proxies:
            proxy_cfg = _proxy_manager.get_proxy()
            if proxy_cfg:
                proxy = proxy_cfg.to_curl_cffi_format()

    if not token_set:
        # Fallback: no pre-warmed token available — create inline.
        # This is rare when TokenManager is running.
        token_set = create_token_set(proxy=proxy)
        if not token_set:
            raise HTTPException(status_code=503, detail="Unable to create authentication token")

    session = session_pool.acquire(proxy=proxy) if session_pool else None
    client = XClient(token_set=token_set, proxy=proxy, token_pool_ref=pool,
                     session=session)
    return client, token_set, session, proxy


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

    # Pre-warm TLS sessions so first requests are fast
    session_pool.prewarm(count=8)

    # Pre-warm auth account sessions (for search, tweet detail, social)
    # Higher count avoids cold TLS handshakes (~700ms) under concurrent load
    acct_pool = get_account_pool()
    if acct_pool.has_accounts:
        acct_pool.prewarm_all(sessions_per_account=8)

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


@app.get("/debug/health")
async def debug_health():
    """Active backend checks for production debugging."""
    results = await cache_mgr.probe() if cache_mgr else {}
    return {
        "status": "ok",
        "pool_size": pool.pool_size() if pool else 0,
        "backends": results,
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
        client, token_set, session, proxy = _get_client()
        try:
            user, api_time = await asyncio.to_thread(get_user_by_username, username, client)
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
        finally:
            client.close()
            if session and session_pool:
                session_pool.release(session, proxy=proxy)

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
        client, token_set, session, proxy = _get_client()
        try:
            user, api_time = await asyncio.to_thread(get_user_by_id, user_id, client)
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
        finally:
            client.close()
            if session and session_pool:
                session_pool.release(session, proxy=proxy)

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
        client, token_set, session, proxy = _get_client()
        try:
            tweet, api_time = await asyncio.to_thread(get_tweet_by_id, tweet_id, client)
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
        finally:
            client.close()
            if session and session_pool:
                session_pool.release(session, proxy=proxy)

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
        client, token_set, session, proxy = _get_client()
        try:
            main_tweet, replies, api_time = await asyncio.to_thread(get_tweet_detail, tweet_id, client)
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
        finally:
            client.close()
            if session and session_pool:
                session_pool.release(session, proxy=proxy)

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
        client, token_set, session, proxy = _get_client()
        try:
            tweets, next_cursor, api_time = await asyncio.to_thread(
                get_user_tweets, user_id, client, count, cursor,
            )
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
        finally:
            client.close()
            if session and session_pool:
                session_pool.release(session, proxy=proxy)

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
        # Search is auth-gated — skip guest token, go straight to account pool.
        # search_tweets() handles account acquisition internally.
        acct_pool = get_account_pool()
        account = acct_pool.acquire() if acct_pool.has_accounts else None

        if account:
            # Fast path: use account's pre-warmed session directly
            from client import token_set_from_account
            auth_ts = token_set_from_account(account)
            session = account.acquire_session()
            auth_client = XClient(token_set=auth_ts, proxy=account.proxy_dict,
                                  session=session)
            try:
                tweets, next_cursor, api_time = await asyncio.to_thread(
                    search_tweets, q, auth_client, count, product, cursor,
                )
                acct_pool.release(account, success=True, status_code=200)
                return [t.to_dict() for t in tweets], next_cursor
            except Exception as e:
                status = 429 if "429" in str(e) else 403 if "403" in str(e) else 500
                acct_pool.release(account, success=False, status_code=status)
                raise HTTPException(status_code=status, detail=str(e))
            finally:
                auth_client.close()
                account.release_session(session)
        else:
            # No accounts — fall back to guest token (will likely fail for search)
            client, token_set, session, proxy = _get_client()
            try:
                tweets, next_cursor, api_time = await asyncio.to_thread(
                    search_tweets, q, client, count, product, cursor,
                )
                if pool:
                    pool.return_token(token_set, success=True)
                return [t.to_dict() for t in tweets], next_cursor
            except Exception as e:
                if pool:
                    pool.return_token(token_set, success=False)
                raise HTTPException(status_code=500, detail=str(e))
            finally:
                client.close()
                if session and session_pool:
                    session_pool.release(session, proxy=proxy)

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
        client, token_set, session, proxy = _get_client()
        try:
            users, next_cursor, api_time = await asyncio.to_thread(
                get_followers, user_id, client, count, cursor,
            )
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
        finally:
            client.close()
            if session and session_pool:
                session_pool.release(session, proxy=proxy)

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
        client, token_set, session, proxy = _get_client()
        try:
            users, next_cursor, api_time = await asyncio.to_thread(
                get_following, user_id, client, count, cursor,
            )
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
        finally:
            client.close()
            if session and session_pool:
                session_pool.release(session, proxy=proxy)

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
