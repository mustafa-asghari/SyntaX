"""
SyntaX Full Endpoint Test (Hybrid: Guest + Auth Accounts)

Guest tokens handle: UserByScreenName, UserByRestId, TweetResultByRestId, UserTweets
Auth accounts handle: SearchTimeline, TweetDetail (replies), Followers, Following
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
from endpoints.social import get_followers, get_following
from account_pool import get_account_pool
from debug import C


# ── Client Factory ──────────────────────────────────────────

def _make_client() -> XClient:
    """Create a fresh guest-token XClient."""
    browser = random.choice(BROWSER_PROFILES)
    client = XClient(browser=browser)
    ts = create_token_set(browser, session=client.session)
    client.session.cookies.clear()
    if not ts:
        raise RuntimeError("Failed to create guest token")
    client.token_set = ts
    return client


def _warmup_txn():
    _txn_generator._ensure_initialized()


# ── Test Functions (Guest) ──────────────────────────────────

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


def test_user_tweets(user_id: str = "44196397") -> Dict[str, Any]:
    client = _make_client()
    try:
        tweets, cursor, ms = get_user_tweets(user_id, client, count=5)
        return {"success": True, "ms": ms, "tweets": tweets, "cursor": cursor}
    except Exception as e:
        return {"success": False, "error": str(e)}
    finally:
        client.close()


# ── Test Functions (Auth-gated — use account pool) ──────────

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


def test_search() -> Dict[str, Any]:
    client = _make_client()
    try:
        tweets, cursor, ms = search_tweets("bitcoin", client, count=5, product="Top")
        return {"success": True, "ms": ms, "tweets": tweets, "cursor": cursor}
    except Exception as e:
        return {"success": False, "error": str(e)}
    finally:
        client.close()


def test_followers() -> Dict[str, Any]:
    client = _make_client()
    try:
        users, cursor, ms = get_followers("44196397", client, count=5)
        return {"success": True, "ms": ms, "users": users, "cursor": cursor}
    except Exception as e:
        return {"success": False, "error": str(e)}
    finally:
        client.close()


def test_following() -> Dict[str, Any]:
    client = _make_client()
    try:
        users, cursor, ms = get_following("44196397", client, count=5)
        return {"success": True, "ms": ms, "users": users, "cursor": cursor}
    except Exception as e:
        return {"success": False, "error": str(e)}
    finally:
        client.close()


# ── Result Printers ─────────────────────────────────────────

def section(num: int, title: str, auth: bool = False):
    tag = f" {C.YELLOW}[auth]{C.RESET}" if auth else f" {C.DIM}[guest]{C.RESET}"
    print(f"\n{'─' * 60}")
    print(f"  {C.BOLD}{C.CYAN}{num}. {title}{C.RESET}{tag}")
    print(f"{'─' * 60}")


def ok(msg: str, ms: float):
    print(f"  {C.GREEN}✓{C.RESET} {msg}  {C.DIM}({ms:.0f}ms){C.RESET}")


def fail(msg: str):
    print(f"  {C.RED}✗{C.RESET} {msg}")


def print_user_result(num: int, title: str, result: Dict[str, Any]):
    section(num, title)
    if result["success"]:
        user = result["user"]
        ok(f"@{user.username}", result["ms"])
        print(f"      Name:       {user.name}")
        print(f"      Followers:  {user.followers_count:,}")
        print(f"      Verified:   {user.is_blue_verified}")
    else:
        fail(result.get("error", "Unknown error"))


def print_user_by_id_result(num: int, result: Dict[str, Any]):
    section(num, "UserByRestId")
    if result["success"]:
        user = result["user"]
        ok(f"@{user.username} (ID: {result['user_id']})", result["ms"])
        print(f"      Followers:  {user.followers_count:,}")
    else:
        fail(result.get("error", "Unknown error"))


def print_tweet_result(num: int, result: Dict[str, Any]):
    section(num, "TweetResultByRestId")
    if result["success"]:
        tweet = result["tweet"]
        ok(f"Tweet {result['tweet_id']}", result["ms"])
        print(f"      Author:     @{tweet.author_username}")
        print(f"      Text:       {tweet.text[:80]}{'...' if len(tweet.text) > 80 else ''}")
        print(f"      Likes:      {tweet.like_count:,}")
        print(f"      Retweets:   {tweet.retweet_count:,}")
        if tweet.media:
            print(f"      Media:      {len(tweet.media)} items")
    else:
        fail(result.get("error", "Unknown error"))


def print_tweet_detail_result(num: int, result: Dict[str, Any]):
    section(num, "TweetDetail (replies)", auth=True)
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
    section(num, "UserTweets")
    if result["success"]:
        tweets = result["tweets"]
        ok(f"{len(tweets)} tweets from @elonmusk", result["ms"])
        for i, t in enumerate(tweets[:3]):
            text = t.text[:70].replace('\n', ' ')
            print(f"      [{i+1}] {text}...")
            print(f"          {C.DIM}{t.like_count:,} likes | {t.retweet_count:,} RTs{C.RESET}")
    else:
        fail(result.get("error", "Unknown error"))


def print_search_result(num: int, result: Dict[str, Any]):
    section(num, "SearchTimeline", auth=True)
    if result["success"]:
        tweets = result["tweets"]
        ok(f"{len(tweets)} results for 'bitcoin'", result["ms"])
        for i, t in enumerate(tweets[:3]):
            text = t.text[:70].replace('\n', ' ')
            print(f"      [{i+1}] @{t.author_username}: {text}...")
            print(f"          {C.DIM}{t.like_count:,} likes | {t.view_count:,} views{C.RESET}")
    else:
        fail(result.get("error", "Unknown error"))


def print_social_result(num: int, title: str, result: Dict[str, Any]):
    section(num, title, auth=True)
    if result["success"]:
        users = result["users"]
        ok(f"{len(users)} users", result["ms"])
        for i, u in enumerate(users[:3]):
            print(f"      [{i+1}] @{u.username} — {u.name} ({u.followers_count:,} followers)")
        if result.get("cursor"):
            print(f"      {C.DIM}Next cursor available{C.RESET}")
    else:
        fail(result.get("error", "Unknown error"))


# ── Main ────────────────────────────────────────────────────

def main():
    print(f"\n{'═' * 60}")
    print(f"  {C.BOLD}{C.CYAN}SyntaX — Full Endpoint Test (Hybrid){C.RESET}")
    print(f"{'═' * 60}")

    overall_start = time.perf_counter()

    txn_thread = threading.Thread(target=_warmup_txn, daemon=True)
    txn_thread.start()

    pool = get_account_pool()
    has_accounts = pool.has_accounts

    print(f"\n  Guest tokens:  {C.GREEN}enabled{C.RESET}")
    if has_accounts:
        print(f"  Auth accounts: {C.GREEN}{pool.count} loaded{C.RESET}")
    else:
        print(f"  Auth accounts: {C.YELLOW}none{C.RESET} (add accounts.json for Search/Replies/Followers)")

    # ── Fire tests in parallel ──
    # Guest endpoints: always run (4 tests)
    # Auth endpoints: run if accounts available (4 tests)
    with ThreadPoolExecutor(max_workers=8) as executor:
        futures = {
            executor.submit(test_user_by_screenname): "user",
            executor.submit(test_user_by_id): "user_by_id",
            executor.submit(test_tweet_by_id): "tweet",
            executor.submit(test_user_tweets): "user_tweets",
            executor.submit(test_tweet_detail): "tweet_detail",
            executor.submit(test_search): "search",
        }
        if has_accounts:
            futures[executor.submit(test_followers)] = "followers"
            futures[executor.submit(test_following)] = "following"

        results = {}
        for future in as_completed(futures):
            key = futures[future]
            try:
                results[key] = future.result()
            except Exception as e:
                results[key] = {"success": False, "error": str(e)}

    # ── Print results in order ──
    n = 1
    print_user_result(n, "UserByScreenName", results["user"]); n += 1
    print_user_by_id_result(n, results["user_by_id"]); n += 1
    print_tweet_result(n, results["tweet"]); n += 1
    print_user_tweets_result(n, results["user_tweets"]); n += 1
    print_tweet_detail_result(n, results["tweet_detail"]); n += 1
    print_search_result(n, results["search"]); n += 1
    if "followers" in results:
        print_social_result(n, "Followers", results["followers"]); n += 1
        print_social_result(n, "Following", results["following"]); n += 1

    # ── Summary ──
    total = (time.perf_counter() - overall_start) * 1000

    passed = sum(1 for r in results.values() if r.get("success"))
    total_tests = len(results)

    seq_time = sum(r.get("ms", 0) for r in results.values() if isinstance(r, dict))
    speedup = seq_time / total if total > 0 else 1

    print(f"\n{'═' * 60}")
    print(f"  {C.BOLD}{passed}/{total_tests} passed{C.RESET} in {total:.0f}ms "
          f"(sequential ~{seq_time:.0f}ms, {C.GREEN}{speedup:.1f}x speedup{C.RESET})")
    print(f"{'═' * 60}\n")


if __name__ == "__main__":
    main()
