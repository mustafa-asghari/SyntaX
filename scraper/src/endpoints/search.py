"""
SyntaX Search Endpoints
Endpoints for searching X tweets.
"""

from typing import Optional, Dict, Any, List

from ..client import XClient
from ..config import QUERY_IDS, TWEET_FEATURES
from .tweet import Tweet, _parse_tweet_result


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

    data, elapsed_ms = client.graphql_request(
        query_id=query_id,
        operation_name="SearchTimeline",
        variables=variables,
        features=TWEET_FEATURES,
        debug=debug,
    )

    tweets, next_cursor = _parse_search_response(data)
    return tweets, next_cursor, elapsed_ms


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
