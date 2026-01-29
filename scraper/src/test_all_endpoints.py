"""
SyntaX Full Endpoint Test
Tests every endpoint with guest tokens using maximum parallelism.

Speed optimizations:
- One XClient per thread (curl_cffi sessions aren't thread-safe)
- All 6 tests fire in parallel
- Concurrent init (txn generator + token creation)
- TLS pre-warming via token creation session reuse
"""

import time
import random
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Dict

from client import (
    create_token_set, XClient,
    _txn_generator, TokenSet,
)
from config import BROWSER_PROFILES
from endpoints.user import get_user_by_username, get_user_by_id
from endpoints.tweet import get_tweet_by_id, get_tweet_detail, get_user_tweets
from endpoints.search import search_tweets
from debug import C


# ── Client Factory ──────────────────────────────────────────

def _make_client() -> XClient:
    """Create a fresh guest-token XClient (fast — just a token + session)."""
    browser = random.choice(BROWSER_PROFILES)
    client = XClient(browser=browser)
    ts = create_token_set(browser, session=client.session)
    client.session.cookies.clear()
    if not ts:
        raise RuntimeError("Failed to create guest token")
    client.token_set = ts
    return client


def _warmup_txn():
    """Ensure the transaction ID generator is ready."""
    _txn_generator._ensure_initialized()


# ── Test Functions ──────────────────────────────────────────

def test_user_by_screenname() -> Dict[str, Any]:
    client = _make_client()
    try:
        user, ms = get_user_by_username("elonmusk", client)
        if user:
            return {"success": True, "ms": ms, "user": user, "user_id": user.id}
        return {"success": False, "error": "User not found"}
    except Exception as e:
        return {"success": False, "error": str(e)}
    finally:
        client.close()


def test_user_by_id(user_id: str = "44196397") -> Dict[str, Any]:
    client = _make_client()
    try:
        user, ms = get_user_by_id(user_id, client)
        if user:
            return {"success": True, "ms": ms, "user": user, "user_id": user_id}
        return {"success": False, "error": f"User {user_id} not found"}
    except Exception as e:
        return {"success": False, "error": str(e)}
    finally:
        client.close()


def test_tweet_by_id() -> Dict[str, Any]:
    tweet_id = "1585341984679469056"
    client = _make_client()
    try:
        tweet, ms = get_tweet_by_id(tweet_id, client)
        if tweet:
            return {"success": True, "ms": ms, "tweet": tweet, "tweet_id": tweet_id}
        return {"success": False, "error": "Tweet not found"}
    except Exception as e:
        return {"success": False, "error": str(e)}
    finally:
        client.close()


def test_tweet_detail() -> Dict[str, Any]:
    tweet_id = "1585341984679469056"
    client = _make_client()
    try:
        main_tweet, replies, ms = get_tweet_detail(tweet_id, client)
        if main_tweet:
            return {"success": True, "ms": ms, "tweet": main_tweet, "replies": replies}
        return {"success": False, "error": "Tweet detail not found"}
    except Exception as e:
        return {"success": False, "error": str(e)}
    finally:
        client.close()


def test_user_tweets(user_id: str = "44196397") -> Dict[str, Any]:
    client = _make_client()
    try:
        tweets, cursor, ms = get_user_tweets(user_id, client, count=5)
        return {"success": True, "ms": ms, "tweets": tweets, "cursor": cursor}
    except Exception as e:
        return {"success": False, "error": str(e)}
    finally:
        client.close()


def test_search() -> Dict[str, Any]:
    client = _make_client()
    try:
        tweets, cursor, ms = search_tweets("bitcoin", client, count=5, product="Top")
        return {"success": True, "ms": ms, "tweets": tweets, "cursor": cursor}
    except Exception as e:
        return {"success": False, "error": str(e)}
    finally:
        client.close()


# ── Result Printers ─────────────────────────────────────────

def section(title: str):
    print(f"\n{'─' * 60}")
    print(f"  {C.BOLD}{C.CYAN}{title}{C.RESET}")
    print(f"{'─' * 60}")


def ok(msg: str, ms: float):
    print(f"  {C.GREEN}✓{C.RESET} {msg}  {C.DIM}({ms:.0f}ms){C.RESET}")


def fail(msg: str):
    print(f"  {C.RED}✗{C.RESET} {msg}")


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


def print_user_by_id_result(num: int, result: Dict[str, Any]):
    section(f"{num}. UserByRestId")
    if result["success"]:
        user = result["user"]
        ok(f"@{user.username} (ID: {result['user_id']})", result["ms"])
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


# ── Main ────────────────────────────────────────────────────

def main():
    print(f"\n{'═' * 60}")
    print(f"  {C.BOLD}{C.CYAN}SyntaX — Full Endpoint Test (Parallel){C.RESET}")
    print(f"{'═' * 60}")

    overall_start = time.perf_counter()

    # Warm up txn generator in background while tests create their own tokens
    txn_thread = threading.Thread(target=_warmup_txn, daemon=True)
    txn_thread.start()

    print(f"\n  Mode: {C.BOLD}guest{C.RESET}")
    print(f"  Strategy: 6 clients, 6 threads, full parallel")

    # ── Fire ALL 6 tests in parallel — each with its own client ──
    with ThreadPoolExecutor(max_workers=6) as executor:
        futures = {
            executor.submit(test_user_by_screenname): "user",
            executor.submit(test_user_by_id): "user_by_id",
            executor.submit(test_tweet_by_id): "tweet",
            executor.submit(test_tweet_detail): "tweet_detail",
            executor.submit(test_user_tweets): "user_tweets",
            executor.submit(test_search): "search",
        }

        results = {}
        for future in as_completed(futures):
            key = futures[future]
            try:
                results[key] = future.result()
            except Exception as e:
                results[key] = {"success": False, "error": str(e)}

    # ── Print results in order ──
    print_user_result(1, "UserByScreenName", results["user"])
    print_user_by_id_result(2, results["user_by_id"])
    print_tweet_result(3, results["tweet"])
    print_tweet_detail_result(4, results["tweet_detail"])
    print_user_tweets_result(5, results["user_tweets"])
    print_search_result(6, results["search"])

    # ── Summary ──
    total = (time.perf_counter() - overall_start) * 1000

    sequential_time = 0
    for result in results.values():
        if isinstance(result, dict) and "ms" in result:
            sequential_time += result["ms"]

    speedup = sequential_time / total if total > 0 else 1

    print(f"\n{'═' * 60}")
    print(f"  {C.BOLD}Total time: {total:.0f}ms{C.RESET} (sequential would be ~{sequential_time:.0f}ms)")
    print(f"  {C.GREEN}Speedup: {speedup:.1f}x faster with parallel execution{C.RESET}")
    print(f"{'═' * 60}\n")


if __name__ == "__main__":
    main()
