"""
SyntaX API
High-performance X/Twitter data API.
"""

import os
import sys
import time
from typing import Optional
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Query, Depends, Request
from fastapi.responses import ORJSONResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import redis

# Add scraper to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'scraper'))

from src.client import XClient, create_token_set
from src.token_pool import TokenPool, get_pool
from src.endpoints.user import get_user_by_username, User


# Response models
class UserResponse(BaseModel):
    id: str
    username: str
    name: str
    bio: str
    location: str
    website: str
    followers_count: int
    following_count: int
    tweet_count: int
    listed_count: int
    created_at: str
    is_verified: bool
    is_blue_verified: bool
    profile_image_url: str
    banner_url: Optional[str] = None


class APIResponse(BaseModel):
    success: bool
    data: Optional[dict] = None
    error: Optional[str] = None
    meta: dict = {}


# Globals
pool: Optional[TokenPool] = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup and shutdown events."""
    global pool

    # Startup
    print("Starting SyntaX API...")

    # Initialize token pool
    pool = TokenPool()
    print(f"Token pool initialized (size: {pool.pool_size()})")

    # If pool is empty, create some tokens
    if pool.pool_size() == 0:
        print("Pool empty, creating initial tokens...")
        for i in range(5):
            token_set = create_token_set()
            if token_set:
                pool.add_token(token_set)
                print(f"  Created token {i+1}/5")
        print(f"Pool size: {pool.pool_size()}")

    yield

    # Shutdown
    print("Shutting down SyntaX API...")
    if pool:
        pool.close()


# Create app
app = FastAPI(
    title="SyntaX API",
    description="High-performance X/Twitter data API. 10x faster than competitors.",
    version="0.1.0",
    default_response_class=ORJSONResponse,
    lifespan=lifespan,
)

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/")
async def root():
    """API root."""
    return {
        "name": "SyntaX API",
        "version": "0.1.0",
        "status": "running",
        "docs": "/docs",
    }


@app.get("/health")
async def health():
    """Health check."""
    return {
        "status": "healthy",
        "pool_size": pool.pool_size() if pool else 0,
    }


@app.get("/v1/users/{username}", response_model=APIResponse)
async def get_user(
    username: str,
    request: Request,
):
    """
    Get user profile by username.

    Returns user data including followers, following, bio, etc.
    """
    start_time = time.perf_counter()

    # Get token from pool
    token_set = pool.get_token() if pool else None

    if not token_set:
        # Create token on-demand (slower, but works)
        token_set = create_token_set()
        if not token_set:
            raise HTTPException(
                status_code=503,
                detail="Unable to create authentication token"
            )

    try:
        # Create client and fetch user
        client = XClient(token_set=token_set)
        user, api_time = get_user_by_username(username, client)
        client.close()

        total_time = (time.perf_counter() - start_time) * 1000

        if user:
            # Return token to pool
            if pool:
                pool.return_token(token_set, success=True)

            return APIResponse(
                success=True,
                data=user.to_dict(),
                meta={
                    "response_time_ms": round(total_time, 1),
                    "x_api_time_ms": round(api_time, 1),
                }
            )
        else:
            if pool:
                pool.return_token(token_set, success=True)

            raise HTTPException(
                status_code=404,
                detail=f"User @{username} not found"
            )

    except HTTPException:
        raise
    except Exception as e:
        # Return token with failure flag
        if pool:
            pool.return_token(token_set, success=False)

        raise HTTPException(
            status_code=500,
            detail=str(e)
        )


@app.get("/v1/pool/stats")
async def pool_stats():
    """Get token pool statistics."""
    if not pool:
        return {"error": "Pool not initialized"}

    return pool.pool_stats()


# Run with: uvicorn api.src.main:app --reload
if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=8000,
        reload=True,
    )
