"""
SyntaX Full Endpoint Test
Tests every endpoint and shows actual parsed data.
"""

import os
import sys
import time
import random
import threading

from client import (
    create_token_set, create_auth_token_set, XClient,
    _txn_generator, TokenSet,
)
from config import BROWSER_PROFILES
from endpoints.user import get_user_by_username, get_user_by_id
from endpoints.tweet import get_tweet_by_id, get_tweet_detail, get_user_tweets
from endpoints.search import search_tweets
from debug import C


def _init_client() -> XClient:
    """Initialize client with parallel init (txn + token)."""
    browser = random.choice(BROWSER_PROFILES)
    client = XClient(browser=browser)
    token_result = [None]

    def _create_token():
        auth_token = os.environ.get("X_AUTH_TOKEN")
        ct0 = os.environ.get("X_CT0")
        if auth_token and ct0:
            token_result[0] = create_auth_token_set(auth_token, ct0)
        else:
            token_result[0] = create_token_set(browser, session=client.session)
            client.session.cookies.clear()

    t = threading.Thread(target=_create_token, daemon=True)
    t.start()
    _txn_generator._ensure_initialized()
    t.join(timeout=15)

    ts = token_result[0]
    if not ts:
        print(f"  {C.RED}FAIL: Could not create token set{C.RESET}")
        sys.exit(1)

    client.token_set = ts
    return client


def section(title: str):
    print(f"\n{'─' * 60}")
    print(f"  {C.BOLD}{C.CYAN}{title}{C.RESET}")
    print(f"{'─' * 60}")


def ok(msg: str, ms: float):
    print(f"  {C.GREEN}✓{C.RESET} {msg}  {C.DIM}({ms:.0f}ms){C.RESET}")


def fail(msg: str):
    print(f"  {C.RED}✗{C.RESET} {msg}")


def main():
    print(f"\n{'═' * 60}")
    print(f"  {C.BOLD}{C.CYAN}SyntaX — Full Endpoint Test{C.RESET}")
    print(f"{'═' * 60}")

    start = time.perf_counter()
    client = _init_client()
    init_ms = (time.perf_counter() - start) * 1000

    mode = "authenticated" if client.token_set.is_authenticated else "guest"
    print(f"\n  Mode: {C.BOLD}{mode}{C.RESET}  Init: {init_ms:.0f}ms")

    # ── 1. UserByScreenName ──────────────────────────────
    section("1. UserByScreenName")
    try:
        user, ms = get_user_by_username("elonmusk", client)
        if user:
            ok(f"@{user.username}", ms)
            print(f"      Name:       {user.name}")
            print(f"      Bio:        {user.bio[:80]}{'...' if len(user.bio) > 80 else ''}")
            print(f"      Followers:  {user.followers_count:,}")
            print(f"      Following:  {user.following_count:,}")
            print(f"      Tweets:     {user.tweet_count:,}")
            print(f"      Verified:   {user.is_blue_verified}")
            print(f"      Created:    {user.created_at}")
            print(f"      Image:      {user.profile_image_url[:60]}...")
            elon_id = user.id  # save for later tests
        else:
            fail("User not found")
            elon_id = "44196397"
    except Exception as e:
        fail(str(e))
        elon_id = "44196397"

    # ── 2. UserByRestId ──────────────────────────────────
    section("2. UserByRestId")
    try:
        user, ms = get_user_by_id(elon_id, client)
        if user:
            ok(f"@{user.username} (ID: {elon_id})", ms)
            print(f"      Followers:  {user.followers_count:,}")
        else:
            fail(f"User ID {elon_id} not found")
    except Exception as e:
        fail(str(e))

    # ── 3. TweetResultByRestId ───────────────────────────
    section("3. TweetResultByRestId (single tweet)")
    # Use a known tweet ID (Elon's "the bird is freed" tweet)
    test_tweet_id = "1585341984679469056"
    try:
        tweet, ms = get_tweet_by_id(test_tweet_id, client)
        if tweet:
            ok(f"Tweet {test_tweet_id}", ms)
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
            fail("Tweet not found (guest tokens sometimes return empty)")
    except Exception as e:
        fail(str(e))

    # ── Auth-only endpoints ──────────────────────────────
    if not client.token_set.is_authenticated:
        section("4-6. Auth-Only Endpoints (SKIPPED)")
        print(f"  {C.YELLOW}These require auth cookies:{C.RESET}")
        print(f"  {C.DIM}  - TweetDetail (conversation thread)")
        print(f"    - UserTweets (timeline)")
        print(f"    - SearchTimeline (search){C.RESET}")
        print(f"\n  {C.YELLOW}Set X_AUTH_TOKEN + X_CT0 in .env to unlock{C.RESET}")
    else:
        # ── 4. TweetDetail ───────────────────────────────
        section("4. TweetDetail (conversation thread)")
        try:
            main_tweet, replies, ms = get_tweet_detail(test_tweet_id, client)
            if main_tweet:
                ok(f"Tweet + {len(replies)} replies", ms)
                print(f"      Main:       {main_tweet.text[:80]}...")
                for i, r in enumerate(replies[:3]):
                    print(f"      Reply {i+1}:    @{r.author_username}: {r.text[:60]}...")
                if len(replies) > 3:
                    print(f"      ... and {len(replies) - 3} more replies")
            else:
                fail("Tweet detail not found")
        except Exception as e:
            fail(str(e))

        # ── 5. UserTweets ────────────────────────────────
        section("5. UserTweets (timeline)")
        try:
            tweets, cursor, ms = get_user_tweets(elon_id, client, count=5)
            ok(f"{len(tweets)} tweets from @elonmusk", ms)
            for i, t in enumerate(tweets[:5]):
                text = t.text[:70].replace('\n', ' ')
                print(f"      [{i+1}] {text}...")
                print(f"          {C.DIM}{t.like_count:,} likes | {t.retweet_count:,} RTs | {t.view_count:,} views{C.RESET}")
            if cursor:
                print(f"      {C.DIM}Next cursor: {cursor[:40]}...{C.RESET}")
        except Exception as e:
            fail(str(e))

        # ── 6. SearchTimeline ────────────────────────────
        section("6. SearchTimeline")
        try:
            tweets, cursor, ms = search_tweets("bitcoin", client, count=5, product="Top")
            ok(f"{len(tweets)} results for 'bitcoin'", ms)
            for i, t in enumerate(tweets[:5]):
                text = t.text[:70].replace('\n', ' ')
                print(f"      [{i+1}] @{t.author_username}: {text}...")
                print(f"          {C.DIM}{t.like_count:,} likes | {t.view_count:,} views{C.RESET}")
            if cursor:
                print(f"      {C.DIM}Next cursor available{C.RESET}")
        except Exception as e:
            fail(str(e))

    # ── Summary ──────────────────────────────────────────
    total = (time.perf_counter() - start) * 1000
    print(f"\n{'═' * 60}")
    print(f"  {C.BOLD}Total time: {total:.0f}ms{C.RESET}")
    print(f"{'═' * 60}\n")


if __name__ == "__main__":
    main()
