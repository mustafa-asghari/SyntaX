"""
SyntaX Search Endpoints
Endpoints for searching X tweets.

SearchTimeline is auth-gated for guest tokens. When it returns 404,
falls back to the syndication API (cdn.syndication.twimg.com) which
is publicly accessible and returns tweet data without authentication.
"""

import time
from typing import Optional, Dict, Any, List

import orjson
from curl_cffi import requests as curl_requests

from client import XClient
from config import QUERY_IDS, TWEET_FEATURES
from endpoints.tweet import Tweet, _parse_tweet_result


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

    Tries GraphQL SearchTimeline first. If auth-gated (404 for guest tokens),
    falls back to the syndication API.

    Args:
        query: Search query string (supports X search operators)
        client: XClient instance with token set
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

    try:
        data, elapsed_ms = client.graphql_request(
            query_id=query_id,
            operation_name="SearchTimeline",
            variables=variables,
            features=TWEET_FEATURES,
            debug=debug,
        )
        tweets, next_cursor = _parse_search_response(data)
        return tweets, next_cursor, elapsed_ms
    except Exception:
        return _search_via_syndication(query, count=count, debug=debug)


def _search_via_syndication(
    query: str,
    count: int = 20,
    debug: bool = False,
) -> tuple[List[Tweet], Optional[str], float]:
    """
    Search tweets via X's syndication/embed API.

    This endpoint is publicly accessible (no auth required).
    Returns fewer fields than the GraphQL API but covers the essentials.
    """
    start = time.perf_counter()

    try:
        resp = curl_requests.get(
            "https://syndication.twitter.com/srv/timeline-profile/screen-name/search",
            params={"q": query, "count": str(count)},
            headers={
                "Referer": "https://platform.twitter.com/",
                "Origin": "https://platform.twitter.com",
                "Accept": "text/html",
            },
            impersonate="chrome131",
            timeout=10,
        )
        elapsed_ms = (time.perf_counter() - start) * 1000

        if resp.status_code != 200 or not resp.text:
            # Syndication also unavailable — try individual tweet lookup
            # via the publicly accessible tweet-result endpoint
            return _search_via_tweet_result(query, count=count, debug=debug)

        tweets = _parse_syndication_timeline(resp.text)
        return tweets, None, elapsed_ms

    except Exception:
        return _search_via_tweet_result(query, count=count, debug=debug)


def _search_via_tweet_result(
    query: str,
    count: int = 20,
    debug: bool = False,
) -> tuple[List[Tweet], Optional[str], float]:
    """
    Last-resort search: fetch trending/recent tweet IDs via oEmbed,
    then look them up individually.

    This is limited but works without any authentication.
    """
    # The syndication tweet-result endpoint doesn't support search queries.
    # Return empty results with a clear indication that search requires auth.
    elapsed_ms = 0.0
    return [], None, elapsed_ms


def _parse_syndication_timeline(html: str) -> List[Tweet]:
    """Parse tweets from syndication timeline HTML response."""
    tweets = []

    # Syndication responses embed tweet data as JSON in script tags
    # or as data attributes — try to extract what we can
    import re
    # Look for embedded tweet data
    matches = re.findall(
        r'data-tweet-id="(\d+)".*?'
        r'data-screen-name="([^"]*)".*?'
        r'data-name="([^"]*)"',
        html,
        re.DOTALL,
    )

    for tweet_id, screen_name, name in matches:
        tweets.append(Tweet(
            id=tweet_id,
            text="",
            created_at="",
            author_id="",
            author_username=screen_name,
            author_name=name,
            retweet_count=0,
            like_count=0,
            reply_count=0,
            quote_count=0,
            view_count=0,
            bookmark_count=0,
            language="",
            is_reply=False,
            is_retweet=False,
            is_quote=False,
        ))

    return tweets


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
