"""
SyntaX Social Endpoints
Endpoints for Followers, Following, and Mentions.

All of these are auth-gated — guest tokens get 404.
Uses the AccountPool to rotate across authenticated accounts.
"""

from typing import Optional, Dict, Any, List
from dataclasses import dataclass, field

from client import XClient, token_set_from_account
from config import QUERY_IDS, TWEET_FEATURES, FIELD_TOGGLES
from account_pool import get_account_pool


@dataclass(slots=True)
class UserSummary:
    """Lightweight user object returned from follower/following lists."""
    id: str
    username: str
    name: str
    bio: str
    followers_count: int
    following_count: int
    is_blue_verified: bool
    profile_image_url: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "username": self.username,
            "name": self.name,
            "bio": self.bio,
            "followers_count": self.followers_count,
            "following_count": self.following_count,
            "is_blue_verified": self.is_blue_verified,
            "profile_image_url": self.profile_image_url,
        }


def _parse_user_result(result: Dict[str, Any]) -> Optional[UserSummary]:
    """Parse a user result from timeline entries."""
    if not result:
        return None

    typename = result.get("__typename")
    if typename == "UserUnavailable":
        return None

    legacy = result.get("legacy") or {}
    core = result.get("core") or {}

    return UserSummary(
        id=result.get("rest_id", ""),
        username=core.get("screen_name") or legacy.get("screen_name", ""),
        name=core.get("name") or legacy.get("name", ""),
        bio=legacy.get("description", ""),
        followers_count=legacy.get("followers_count", 0),
        following_count=legacy.get("friends_count", 0),
        is_blue_verified=result.get("is_blue_verified", False),
        profile_image_url=legacy.get("profile_image_url_https", ""),
    )


def _social_request(
    operation: str,
    user_id: str,
    client: XClient,
    count: int = 20,
    cursor: Optional[str] = None,
    debug: bool = False,
) -> tuple[List[UserSummary], Optional[str], float]:
    """Shared logic for Followers/Following (both auth-gated)."""
    query_id = QUERY_IDS.get(operation)
    if not query_id:
        raise ValueError(f"{operation} query ID not configured")

    variables = {
        "userId": user_id,
        "count": count,
        "includePromotedContent": False,
    }
    if cursor:
        variables["cursor"] = cursor

    # Need an authenticated account
    use_client = client
    account = None
    pool = None

    if not (client.token_set and client.token_set.is_authenticated):
        pool = get_account_pool()
        account = pool.acquire() if pool.has_accounts else None
        if not account:
            raise ValueError(f"{operation} requires an authenticated account. "
                             f"Add accounts to accounts.json.")
        auth_ts = token_set_from_account(account)
        use_client = XClient(token_set=auth_ts, proxy=account.proxy_dict)

    try:
        data, elapsed_ms = use_client.graphql_request(
            query_id=query_id,
            operation_name=operation,
            variables=variables,
            features=TWEET_FEATURES,
            field_toggles=FIELD_TOGGLES,
            debug=debug,
        )
        if account and pool:
            pool.release(account, success=True)
    except Exception as e:
        if account and pool:
            status = 429 if "429" in str(e) else 403 if "403" in str(e) else 404
            pool.release(account, success=False, status_code=status)
        raise
    finally:
        if account and use_client is not client:
            use_client.close()

    # Parse the response
    users, next_cursor = _parse_social_response(data)
    return users, next_cursor, elapsed_ms


def _parse_social_response(
    data: Dict[str, Any],
) -> tuple[List[UserSummary], Optional[str]]:
    """Parse followers/following response."""
    users = []
    next_cursor = None

    timeline = (
        data.get("data", {})
        .get("user", {})
        .get("result", {})
        .get("timeline", {})
        .get("timeline", {})
    )

    for instruction in timeline.get("instructions", []):
        if instruction.get("type") != "TimelineAddEntries":
            continue
        for entry in instruction.get("entries", []):
            content = entry.get("content", {})

            if content.get("entryType") == "TimelineTimelineItem":
                result = (
                    content.get("itemContent", {})
                    .get("user_results", {})
                    .get("result", {})
                )
                user = _parse_user_result(result)
                if user:
                    users.append(user)

            elif content.get("entryType") == "TimelineTimelineCursor":
                if content.get("cursorType") == "Bottom":
                    next_cursor = content.get("value")

    return users, next_cursor


# ── Public API ─────────────────────────────────────────────

def get_followers(
    user_id: str,
    client: XClient,
    count: int = 20,
    cursor: Optional[str] = None,
    debug: bool = False,
) -> tuple[List[UserSummary], Optional[str], float]:
    """Get a user's followers (auth-gated).

    Returns:
        Tuple of (users, next_cursor, response_time_ms)
    """
    return _social_request("Followers", user_id, client, count, cursor, debug)


def get_following(
    user_id: str,
    client: XClient,
    count: int = 20,
    cursor: Optional[str] = None,
    debug: bool = False,
) -> tuple[List[UserSummary], Optional[str], float]:
    """Get users that a user follows (auth-gated).

    Returns:
        Tuple of (users, next_cursor, response_time_ms)
    """
    return _social_request("Following", user_id, client, count, cursor, debug)
