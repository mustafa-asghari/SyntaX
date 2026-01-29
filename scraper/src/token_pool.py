"""
SyntaX Token Pool Manager
Manages a pool of pre-warmed tokens in Redis for zero-latency token acquisition.
"""

import time
import asyncio
from typing import Optional, List
from dataclasses import dataclass

import redis
import orjson

from .config import (
    REDIS_KEYS,
    TOKEN_CONFIG,
    get_redis_url,
)
from .client import TokenSet, create_token_set


class TokenPool:
    """
    Redis-backed token pool for high-performance token management.

    Features:
    - Pre-warmed pool of tokens (never wait for token generation)
    - Health scoring (prefer healthier tokens)
    - Automatic expiration handling
    - Atomic token acquisition
    """

    def __init__(self, redis_url: Optional[str] = None):
        self.redis_url = redis_url or get_redis_url()
        self._redis: Optional[redis.Redis] = None

    @property
    def redis_client(self) -> redis.Redis:
        """Lazy-load Redis connection."""
        if self._redis is None:
            self._redis = redis.from_url(self.redis_url, decode_responses=True)
        return self._redis

    def _token_key(self, token_id: str) -> str:
        """Get Redis key for a token set."""
        return f"{REDIS_KEYS['token_set']}{token_id}"

    def add_token(self, token_set: TokenSet) -> str:
        """
        Add a token set to the pool.

        Returns the token ID.
        """
        token_id = f"{int(token_set.created_at * 1000)}"

        # Store token data
        self.redis_client.hset(
            self._token_key(token_id),
            mapping={
                "guest_token": token_set.guest_token,
                "csrf_token": token_set.csrf_token,
                "created_at": str(token_set.created_at),
                "cf_cookie": token_set.cf_cookie or "",
                "request_count": str(token_set.request_count),
            }
        )

        # Set expiration (CF cookie TTL)
        self.redis_client.expire(
            self._token_key(token_id),
            TOKEN_CONFIG["cf_cookie_ttl"]
        )

        # Add to sorted set (score = health, higher = better)
        self.redis_client.zadd(
            REDIS_KEYS["token_pool"],
            {token_id: 1.0}
        )

        return token_id

    def get_token(self) -> Optional[TokenSet]:
        """
        Get a token from the pool (highest health score).

        Uses atomic operations to prevent race conditions.
        """
        # Get token with highest score
        result = self.redis_client.zpopmax(REDIS_KEYS["token_pool"])
        if not result:
            return None

        token_id, score = result[0]

        # Get token data
        data = self.redis_client.hgetall(self._token_key(token_id))
        if not data:
            return None

        token_set = TokenSet(
            guest_token=data["guest_token"],
            csrf_token=data["csrf_token"],
            created_at=float(data["created_at"]),
            cf_cookie=data.get("cf_cookie") or None,
            request_count=int(data.get("request_count", 0)),
        )

        # Check if token is still valid
        age = time.time() - token_set.created_at
        if age > TOKEN_CONFIG["guest_token_ttl"]:
            # Token expired, don't return it
            self.redis_client.delete(self._token_key(token_id))
            return self.get_token()  # Try next token

        return token_set

    def return_token(self, token_set: TokenSet, success: bool = True) -> None:
        """
        Return a token to the pool after use.

        Adjusts health score based on success/failure.
        """
        token_id = f"{int(token_set.created_at * 1000)}"

        # Check if token is still valid
        age = time.time() - token_set.created_at
        if age > TOKEN_CONFIG["guest_token_ttl"]:
            # Expired, don't return
            self.redis_client.delete(self._token_key(token_id))
            return

        # Check request count
        if token_set.request_count >= TOKEN_CONFIG["max_requests_per_token"]:
            # Too many requests, retire token
            self.redis_client.delete(self._token_key(token_id))
            return

        # Calculate new health score
        base_score = 1.0
        if not success:
            base_score -= 0.2

        # Penalize older tokens
        age_penalty = age / TOKEN_CONFIG["guest_token_ttl"] * 0.3
        health_score = max(0.1, base_score - age_penalty)

        # Update token data
        self.redis_client.hset(
            self._token_key(token_id),
            "request_count",
            str(token_set.request_count)
        )

        # Return to pool with updated score
        self.redis_client.zadd(
            REDIS_KEYS["token_pool"],
            {token_id: health_score}
        )

    def pool_size(self) -> int:
        """Get current pool size."""
        return self.redis_client.zcard(REDIS_KEYS["token_pool"])

    def pool_stats(self) -> dict:
        """Get pool statistics."""
        size = self.pool_size()
        scores = self.redis_client.zrange(
            REDIS_KEYS["token_pool"],
            0, -1,
            withscores=True
        )

        if not scores:
            return {"size": 0, "avg_health": 0, "min_health": 0, "max_health": 0}

        health_scores = [s[1] for s in scores]
        return {
            "size": size,
            "avg_health": sum(health_scores) / len(health_scores),
            "min_health": min(health_scores),
            "max_health": max(health_scores),
        }

    def clear_pool(self) -> int:
        """Clear all tokens from the pool."""
        # Get all token IDs
        token_ids = self.redis_client.zrange(REDIS_KEYS["token_pool"], 0, -1)

        # Delete token data
        for token_id in token_ids:
            self.redis_client.delete(self._token_key(token_id))

        # Clear sorted set
        return self.redis_client.delete(REDIS_KEYS["token_pool"])

    def fill_pool(self, target_size: Optional[int] = None) -> int:
        """
        Fill pool to target size.

        Returns number of tokens added.
        """
        target = target_size or TOKEN_CONFIG["pool_target_size"]
        current = self.pool_size()
        to_add = max(0, target - current)

        added = 0
        for _ in range(to_add):
            token_set = create_token_set()
            if token_set:
                self.add_token(token_set)
                added += 1
                print(f"Added token {added}/{to_add}")
            else:
                print("Failed to create token")

        return added

    def close(self):
        """Close Redis connection."""
        if self._redis:
            self._redis.close()
            self._redis = None


# Singleton instance
_pool: Optional[TokenPool] = None


def get_pool() -> TokenPool:
    """Get the global token pool instance."""
    global _pool
    if _pool is None:
        _pool = TokenPool()
    return _pool


# Test function
def test_pool():
    """Test the token pool."""
    pool = TokenPool()

    print("Creating token set...")
    token_set = create_token_set()
    if not token_set:
        print("Failed to create token set")
        return

    print(f"Adding token to pool...")
    token_id = pool.add_token(token_set)
    print(f"Token ID: {token_id}")

    print(f"Pool size: {pool.pool_size()}")
    print(f"Pool stats: {pool.pool_stats()}")

    print("Getting token from pool...")
    retrieved = pool.get_token()
    if retrieved:
        print(f"Got token: {retrieved.guest_token}")
        pool.return_token(retrieved, success=True)
        print("Returned token")

    print(f"Pool size: {pool.pool_size()}")

    pool.close()


if __name__ == "__main__":
    test_pool()
