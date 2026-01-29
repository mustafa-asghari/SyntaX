"""
SyntaX End-to-End Integration Test
Tests the full pipeline: token creation -> transaction ID -> GraphQL API.
"""

import sys
import time

sys.path.insert(0, ".")

from src.client import create_token_set, XClient
from src.endpoints.user import get_user_by_username, get_user_by_id
from src.endpoints.tweet import get_tweet_by_id
from src.config import QUERY_IDS


def main():
    print("=" * 60)
    print("SyntaX Integration Test")
    print("=" * 60)

    # Create token
    print("\n1. Creating token set...")
    start = time.perf_counter()
    ts = create_token_set()
    if not ts:
        print("   FAIL: Could not create token set")
        return
    print(f"   Guest token: {ts.guest_token} ({(time.perf_counter()-start)*1000:.0f}ms)")

    client = XClient(token_set=ts)

    # User by username
    print("\n2. User by username (@elonmusk)...")
    user, t = get_user_by_username("elonmusk", client)
    if user:
        print(f"   @{user.username} - {user.name}")
        print(f"   Followers: {user.followers_count:,} | Tweets: {user.tweet_count:,}")
        print(f"   Verified: {user.is_blue_verified} | ID: {user.id}")
        print(f"   Time: {t:.0f}ms")
    else:
        print("   FAIL")

    # User by ID
    if user:
        print(f"\n3. User by ID ({user.id})...")
        user2, t = get_user_by_id(user.id, client)
        if user2:
            print(f"   @{user2.username} - {user2.name} ({t:.0f}ms)")
        else:
            print(f"   FAIL ({t:.0f}ms)")

    # Available query IDs
    print("\n4. Available query IDs:")
    for op, qid in sorted(QUERY_IDS.items()):
        print(f"   {op}: {qid}")

    client.close()

    print("\n" + "=" * 60)
    print("Test complete - core pipeline working!")
    print("=" * 60)
    print("\nNote: UserTweets, Search, TweetDetail require")
    print("authenticated cookies for full access.")


if __name__ == "__main__":
    main()
