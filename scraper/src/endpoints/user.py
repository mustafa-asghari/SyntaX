"""
SyntaX User Endpoints
Endpoints for fetching X user data.

Speed optimizations:
- __slots__ on User dataclass for faster attribute access
"""

from typing import Optional, Dict, Any
from dataclasses import dataclass

from client import XClient, TokenSet
from config import QUERY_IDS, FEATURES, FIELD_TOGGLES


@dataclass(slots=True)
class User:
    """Parsed X user data. Uses slots=True for faster access."""
    id: str
    username: str
    name: str
    bio: str
    location: str
    website: str
    followers_count: int
    following_count: int
    tweet_count: int
    listed_count: int
    created_at: str
    is_verified: bool
    is_blue_verified: bool
    profile_image_url: str
    banner_url: Optional[str]
    raw_json: Dict[str, Any]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "username": self.username,
            "name": self.name,
            "bio": self.bio,
            "location": self.location,
            "website": self.website,
            "followers_count": self.followers_count,
            "following_count": self.following_count,
            "tweet_count": self.tweet_count,
            "listed_count": self.listed_count,
            "created_at": self.created_at,
            "is_verified": self.is_verified,
            "is_blue_verified": self.is_blue_verified,
            "profile_image_url": self.profile_image_url,
            "banner_url": self.banner_url,
        }


def parse_user_result(data: Dict[str, Any]) -> Optional[User]:
    """Parse user data from X API response."""
    try:
        # Navigate to user result
        result = data.get("data", {}).get("user", {}).get("result", {})

        if not result or result.get("__typename") == "UserUnavailable":
            return None

        legacy = result.get("legacy", {})
        core = result.get("core", {})

        # Extract entities for URL
        entities = legacy.get("entities", {})
        url_entities = entities.get("url", {}).get("urls", [])
        website = url_entities[0].get("expanded_url", "") if url_entities else ""

        # screen_name/name can be in core (new format) or legacy (old format)
        username = core.get("screen_name") or legacy.get("screen_name", "")
        name = core.get("name") or legacy.get("name", "")
        created_at = core.get("created_at") or legacy.get("created_at", "")

        # Profile image can be in avatar (new) or legacy (old)
        avatar = result.get("avatar", {})
        profile_image = avatar.get("image_url") or legacy.get("profile_image_url_https", "")
        profile_image = profile_image.replace("_normal", "_400x400")

        return User(
            id=result.get("rest_id", ""),
            username=username,
            name=name,
            bio=legacy.get("description", ""),
            location=legacy.get("location", ""),
            website=website,
            followers_count=legacy.get("followers_count", 0),
            following_count=legacy.get("friends_count", 0),
            tweet_count=legacy.get("statuses_count", 0),
            listed_count=legacy.get("listed_count", 0),
            created_at=created_at,
            is_verified=legacy.get("verified", False),
            is_blue_verified=result.get("is_blue_verified", False),
            profile_image_url=profile_image,
            banner_url=legacy.get("profile_banner_url"),
            raw_json=data,
        )

    except Exception as e:
        print(f"Error parsing user: {e}")
        return None


def get_user_by_username(
    username: str,
    client: XClient,
    debug: bool = False,
) -> tuple[Optional[User], float]:
    """
    Get user profile by username.

    Args:
        username: X username (without @)
        client: XClient instance with token set
        debug: Print debug info

    Returns:
        Tuple of (User or None, response_time_ms)
    """
    query_id = QUERY_IDS.get("UserByScreenName")
    if not query_id:
        raise ValueError("UserByScreenName query ID not configured")

    variables = {
        "screen_name": username,
        "withGrokTranslatedBio": False,
    }

    data, elapsed_ms = client.graphql_request(
        query_id=query_id,
        operation_name="UserByScreenName",
        variables=variables,
        features=FEATURES,
        field_toggles=FIELD_TOGGLES,
        debug=debug,
    )

    user = parse_user_result(data)
    return user, elapsed_ms


def get_user_by_id(
    user_id: str,
    client: XClient,
) -> tuple[Optional[User], float]:
    """
    Get user profile by user ID.

    Args:
        user_id: X user ID (numeric string)
        client: XClient instance with token set

    Returns:
        Tuple of (User or None, response_time_ms)
    """
    query_id = QUERY_IDS.get("UserByRestId")
    if not query_id:
        raise ValueError("UserByRestId query ID not configured - run query_monitor to discover it")

    variables = {
        "userId": user_id,
        "withGrokTranslatedBio": False,
    }

    data, elapsed_ms = client.graphql_request(
        query_id=query_id,
        operation_name="UserByRestId",
        variables=variables,
        features=FEATURES,
        field_toggles=FIELD_TOGGLES,
    )

    user = parse_user_result(data)
    return user, elapsed_ms
