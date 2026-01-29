"""
SyntaX Full Endpoint Test
Tests every endpoint and shows actual parsed data.

Speed optimizations:
- Parallel test execution where dependencies allow
- Concurrent init (txn generator + token creation)
"""

import os
import sys
import time
import random
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Optional, Any, Dict, List, Tuple

from client import (
    create_token_set, create_auth_token_set, XClient,
    _txn_generator, TokenSet,
)
from config import BROWSER_PROFILES
from endpoints.user import get_user_by_username, get_user_by_id, User
from endpoints.tweet import get_tweet_by_id, get_tweet_detail, get_user_tweets, Tweet
from endpoints.search import search_tweets
from debug import C


def _init_client() -> XClient:
    """Initialize client with maximum parallelization.
    Runs operations concurrently:
    1. Transaction ID generator initialization (fetches x.com + JS)
    2. Token creation (auth or guest token)
    
    For guest tokens: token creation already warms TLS to api.x.com
    For auth tokens: we pre-warm TLS after token is set
    """
    browser = random.choice(BROWSER_PROFILES)
    client = XClient(browser=browser)
    token_result = [None]
    is_auth = [False]

    def _create_token():
        auth_token = os.environ.get("X_AUTH_TOKEN")
        ct0 = os.environ.get("X_CT0")
        if auth_token and ct0:
            token_result[0] = create_auth_token_set(auth_token, ct0)
            is_auth[0] = True
        else:
            # Guest token creation uses session = warms TLS already
            token_result[0] = create_token_set(browser, session=client.session)
            client.session.cookies.clear()

    def _init_txn():
        _txn_generator._ensure_initialized()

    # Start token + txn in parallel
    t1 = threading.Thread(target=_create_token, daemon=True)
    t2 = threading.Thread(target=_init_txn, daemon=True)
    t1.start()
    t2.start()
    
    t1.join(timeout=15)
    t2.join(timeout=15)

    ts = token_result[0]
    if not ts:
        print(f"  {C.RED}FAIL: Could not create token set{C.RESET}")
        sys.exit(1)

    client.token_set = ts
    
    # For auth tokens, pre-warm TLS now (doesn't block - runs in background)
    if is_auth[0]:
        threading.Thread(target=client.prewarm_connection, daemon=True).start()
    
    return client


def section(title: str):
    print(f"\n{'─' * 60}")
    print(f"  {C.BOLD}{C.CYAN}{title}{C.RESET}")
    print(f"{'─' * 60}")


def ok(msg: str, ms: float):
    print(f"  {C.GREEN}✓{C.RESET} {msg}  {C.DIM}({ms:.0f}ms){C.RESET}")


def fail(msg: str):
    print(f"  {C.RED}✗{C.RESET} {msg}")


# ── Test Functions (return results instead of printing) ──

def test_user_by_screenname(client: XClient) -> Dict[str, Any]:
    """Test 1: UserByScreenName"""
    try:
        user, ms = get_user_by_username("elonmusk", client)
        if user:
            return {
                "success": True, "ms": ms, "user": user,
                "user_id": user.id
            }
        return {"success": False, "error": "User not found", "user_id": "44196397"}
    except Exception as e:
        return {"success": False, "error": str(e), "user_id": "44196397"}


def test_user_by_id(client: XClient, user_id: str) -> Dict[str, Any]:
    """Test 2: UserByRestId"""
    try:
        user, ms = get_user_by_id(user_id, client)
        if user:
            return {"success": True, "ms": ms, "user": user}
        return {"success": False, "error": f"User ID {user_id} not found"}
    except Exception as e:
        return {"success": False, "error": str(e)}


def test_tweet_by_id(client: XClient) -> Dict[str, Any]:
    """Test 3: TweetResultByRestId"""
    test_tweet_id = "1585341984679469056"
    try:
        tweet, ms = get_tweet_by_id(test_tweet_id, client)
        if tweet:
            return {"success": True, "ms": ms, "tweet": tweet, "tweet_id": test_tweet_id}
        return {"success": False, "error": "Tweet not found"}
    except Exception as e:
        return {"success": False, "error": str(e)}


def test_tweet_detail(client: XClient, tweet_id: str) -> Dict[str, Any]:
    """Test 4: TweetDetail (requires auth)"""
    try:
        main_tweet, replies, ms = get_tweet_detail(tweet_id, client)
        if main_tweet:
            return {"success": True, "ms": ms, "tweet": main_tweet, "replies": replies}
        return {"success": False, "error": "Tweet detail not found"}
    except Exception as e:
        return {"success": False, "error": str(e)}


def test_user_tweets(client: XClient, user_id: str) -> Dict[str, Any]:
    """Test 5: UserTweets (requires auth)"""
    try:
        tweets, cursor, ms = get_user_tweets(user_id, client, count=5)
        return {"success": True, "ms": ms, "tweets": tweets, "cursor": cursor}
    except Exception as e:
        return {"success": False, "error": str(e)}


def test_search(client: XClient) -> Dict[str, Any]:
    """Test 6: SearchTimeline (requires auth)"""
    try:
        tweets, cursor, ms = search_tweets("bitcoin", client, count=5, product="Top")
        return {"success": True, "ms": ms, "tweets": tweets, "cursor": cursor}
    except Exception as e:
        return {"success": False, "error": str(e)}


# ── Result Printers ──

def print_user_result(num: int, title: str, result: Dict[str, Any]):
    section(f"{num}. {title}")
    if result["success"]:
        user = result["user"]
        ok(f"@{user.username}", result["ms"])
        print(f"      Name:       {user.name}")
        print(f"      Bio:        {user.bio[:80]}{'...' if len(user.bio) > 80 else ''}")
        print(f"      Followers:  {user.followers_count:,}")
        print(f"      Following:  {user.following_count:,}")
        print(f"      Tweets:     {user.tweet_count:,}")
        print(f"      Verified:   {user.is_blue_verified}")
        print(f"      Created:    {user.created_at}")
        print(f"      Image:      {user.profile_image_url[:60]}...")
    else:
        fail(result.get("error", "Unknown error"))


def print_user_by_id_result(num: int, result: Dict[str, Any], user_id: str):
    section(f"{num}. UserByRestId")
    if result["success"]:
        user = result["user"]
        ok(f"@{user.username} (ID: {user_id})", result["ms"])
        print(f"      Followers:  {user.followers_count:,}")
    else:
        fail(result.get("error", "Unknown error"))


def print_tweet_result(num: int, result: Dict[str, Any]):
    section(f"{num}. TweetResultByRestId (single tweet)")
    if result["success"]:
        tweet = result["tweet"]
        ok(f"Tweet {result['tweet_id']}", result["ms"])
        print(f"      Author:     @{tweet.author_username}")
        print(f"      Text:       {tweet.text[:100]}{'...' if len(tweet.text) > 100 else ''}")
        print(f"      Likes:      {tweet.like_count:,}")
        print(f"      Retweets:   {tweet.retweet_count:,}")
        print(f"      Replies:    {tweet.reply_count:,}")
        print(f"      Views:      {tweet.view_count:,}")
        print(f"      Created:    {tweet.created_at}")
        if tweet.media:
            print(f"      Media:      {len(tweet.media)} items")
        if tweet.urls:
            print(f"      URLs:       {len(tweet.urls)} links")
    else:
        fail(result.get("error", "Unknown error"))


def print_tweet_detail_result(num: int, result: Dict[str, Any]):
    section(f"{num}. TweetDetail (conversation thread)")
    if result["success"]:
        tweet = result["tweet"]
        replies = result["replies"]
        ok(f"Tweet + {len(replies)} replies", result["ms"])
        print(f"      Main:       {tweet.text[:80]}...")
        for i, r in enumerate(replies[:3]):
            print(f"      Reply {i+1}:    @{r.author_username}: {r.text[:60]}...")
        if len(replies) > 3:
            print(f"      ... and {len(replies) - 3} more replies")
    else:
        fail(result.get("error", "Unknown error"))


def print_user_tweets_result(num: int, result: Dict[str, Any]):
    section(f"{num}. UserTweets (timeline)")
    if result["success"]:
        tweets = result["tweets"]
        cursor = result["cursor"]
        ok(f"{len(tweets)} tweets from @elonmusk", result["ms"])
        for i, t in enumerate(tweets[:5]):
            text = t.text[:70].replace('\n', ' ')
            print(f"      [{i+1}] {text}...")
            print(f"          {C.DIM}{t.like_count:,} likes | {t.retweet_count:,} RTs | {t.view_count:,} views{C.RESET}")
        if cursor:
            print(f"      {C.DIM}Next cursor: {cursor[:40]}...{C.RESET}")
    else:
        fail(result.get("error", "Unknown error"))


def print_search_result(num: int, result: Dict[str, Any]):
    section(f"{num}. SearchTimeline")
    if result["success"]:
        tweets = result["tweets"]
        cursor = result["cursor"]
        ok(f"{len(tweets)} results for 'bitcoin'", result["ms"])
        for i, t in enumerate(tweets[:5]):
            text = t.text[:70].replace('\n', ' ')
            print(f"      [{i+1}] @{t.author_username}: {text}...")
            print(f"          {C.DIM}{t.like_count:,} likes | {t.view_count:,} views{C.RESET}")
        if cursor:
            print(f"      {C.DIM}Next cursor available{C.RESET}")
    else:
        fail(result.get("error", "Unknown error"))


def main():
    print(f"\n{'═' * 60}")
    print(f"  {C.BOLD}{C.CYAN}SyntaX — Full Endpoint Test (Parallel){C.RESET}")
    print(f"{'═' * 60}")

    start = time.perf_counter()
    client = _init_client()
    init_ms = (time.perf_counter() - start) * 1000

    mode = "authenticated" if client.token_set.is_authenticated else "guest"
    print(f"\n  Mode: {C.BOLD}{mode}{C.RESET}  Init: {init_ms:.0f}ms")

    # ── Phase 1: Run independent tests in parallel ──
    # Tests 1 (UserByScreenName) and 3 (TweetById) can run in parallel
    # They don't depend on each other's results
    
    results = {}
    
    with ThreadPoolExecutor(max_workers=4) as executor:
        # Phase 1: Independent tests (run in parallel)
        future_user = executor.submit(test_user_by_screenname, client)
        future_tweet = executor.submit(test_tweet_by_id, client)
        
        # If authenticated, also run search in parallel (doesn't depend on anything)
        future_search = None
        if client.token_set.is_authenticated:
            future_search = executor.submit(test_search, client)
        
        # Wait for phase 1 results
        results["user"] = future_user.result()
        results["tweet"] = future_tweet.result()
        if future_search:
            results["search"] = future_search.result()
        
        # Get user_id and tweet_id for dependent tests
        user_id = results["user"].get("user_id", "44196397")
        tweet_id = results["tweet"].get("tweet_id", "1585341984679469056")
        
        # Phase 2: Dependent tests (run in parallel with each other)
        future_user_by_id = executor.submit(test_user_by_id, client, user_id)
        
        if client.token_set.is_authenticated:
            future_tweet_detail = executor.submit(test_tweet_detail, client, tweet_id)
            future_user_tweets = executor.submit(test_user_tweets, client, user_id)
            
            results["user_by_id"] = future_user_by_id.result()
            results["tweet_detail"] = future_tweet_detail.result()
            results["user_tweets"] = future_user_tweets.result()
        else:
            results["user_by_id"] = future_user_by_id.result()

    # ── Print results in order ──
    print_user_result(1, "UserByScreenName", results["user"])
    print_user_by_id_result(2, results["user_by_id"], user_id)
    print_tweet_result(3, results["tweet"])
    
    if not client.token_set.is_authenticated:
        section("4-6. Auth-Only Endpoints (SKIPPED)")
        print(f"  {C.YELLOW}These require auth cookies:{C.RESET}")
        print(f"  {C.DIM}  - TweetDetail (conversation thread)")
        print(f"    - UserTweets (timeline)")
        print(f"    - SearchTimeline (search){C.RESET}")
        print(f"\n  {C.YELLOW}Set X_AUTH_TOKEN + X_CT0 in .env to unlock{C.RESET}")
    else:
        print_tweet_detail_result(4, results["tweet_detail"])
        print_user_tweets_result(5, results["user_tweets"])
        print_search_result(6, results["search"])

    # ── Summary ──
    total = (time.perf_counter() - start) * 1000
    
    # Calculate parallel speedup
    sequential_time = init_ms
    for key, result in results.items():
        if isinstance(result, dict) and "ms" in result:
            sequential_time += result["ms"]
    
    speedup = sequential_time / total if total > 0 else 1
    
    print(f"\n{'═' * 60}")
    print(f"  {C.BOLD}Total time: {total:.0f}ms{C.RESET} (sequential would be ~{sequential_time:.0f}ms)")
    print(f"  {C.GREEN}Speedup: {speedup:.1f}x faster with parallel execution{C.RESET}")
    print(f"{'═' * 60}\n")


if __name__ == "__main__":
    main()
