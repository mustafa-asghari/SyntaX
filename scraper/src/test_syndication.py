"""
SyntaX Speed Test
Runs all endpoints with detailed speed debugging.
Shows cold vs warm performance and per-phase timing.
"""

import os
import sys
import time

from client import create_token_set, create_auth_token_set, XClient, _txn_generator
from endpoints.user import get_user_by_username, get_user_by_id
from endpoints.tweet import get_tweet_by_id, get_user_tweets
from endpoints.search import search_tweets
from config import QUERY_IDS
from debug import SpeedDebugger, C


def main():
    debugger = SpeedDebugger()

    print(f"\n{C.BOLD}{C.CYAN}  SyntaX Speed Test{C.RESET}")
    print(f"  {'═' * 50}")

    # ── Phase 1: Initialization ──────────────────────────
    init_rd = debugger.new_request("Initialization")

    init_rd.phase("txn generator wait")
    _txn_generator._ensure_initialized()

    init_rd.phase("create token")
    # Try auth first, fall back to guest
    auth_token = os.environ.get("X_AUTH_TOKEN")
    ct0 = os.environ.get("X_CT0")

    if auth_token and ct0:
        ts = create_auth_token_set(auth_token, ct0)
        mode = "authenticated"
    else:
        ts = create_token_set()
        mode = "guest"

    init_rd.end()
    debugger.set_init_debug(init_rd)

    if not ts:
        print(f"\n  {C.RED}FAIL: Could not create token set{C.RESET}")
        return

    print(f"\n  Mode: {C.BOLD}{mode}{C.RESET}")
    if mode == "guest":
        print(f"  {C.DIM}Set X_AUTH_TOKEN + X_CT0 env vars to unlock all endpoints{C.RESET}")
    print()

    client = XClient(token_set=ts)

    # ── Phase 2: Cold request ────────────────────────────
    rd = debugger.new_request("UserByScreenName @elonmusk (cold)")
    try:
        data, t = client.graphql_request(
            QUERY_IDS["UserByScreenName"], "UserByScreenName",
            {"screen_name": "elonmusk", "withGrokTranslatedBio": False},
            field_toggles={"withPayments": False, "withAuxiliaryUserLabels": True},
            debug_obj=rd,
        )
        user = data.get("data", {}).get("user", {}).get("result", {})
        core = user.get("core", {})
        legacy = user.get("legacy", {})
        print(f"\n  {C.GREEN}✓{C.RESET} @{core.get('screen_name')} | {legacy.get('followers_count', 0):,} followers")
    except Exception as e:
        print(f"\n  {C.RED}✗{C.RESET} {e}")

    # ── Phase 3: Warm requests ───────────────────────────
    for username in ["jack", "BillGates", "NASA"]:
        rd = debugger.new_request(f"UserByScreenName @{username} (warm)")
        try:
            data, t = client.graphql_request(
                QUERY_IDS["UserByScreenName"], "UserByScreenName",
                {"screen_name": username, "withGrokTranslatedBio": False},
                field_toggles={"withPayments": False, "withAuxiliaryUserLabels": True},
                debug_obj=rd,
            )
            user = data.get("data", {}).get("user", {}).get("result", {})
            core = user.get("core", {})
            legacy = user.get("legacy", {})
            print(f"\n  {C.GREEN}✓{C.RESET} @{core.get('screen_name')} | {legacy.get('followers_count', 0):,} followers")
        except Exception as e:
            print(f"\n  {C.RED}✗{C.RESET} @{username}: {e}")

    # ── Phase 4: UserByRestId ────────────────────────────
    rd = debugger.new_request("UserByRestId 44196397 (warm)")
    try:
        data, t = client.graphql_request(
            QUERY_IDS["UserByRestId"], "UserByRestId",
            {"userId": "44196397", "withGrokTranslatedBio": False},
            field_toggles={"withPayments": False, "withAuxiliaryUserLabels": True},
            debug_obj=rd,
        )
        user = data.get("data", {}).get("user", {}).get("result", {})
        core = user.get("core", {})
        print(f"\n  {C.GREEN}✓{C.RESET} @{core.get('screen_name')} (by ID)")
    except Exception as e:
        print(f"\n  {C.RED}✗{C.RESET} UserByRestId: {e}")

    # ── Phase 5: Auth-only endpoints ─────────────────────
    if ts.is_authenticated:
        print(f"\n  {C.BOLD}Auth Endpoints:{C.RESET}")

        # UserTweets
        rd = debugger.new_request("UserTweets @elonmusk")
        try:
            from config import TWEET_FEATURES
            data, t = client.graphql_request(
                QUERY_IDS["UserTweets"], "UserTweets",
                {"userId": "44196397", "count": 5, "includePromotedContent": False,
                 "withQuickPromoteEligibilityTweetFields": False, "withVoice": True, "withV2Timeline": True},
                features=TWEET_FEATURES, debug_obj=rd,
            )
            # Try to count tweets
            ur = data.get("data", {}).get("user", {}).get("result", {})
            tl = ur.get("timeline_v2", {}).get("timeline", {}) or ur.get("timeline", {})
            tweet_count = 0
            for inst in tl.get("instructions", []):
                tweet_count += len(inst.get("entries", []))
            print(f"\n  {C.GREEN}✓{C.RESET} UserTweets: {tweet_count} entries")
        except Exception as e:
            print(f"\n  {C.RED}✗{C.RESET} UserTweets: {e}")

        # Search
        rd = debugger.new_request("Search 'bitcoin'")
        try:
            tweets, cursor, t = search_tweets("bitcoin", client, count=5)
            print(f"\n  {C.GREEN}✓{C.RESET} Search: {len(tweets)} results")
        except Exception as e:
            print(f"\n  {C.RED}✗{C.RESET} Search: {e}")

    # ── Summary ──────────────────────────────────────────
    debugger.print_summary()

    if not ts.is_authenticated:
        print(f"\n  {C.YELLOW}Tip:{C.RESET} To unlock UserTweets, Search, TweetDetail:")
        print(f"  {C.DIM}export X_AUTH_TOKEN='your_auth_token_cookie'")
        print(f"  export X_CT0='your_ct0_cookie'{C.RESET}")
        print()


if __name__ == "__main__":
    main()
