"""
SyntaX Query Monitor
Extracts GraphQL query IDs from X's JavaScript bundles.
Query IDs change on every X deployment, so this needs to run periodically.
"""

import re
import time
import json
from typing import Dict, Optional

from curl_cffi import requests
import redis

from config import (
    X_HOME_URL,
    BROWSER_PROFILES,
    REDIS_KEYS,
    get_redis_url,  
)


# Known operation names we care about
OPERATIONS = [
    "UserByScreenName",
    "UserByRestId",
    "UserTweets",
    "UserTweetsAndReplies",
    "UserMedia",
    "Likes",
    "Followers",
    "Following",
    "TweetResultByRestId",
    "TweetDetail",
    "SearchTimeline",
    "TrendsList",
    "ListLatestTweetsTimeline",
    "Favoriters",
    "Retweeters",
]


def fetch_main_page() -> Optional[str]:
    """Fetch X main page to find bundle URLs."""
    try:
        resp = requests.get(
            X_HOME_URL,
            impersonate="chrome120",
            timeout=15,
        )
        return resp.text
    except Exception as e:
        print(f"Error fetching main page: {e}")
        return None


def extract_bundle_urls(html: str) -> list[str]:
    """Extract JavaScript bundle URLs from HTML."""
    # Pattern for main bundles
    patterns = [
        r'https://abs\.twimg\.com/responsive-web/client-web[^"]+\.js',
        r'https://abs\.twimg\.com/responsive-web/client-web-legacy[^"]+\.js',
    ]

    urls = set()
    for pattern in patterns:
        matches = re.findall(pattern, html)
        urls.update(matches)

    return list(urls)


def fetch_bundle(url: str) -> Optional[str]:
    """Fetch a JavaScript bundle."""
    try:
        resp = requests.get(
            url,
            impersonate="chrome120",
            timeout=30,
        )
        return resp.text
    except Exception as e:
        print(f"Error fetching bundle {url}: {e}")
        return None


def extract_query_ids(bundle: str) -> Dict[str, str]:
    """Extract queryId and operationName pairs from bundle."""
    query_ids = {}

    # Pattern 1: queryId:"xxx",...operationName:"yyy"
    pattern1 = r'queryId:\s*"([^"]+)"[^}]*?operationName:\s*"([^"]+)"'

    # Pattern 2: operationName:"yyy",...queryId:"xxx"
    pattern2 = r'operationName:\s*"([^"]+)"[^}]*?queryId:\s*"([^"]+)"'

    for match in re.finditer(pattern1, bundle):
        query_id, operation_name = match.groups()
        if operation_name in OPERATIONS:
            query_ids[operation_name] = query_id

    for match in re.finditer(pattern2, bundle):
        operation_name, query_id = match.groups()
        if operation_name in OPERATIONS:
            query_ids[operation_name] = query_id

    return query_ids


def discover_query_ids() -> Dict[str, str]:
    """
    Discover all query IDs from X's bundles.

    Returns a dict mapping operation names to query IDs.
    """
    print("Discovering query IDs from X bundles...")

    # Step 1: Fetch main page
    print("  Fetching main page...")
    html = fetch_main_page()
    if not html:
        return {}

    # Step 2: Extract bundle URLs
    print("  Extracting bundle URLs...")
    bundle_urls = extract_bundle_urls(html)
    print(f"  Found {len(bundle_urls)} bundles")

    # Step 3: Fetch and parse each bundle
    all_query_ids = {}

    for url in bundle_urls:
        print(f"  Fetching {url[:60]}...")
        bundle = fetch_bundle(url)
        if bundle:
            ids = extract_query_ids(bundle)
            all_query_ids.update(ids)
            if ids:
                print(f"    Found {len(ids)} query IDs")

    print(f"\nTotal query IDs discovered: {len(all_query_ids)}")
    return all_query_ids


def save_to_redis(query_ids: Dict[str, str], redis_url: Optional[str] = None):
    """Save query IDs to Redis."""
    r = redis.from_url(redis_url or get_redis_url(), decode_responses=True)

    # Save as hash
    if query_ids:
        r.hset(REDIS_KEYS["query_ids"], mapping=query_ids)
        print(f"Saved {len(query_ids)} query IDs to Redis")

    # Also save timestamp
    r.set(f"{REDIS_KEYS['query_ids']}:updated_at", str(time.time()))

    r.close()


def load_from_redis(redis_url: Optional[str] = None) -> Dict[str, str]:
    """Load query IDs from Redis."""
    r = redis.from_url(redis_url or get_redis_url(), decode_responses=True)
    query_ids = r.hgetall(REDIS_KEYS["query_ids"])
    r.close()
    return query_ids


def update_config_file(query_ids: Dict[str, str]):
    """Update the config.py file with new query IDs (for reference)."""
    print("\nDiscovered Query IDs:")
    print("-" * 50)
    for op, qid in sorted(query_ids.items()):
        print(f'    "{op}": "{qid}",')
    print("-" * 50)


def run_monitor(interval_seconds: int = 900):
    """
    Run the query monitor continuously.

    Args:
        interval_seconds: How often to check for new query IDs (default: 15 min)
    """
    print(f"Starting Query Monitor (interval: {interval_seconds}s)")

    last_ids = {}

    while True:
        try:
            query_ids = discover_query_ids()

            if query_ids:
                # Check for changes
                changes = []
                for op, qid in query_ids.items():
                    if op not in last_ids:
                        changes.append(f"NEW: {op} = {qid}")
                    elif last_ids[op] != qid:
                        changes.append(f"CHANGED: {op}: {last_ids[op]} -> {qid}")

                if changes:
                    print("\n*** Query ID Changes Detected ***")
                    for change in changes:
                        print(f"  {change}")
                    print()

                # Save to Redis
                save_to_redis(query_ids)
                last_ids = query_ids

            print(f"Next check in {interval_seconds}s...")
            time.sleep(interval_seconds)

        except KeyboardInterrupt:
            print("\nStopping Query Monitor")
            break
        except Exception as e:
            print(f"Error: {e}")
            time.sleep(60)


def main():
    """Run once and print results."""
    query_ids = discover_query_ids()

    if query_ids:
        update_config_file(query_ids)

        # Try to save to Redis
        try:
            save_to_redis(query_ids)
        except Exception as e:
            print(f"Could not save to Redis: {e}")
    else:
        print("No query IDs discovered")


if __name__ == "__main__":
    main()
