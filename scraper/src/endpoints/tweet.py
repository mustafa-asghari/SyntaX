"""
SyntaX Tweet Endpoints
Endpoints for fetching X tweet data.

Speed optimizations:
- __slots__ on Tweet dataclass for faster attribute access
- Inlined parsing logic with local variable caching
- Reduced dict.get() calls with walrus operator
"""

from typing import Optional, Dict, Any, List
from dataclasses import dataclass, field

from client import XClient, token_set_from_account
from config import QUERY_IDS, TWEET_FEATURES, FIELD_TOGGLES
from account_pool import get_account_pool


@dataclass(slots=True)
class Tweet:
    """Parsed X tweet data. Uses slots=True for faster access."""
    id: str
    text: str
    created_at: str
    author_id: str
    author_username: str
    author_name: str
    retweet_count: int
    like_count: int
    reply_count: int
    quote_count: int
    view_count: int
    bookmark_count: int
    language: str
    is_reply: bool
    is_retweet: bool
    is_quote: bool
    media: List[Dict[str, Any]] = field(default_factory=list)
    urls: List[Dict[str, str]] = field(default_factory=list)
    raw_json: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "text": self.text,
            "created_at": self.created_at,
            "author_id": self.author_id,
            "author_username": self.author_username,
            "author_name": self.author_name,
            "retweet_count": self.retweet_count,
            "like_count": self.like_count,
            "reply_count": self.reply_count,
            "quote_count": self.quote_count,
            "view_count": self.view_count,
            "bookmark_count": self.bookmark_count,
            "language": self.language,
            "is_reply": self.is_reply,
            "is_retweet": self.is_retweet,
            "is_quote": self.is_quote,
            "media": self.media,
            "urls": self.urls,
        }


# Cache dict.get for micro-optimization in hot paths
_EMPTY_DICT: Dict[str, Any] = {}
_EMPTY_LIST: List[Any] = []

def _parse_tweet_result(result: Dict[str, Any]) -> Optional[Tweet]:
    """Parse a single tweet result object. Optimized for speed."""
    if not result:
        return None
    
    typename = result.get("__typename")
    if typename == "TweetUnavailable":
        return None

    # Handle TweetWithVisibilityResults wrapper (use walrus to avoid double lookup)
    if typename == "TweetWithVisibilityResults":
        result = result.get("tweet") or result

    # Cache nested lookups in local vars (faster than repeated dict access)
    legacy = result.get("legacy") or _EMPTY_DICT
    core_results = result.get("core") or _EMPTY_DICT
    user_results = core_results.get("user_results") or _EMPTY_DICT
    core = user_results.get("result") or _EMPTY_DICT
    user_core = core.get("core") or _EMPTY_DICT
    user_legacy = core.get("legacy") or _EMPTY_DICT

    # View count (avoid repeated lookups)
    views = result.get("views") or _EMPTY_DICT
    view_count_raw = views.get("count")
    view_count = int(view_count_raw) if view_count_raw else 0

    # Extract media (inline for speed)
    media_list = []
    if (ext_entities := legacy.get("extended_entities")):
        if (media_items := ext_entities.get("media")):
            for m in media_items:
                mtype = m.get("type", "photo")
                media_item = {
                    "type": mtype,
                    "url": m.get("media_url_https", ""),
                    "expanded_url": m.get("expanded_url", ""),
                }
                if mtype in ("video", "animated_gif"):
                    if (video_info := m.get("video_info")):
                        variants = video_info.get("variants") or _EMPTY_LIST
                        mp4s = [v for v in variants if v.get("content_type") == "video/mp4"]
                        if mp4s:
                            best = max(mp4s, key=lambda v: v.get("bitrate", 0))
                            media_item["video_url"] = best.get("url", "")
                media_list.append(media_item)

    # Extract URLs (inline for speed)
    url_list = []
    if (entities := legacy.get("entities")):
        if (urls := entities.get("urls")):
            for u in urls:
                url_list.append({
                    "url": u.get("expanded_url") or u.get("url", ""),
                    "display_url": u.get("display_url", ""),
                })

    try:
        return Tweet(
            id=legacy.get("id_str") or result.get("rest_id", ""),
            text=legacy.get("full_text", ""),
            created_at=legacy.get("created_at", ""),
            author_id=legacy.get("user_id_str") or core.get("rest_id", ""),
            author_username=user_core.get("screen_name") or user_legacy.get("screen_name", ""),
            author_name=user_core.get("name") or user_legacy.get("name", ""),
            retweet_count=legacy.get("retweet_count", 0),
            like_count=legacy.get("favorite_count", 0),
            reply_count=legacy.get("reply_count", 0),
            quote_count=legacy.get("quote_count", 0),
            view_count=view_count,
            bookmark_count=legacy.get("bookmark_count", 0),
            language=legacy.get("lang", ""),
            is_reply=bool(legacy.get("in_reply_to_status_id_str")),
            is_retweet=bool(legacy.get("retweeted_status_result")),
            is_quote=legacy.get("is_quote_status", False),
            media=media_list,
            urls=url_list,
            raw_json=result,
        )
    except Exception as e:
        print(f"Error parsing tweet: {e}")
        return None


def get_tweet_by_id(
    tweet_id: str,
    client: XClient,
    debug: bool = False,
) -> tuple[Optional[Tweet], float]:
    """Get a single tweet by its ID."""
    query_id = QUERY_IDS.get("TweetResultByRestId")
    if not query_id:
        raise ValueError("TweetResultByRestId query ID not configured")

    variables = {
        "tweetId": tweet_id,
        "withCommunity": False,
        "includePromotedContent": False,
        "withVoice": False,
    }

    data, elapsed_ms = client.graphql_request(
        query_id=query_id,
        operation_name="TweetResultByRestId",
        variables=variables,
        features=TWEET_FEATURES,
        debug=debug,
    )

    result = data.get("data", {}).get("tweetResult", {}).get("result", {})
    tweet = _parse_tweet_result(result)
    return tweet, elapsed_ms


def get_tweet_detail(
    tweet_id: str,
    client: XClient,
    debug: bool = False,
) -> tuple[Optional[Tweet], List[Tweet], float]:
    """
    Get tweet detail with conversation thread.

    Guest tokens get 404 for TweetDetail — uses an authenticated
    account from the pool to get the full thread with replies.
    Falls back to TweetResultByRestId (no replies) if no accounts
    are available.

    Returns:
        Tuple of (main_tweet, reply_tweets, response_time_ms)
    """
    query_id = QUERY_IDS.get("TweetDetail")
    if not query_id:
        raise ValueError("TweetDetail query ID not configured")

    variables = {
        "focalTweetId": tweet_id,
        "with_rux_injections": False,
        "rankingMode": "Relevance",
        "includePromotedContent": False,
        "withCommunity": True,
        "withQuickPromoteEligibilityTweetFields": False,
        "withBirdwatchNotes": True,
        "withVoice": True,
    }

    # Pick the right client: auth account for guest tokens, direct for auth
    use_client = client
    account = None
    pool = None
    session = None

    if client.token_set and not client.token_set.is_authenticated:
        # Guest token — grab an auth account from the pool
        pool = get_account_pool()
        account = pool.acquire() if pool.has_accounts else None

        if account:
            auth_ts = token_set_from_account(account)
            session = account.acquire_session()
            use_client = XClient(token_set=auth_ts, proxy=account.proxy_dict,
                                 session=session)
        else:
            # No accounts — fall back to single tweet (no replies)
            main_tweet, elapsed_ms = get_tweet_by_id(tweet_id, client, debug=debug)
            return main_tweet, [], elapsed_ms

    try:
        data, elapsed_ms = use_client.graphql_request(
            query_id=query_id,
            operation_name="TweetDetail",
            variables=variables,
            features=TWEET_FEATURES,
            field_toggles={"withArticlePlainText": False},
            debug=debug,
        )
        if account and pool:
            pool.release(account, success=True)
    except Exception:
        if account and pool:
            status = 404
            pool.release(account, success=False, status_code=status)
        # Fall back to single tweet lookup
        main_tweet, elapsed_ms = get_tweet_by_id(tweet_id, client, debug=debug)
        return main_tweet, [], elapsed_ms
    finally:
        if account and use_client is not client:
            use_client.close()  # no-op for external session
        if account and session:
            account.release_session(session)

    # Parse conversation thread
    main_tweet = None
    replies = []

    instructions = (
        data.get("data", {})
        .get("threaded_conversation_with_injections_v2", {})
        .get("instructions", [])
    )

    for instruction in instructions:
        if instruction.get("type") != "TimelineAddEntries":
            continue
        for entry in instruction.get("entries", []):
            content = entry.get("content", {})
            entry_type = content.get("entryType", "")

            if entry_type == "TimelineTimelineItem":
                result = (
                    content.get("itemContent", {})
                    .get("tweet_results", {})
                    .get("result", {})
                )
                tweet = _parse_tweet_result(result)
                if tweet:
                    if tweet.id == tweet_id:
                        main_tweet = tweet
                    else:
                        replies.append(tweet)

            elif entry_type == "TimelineTimelineModule":
                for item in content.get("items", []):
                    result = (
                        item.get("item", {})
                        .get("itemContent", {})
                        .get("tweet_results", {})
                        .get("result", {})
                    )
                    tweet = _parse_tweet_result(result)
                    if tweet and tweet.id != tweet_id:
                        replies.append(tweet)

    return main_tweet, replies, elapsed_ms


def get_user_tweets(
    user_id: str,
    client: XClient,
    count: int = 20,
    cursor: Optional[str] = None,
    debug: bool = False,
) -> tuple[List[Tweet], Optional[str], float]:
    """
    Get tweets from a user's timeline.

    Returns:
        Tuple of (tweets, next_cursor, response_time_ms)
    """
    query_id = QUERY_IDS.get("UserTweets")
    if not query_id:
        raise ValueError("UserTweets query ID not configured")

    variables = {
        "userId": user_id,
        "count": count,
        "includePromotedContent": False,
        "withQuickPromoteEligibilityTweetFields": False,
        "withVoice": True,
        "withV2Timeline": True,
    }
    if cursor:
        variables["cursor"] = cursor

    data, elapsed_ms = client.graphql_request(
        query_id=query_id,
        operation_name="UserTweets",
        variables=variables,
        features=TWEET_FEATURES,
        debug=debug,
    )

    tweets, next_cursor = _parse_timeline_response(data)
    return tweets, next_cursor, elapsed_ms


def _parse_timeline_response(data: Dict[str, Any]) -> tuple[List[Tweet], Optional[str]]:
    """Parse a timeline response into tweets and cursor."""
    tweets = []
    next_cursor = None

    user_result = data.get("data", {}).get("user", {}).get("result", {})
    # Handle both response formats:
    #   old: user_result.timeline_v2.timeline.instructions
    #   new: user_result.timeline.timeline.instructions (double-nested)
    timeline = (
        user_result.get("timeline_v2", {}).get("timeline", {})
        or user_result.get("timeline", {}).get("timeline", {})
    )

    for instruction in timeline.get("instructions", []):
        itype = instruction.get("type", "")

        # Standard entries (TimelineAddEntries)
        if itype == "TimelineAddEntries":
            for entry in instruction.get("entries", []):
                _parse_timeline_entry(entry, tweets)
                # Check for cursor
                content = entry.get("content", {})
                if content.get("entryType") == "TimelineTimelineCursor":
                    if content.get("cursorType") == "Bottom":
                        next_cursor = content.get("value")

        # Pinned tweet (TimelinePinEntry)
        elif itype == "TimelinePinEntry":
            entry = instruction.get("entry", {})
            _parse_timeline_entry(entry, tweets)

    return tweets, next_cursor


def _parse_timeline_entry(entry: Dict[str, Any], tweets: List[Tweet]):
    """Parse a single timeline entry and append any tweets found."""
    content = entry.get("content", {})
    entry_type = content.get("entryType", "")

    if entry_type == "TimelineTimelineItem":
        result = (
            content.get("itemContent", {})
            .get("tweet_results", {})
            .get("result", {})
        )
        tweet = _parse_tweet_result(result)
        if tweet:
            tweets.append(tweet)
