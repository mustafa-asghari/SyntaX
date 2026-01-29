"""
SyntaX Syndication API
Alternative endpoint for fetching tweets without authentication.
Uses X's public embed/syndication infrastructure.

This API has different rate limits than GraphQL and doesn't require guest tokens.
"""

import time
import random
import hashlib
from typing import Optional, Dict, Any, Tuple, List
from dataclasses import dataclass

import orjson
from curl_cffi import requests

from config import BROWSER_PROFILES


# ── Constants ────────────────────────────────────────────────

SYNDICATION_URL = "https://cdn.syndication.twimg.com/tweet-result"
SYNDICATION_TIMELINE_URL = "https://syndication.twitter.com/srv/timeline-profile/screen-name"

# Minimal headers - syndication doesn't check as aggressively
_SYNDICATION_HEADERS = {
    "accept": "*/*",
    "accept-language": "en-US,en;q=0.9",
    "origin": "https://platform.twitter.com",
    "referer": "https://platform.twitter.com/",
    "sec-fetch-dest": "empty",
    "sec-fetch-mode": "cors",
    "sec-fetch-site": "cross-site",
}


# ── Data Classes ─────────────────────────────────────────────

@dataclass(slots=True)
class SyndicationTweet:
    """Tweet data from syndication API."""
    id: str
    text: str
    created_at: str
    author_id: str
    author_username: str
    author_name: str
    author_avatar: str
    like_count: int
    retweet_count: int
    reply_count: int
    quote_count: int
    view_count: int
    language: str
    media: List[Dict[str, Any]]
    is_reply: bool
    is_retweet: bool
    is_quote: bool
    quoted_tweet: Optional["SyndicationTweet"] = None
    
    @classmethod
    def from_response(cls, data: Dict[str, Any]) -> Optional["SyndicationTweet"]:
        """Parse syndication API response into SyndicationTweet."""
        if not data or "id_str" not in data:
            return None
        
        # Parse user
        user = data.get("user", {})
        
        # Parse media
        media = []
        for m in data.get("mediaDetails", []) or []:
            media.append({
                "type": m.get("type"),
                "url": m.get("media_url_https"),
                "video_url": m.get("video_info", {}).get("variants", [{}])[0].get("url") if m.get("type") == "video" else None,
            })
        
        # Parse quoted tweet
        quoted = None
        if data.get("quoted_tweet"):
            quoted = cls.from_response(data["quoted_tweet"])
        
        return cls(
            id=data.get("id_str", ""),
            text=data.get("text", ""),
            created_at=data.get("created_at", ""),
            author_id=user.get("id_str", ""),
            author_username=user.get("screen_name", ""),
            author_name=user.get("name", ""),
            author_avatar=user.get("profile_image_url_https", ""),
            like_count=data.get("favorite_count", 0),
            retweet_count=data.get("retweet_count", 0),
            reply_count=data.get("reply_count", 0),
            quote_count=data.get("quote_count", 0),
            view_count=int(data.get("views", {}).get("count", 0) or 0),
            language=data.get("lang", ""),
            media=media,
            is_reply=data.get("in_reply_to_status_id_str") is not None,
            is_retweet=data.get("retweeted_status") is not None,
            is_quote=data.get("is_quote_status", False),
            quoted_tweet=quoted,
        )


# ── API Functions ────────────────────────────────────────────

def _generate_token(tweet_id: str) -> str:
    """
    Generate the token required for syndication API.
    This is a simple hash-based token that X uses for rate limiting.
    """
    # X uses a simple token generation based on tweet ID
    # Format: first 12 chars of md5(id + secret)
    # The "secret" rotates but these values work currently
    base = f"{tweet_id}"
    return hashlib.md5(base.encode()).hexdigest()[:12]


def get_tweet_syndication(
    tweet_id: str,
    browser: Optional[str] = None,
    session: Optional[requests.Session] = None,
) -> Tuple[Optional[SyndicationTweet], float]:
    """
    Fetch a single tweet via syndication API.
    
    This endpoint:
    - Does NOT require auth tokens
    - Has separate rate limits from GraphQL
    - Returns full tweet data including media
    
    Args:
        tweet_id: The tweet ID to fetch
        browser: Browser profile to impersonate
        session: Optional session for connection reuse
        
    Returns:
        Tuple of (SyndicationTweet or None, response_time_ms)
    """
    browser = browser or random.choice(BROWSER_PROFILES)
    token = _generate_token(tweet_id)
    
    params = {
        "id": tweet_id,
        "token": token,
        "lang": "en",
    }
    
    start = time.perf_counter()
    
    try:
        if session:
            response = session.get(
                SYNDICATION_URL,
                params=params,
                headers=_SYNDICATION_HEADERS,
                timeout=(5, 10),
            )
        else:
            response = requests.get(
                SYNDICATION_URL,
                params=params,
                headers=_SYNDICATION_HEADERS,
                impersonate=browser,
                timeout=(5, 10),
            )
        
        elapsed = (time.perf_counter() - start) * 1000
        
        if response.status_code == 404:
            return None, elapsed
        
        response.raise_for_status()
        data = orjson.loads(response.content)
        
        tweet = SyndicationTweet.from_response(data)
        return tweet, elapsed
        
    except Exception as e:
        elapsed = (time.perf_counter() - start) * 1000
        print(f"[Syndication] Error fetching {tweet_id}: {e}")
        return None, elapsed


def get_tweets_batch_syndication(
    tweet_ids: List[str],
    max_workers: int = 10,
    browser: Optional[str] = None,
) -> List[Tuple[str, Optional[SyndicationTweet], float]]:
    """
    Fetch multiple tweets in parallel via syndication API.
    
    Args:
        tweet_ids: List of tweet IDs to fetch
        max_workers: Max parallel requests
        browser: Browser profile to use
        
    Returns:
        List of (tweet_id, SyndicationTweet or None, ms) tuples
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed
    
    browser = browser or random.choice(BROWSER_PROFILES)
    session = requests.Session(impersonate=browser)
    
    results = []
    
    try:
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {
                executor.submit(get_tweet_syndication, tid, browser, session): tid
                for tid in tweet_ids
            }
            
            for future in as_completed(futures):
                tid = futures[future]
                try:
                    tweet, ms = future.result()
                    results.append((tid, tweet, ms))
                except Exception as e:
                    results.append((tid, None, 0.0))
    finally:
        session.close()
    
    return results


# ── Test ─────────────────────────────────────────────────────

def test_syndication():
    """Test the syndication API."""
    print("\n=== Syndication API Test ===\n")
    
    # Test single tweet
    test_ids = [
        "1585341984679469056",  # Known tweet
        "1234567890123456789",  # Likely doesn't exist
    ]
    
    for tweet_id in test_ids:
        tweet, ms = get_tweet_syndication(tweet_id)
        if tweet:
            print(f"✓ Tweet {tweet_id} ({ms:.0f}ms)")
            print(f"  Author: @{tweet.author_username}")
            print(f"  Text: {tweet.text[:80]}...")
            print(f"  Likes: {tweet.like_count:,}")
            print(f"  Views: {tweet.view_count:,}")
            if tweet.media:
                print(f"  Media: {len(tweet.media)} items")
        else:
            print(f"✗ Tweet {tweet_id} not found ({ms:.0f}ms)")
        print()
    
    # Test batch
    print("--- Batch Test (5 requests) ---")
    batch_ids = ["1585341984679469056"] * 5
    results = get_tweets_batch_syndication(batch_ids, max_workers=5)
    
    total_ms = sum(r[2] for r in results)
    avg_ms = total_ms / len(results)
    print(f"Batch complete: {len(results)} requests, avg {avg_ms:.0f}ms each")


if __name__ == "__main__":
    test_syndication()
