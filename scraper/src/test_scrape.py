"""
SyntaX Test Script
Test the scraper to make sure everything works.
"""

import time
import sys

from client import create_token_set, XClient
from endpoints.user import get_user_by_username


def test_single_user(username: str = "elonmusk"):
    """Test fetching a single user."""
    print(f"\n{'='*60}")
    print(f"SyntaX Scraper Test")
    print(f"{'='*60}\n")

    # Step 1: Create token set
    print("[1/3] Creating token set...")
    start = time.perf_counter()
    token_set = create_token_set()
    token_time = (time.perf_counter() - start) * 1000

    if not token_set:
        print("FAILED: Could not create token set")
        return False

    print(f"  Token created in {token_time:.0f}ms")
    print(f"  Guest token: {token_set.guest_token}")
    #print(f"  CF cookie: {token_set.cf_cookie[:30]}...")

    # Step 2: Create client
    print(f"\n[2/3] Creating client...")
    client = XClient(token_set=token_set)

    # Step 3: Fetch user
    print(f"\n[3/3] Fetching user @{username}...")
    try:
        user, elapsed_ms = get_user_by_username(username, client, debug=True)

        if user:
            print(f"\n  SUCCESS! Response time: {elapsed_ms:.0f}ms")
            print(f"\n  User Data:")
            print(f"    ID:          {user.id}")
            print(f"    Username:    @{user.username}")
            print(f"    Name:        {user.name}")
            print(f"    Bio:         {user.bio[:50]}..." if len(user.bio) > 50 else f"    Bio:         {user.bio}")
            print(f"    Followers:   {user.followers_count:,}")
            print(f"    Following:   {user.following_count:,}")
            print(f"    Tweets:      {user.tweet_count:,}")
            print(f"    Verified:    {user.is_blue_verified}")
            print(f"    Created:     {user.created_at}")
            return True
        else:
            print(f"\n  FAILED: User not found or unavailable")
            return False

    except Exception as e:
        print(f"\n  ERROR: {e}")
        import traceback
        traceback.print_exc()
        return False

    finally:
        client.close()


def test_multiple_users(usernames: list[str]):
    """Test fetching multiple users with the same token."""
    print(f"\n{'='*60}")
    print(f"SyntaX Bulk Test - {len(usernames)} users")
    print(f"{'='*60}\n")

    # Create token set once
    print("Creating token set...")
    token_set = create_token_set()
    if not token_set:
        print("FAILED: Could not create token set")
        return

    client = XClient(token_set=token_set)
    results = []

    for username in usernames:
        try:
            user, elapsed_ms = get_user_by_username(username, client)
            if user:
                results.append((username, elapsed_ms, True, user.followers_count))
                print(f"  @{username}: {elapsed_ms:.0f}ms - {user.followers_count:,} followers")
            else:
                results.append((username, elapsed_ms, False, 0))
                print(f"  @{username}: {elapsed_ms:.0f}ms - NOT FOUND")
        except Exception as e:
            results.append((username, 0, False, 0))
            print(f"  @{username}: ERROR - {e}")

    client.close()

    # Summary
    successful = [r for r in results if r[2]]
    if successful:
        avg_time = sum(r[1] for r in successful) / len(successful)
        print(f"\n  Summary:")
        print(f"    Success: {len(successful)}/{len(usernames)}")
        print(f"    Avg response time: {avg_time:.0f}ms")
        print(f"    Token requests used: {token_set.request_count}")


def main():
    """Run tests."""
    # Test single user
    if len(sys.argv) > 1:
        username = sys.argv[1]
    else:
        username = "elonmusk"

    success = test_single_user(username)

    if success:
        # Test multiple users
        print("\n")
        test_multiple_users([
            "elonmusk",
            "jack",
            "naval",
            "paulg",
            "sama",
        ])

    print(f"\n{'='*60}")
    print("Test complete!")
    print(f"{'='*60}\n")


if __name__ == "__main__":
    main()
