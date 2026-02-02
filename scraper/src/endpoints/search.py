"""
SyntaX Search Endpoints
Endpoints for searching X tweets.

SearchTimeline requires authenticated accounts (guest tokens get 404).
Uses the AccountPool to rotate across auth accounts while keeping
guest tokens for non-gated endpoints.
"""

from typing import Optional, Dict, Any, List

from client import XClient, token_set_from_account
from config import QUERY_IDS, TWEET_FEATURES
from endpoints.tweet import Tweet, _parse_tweet_result
from account_pool import get_account_pool


def search_tweets(
    query: str,
    client: XClient,
    count: int = 20,
    product: str = "Top",
    cursor: Optional[str] = None,
    debug: bool = False,
) -> tuple[List[Tweet], Optional[str], float]:
    """
    Search for tweets.

    For guest tokens, acquires an authenticated account from the pool
    since SearchTimeline is auth-gated. Falls back gracefully if no
    accounts are available.

    Args:
        query: Search query string (supports X search operators)
        client: XClient instance
        count: Number of results (max ~20 per page)
        product: "Top", "Latest", "People", "Photos", "Videos"
        cursor: Pagination cursor from previous request
        debug: Print debug info

    Returns:
        Tuple of (tweets, next_cursor, response_time_ms)
    """
    query_id = QUERY_IDS.get("SearchTimeline")
    if not query_id:
        raise ValueError("SearchTimeline query ID not configured")

    variables = {
        "rawQuery": query,
        "count": count,
        "querySource": "typed_query",
        "product": product,
    }
    if cursor:
        variables["cursor"] = cursor

    # If client is already authenticated, use it directly
    if client.token_set and client.token_set.is_authenticated:
        data, elapsed_ms = client.graphql_request(
            query_id=query_id,
            operation_name="SearchTimeline",
            variables=variables,
            features=TWEET_FEATURES,
            debug=debug,
        )
        tweets, next_cursor = _parse_search_response(data)
        return tweets, next_cursor, elapsed_ms

    # Guest token — need an authenticated account from the pool
    pool = get_account_pool()
    account = pool.acquire() if pool.has_accounts else None

    if not account:
        # No accounts available — return empty
        return [], None, 0.0

    # Build an auth client using the account's credentials + warm session
    auth_ts = token_set_from_account(account)
    session = account.acquire_session()
    auth_client = XClient(
        token_set=auth_ts,
        proxy=account.proxy_dict,
        session=session,
    )

    try:
        data, elapsed_ms = auth_client.graphql_request(
            query_id=query_id,
            operation_name="SearchTimeline",
            variables=variables,
            features=TWEET_FEATURES,
            debug=debug,
        )
        pool.release(account, success=True, status_code=200)
        tweets, next_cursor = _parse_search_response(data)
        return tweets, next_cursor, elapsed_ms
    except Exception as e:
        status = 404
        if "429" in str(e):
            status = 429
        elif "403" in str(e):
            status = 403
        pool.release(account, success=False, status_code=status)
        raise
    finally:
        auth_client.close()  # no-op for external session
        account.release_session(session)


def _parse_search_response(data: Dict[str, Any]) -> tuple[List[Tweet], Optional[str]]:
    """Parse a search response into tweets and cursor."""
    tweets = []
    next_cursor = None

    timeline = (
        data.get("data", {})
        .get("search_by_raw_query", {})
        .get("search_timeline", {})
        .get("timeline", {})
    )

    for instruction in timeline.get("instructions", []):
        entries = instruction.get("entries", [])
        for entry in entries:
            content = entry.get("content", {})

            # Tweet entry
            if content.get("entryType") == "TimelineTimelineItem":
                result = (
                    content.get("itemContent", {})
                    .get("tweet_results", {})
                    .get("result", {})
                )
                tweet = _parse_tweet_result(result)
                if tweet:
                    tweets.append(tweet)

            # Cursor entry
            elif content.get("entryType") == "TimelineTimelineCursor":
                if content.get("cursorType") == "Bottom":
                    next_cursor = content.get("value")

    return tweets, next_cursor
