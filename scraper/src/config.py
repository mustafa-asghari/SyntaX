"""
SyntaX Configuration
Contains all constants for X API access including captured query IDs, features, and headers.
"""

import os
from typing import Dict, Any

# Bearer token (public, used by X web app)
# This is the standard guest bearer token from X's web app
BEARER_TOKEN = "AAAAAAAAAAAAAAAAAAAAANRILgAAAAAAnNwIzUejRCOuH5E6I8xnZz4puTs%3D1Zv7ttfk8LF81IUq16cHjhLTvJu4FA33AGWWjCpTnA"

# Base URLs - try both endpoints
GRAPHQL_BASE_URL = "https://api.x.com/graphql"
GUEST_TOKEN_URL = "https://api.x.com/1.1/guest/activate.json"
X_HOME_URL = "https://x.com"

# Query IDs (captured from X's bundles - auto-updated by query_monitor)
QUERY_IDS: Dict[str, str] = {
    # User endpoints
    "UserByScreenName": "-oaLodhGbbnzJBACb1kk2Q",
    "UserByRestId": "Bbaot8ySMtJD7K2t01gW7A",
    "UserTweets": "a3SQAz_VP9k8VWDr9bMcXQ",
    "UserTweetsAndReplies": "NullQbZlUJl-u6oBYRdrVw",
    "UserMedia": "8HCIrWwy4C0fBTbPnMq5aA",
    "Likes": "fuBEtiFu3uQFuPDTsv4bfg",
    "Followers": "oQWxG6XdR5SPvMBsPiKUPQ",
    "Following": "i2GOldCH2D3OUEhAdimLrA",

    # Tweet endpoints
    "TweetResultByRestId": "0aTrQMKgj95K791yXeNDRA",
    "TweetDetail": "Kzfv17rukSzjT96BerOWZA",

    # Search
    "SearchTimeline": "f_A-Gyo204PRxixpkrchJg",

    # Lists
    "ListLatestTweetsTimeline": "haIYNjPwpisz8wMc42vWpQ",
}

# Features object (required for all GraphQL requests)
FEATURES: Dict[str, bool] = {
    "hidden_profile_subscriptions_enabled": True,
    "profile_label_improvements_pcf_label_in_post_enabled": True,
    "responsive_web_profile_redirect_enabled": False,
    "rweb_tipjar_consumption_enabled": False,
    "verified_phone_label_enabled": False,
    "subscriptions_verification_info_is_identity_verified_enabled": True,
    "subscriptions_verification_info_verified_since_enabled": True,
    "highlights_tweets_tab_ui_enabled": True,
    "responsive_web_twitter_article_notes_tab_enabled": True,
    "subscriptions_feature_can_gift_premium": True,
    "creator_subscriptions_tweet_preview_api_enabled": True,
    "responsive_web_graphql_skip_user_profile_image_extensions_enabled": False,
    "responsive_web_graphql_timeline_navigation_enabled": True,
}

# Field toggles (for certain endpoints)
FIELD_TOGGLES: Dict[str, bool] = {
    "withPayments": False,
    "withAuxiliaryUserLabels": True,
}

# Tweet features (additional features for tweet endpoints)
TWEET_FEATURES: Dict[str, bool] = {
    **FEATURES,
    "rweb_video_timestamps_enabled": True,
    "longform_notetweets_consumption_enabled": True,
    "longform_notetweets_richtext_consumption_enabled": True,
    "longform_notetweets_inline_media_enabled": True,
    "responsive_web_enhance_cards_enabled": False,
    "tweetypie_unmention_optimization_enabled": True,
    "responsive_web_edit_tweet_api_enabled": True,
    "graphql_is_translatable_rweb_tweet_is_translatable_enabled": True,
    "view_counts_everywhere_api_enabled": True,
    "freedom_of_speech_not_reach_fetch_enabled": True,
    "standardized_nudges_misinfo": True,
    "tweet_with_visibility_results_prefer_gql_limited_actions_policy_enabled": True,
    "interactive_text_enabled": True,
    "responsive_web_text_conversations_enabled": False,
    "responsive_web_twitter_blue_verified_badge_is_enabled": True,
    "vibe_api_enabled": True,
    "responsive_web_graphql_exclude_directive_enabled": True,
    "communities_web_enable_tweet_community_results_fetch": True,
    "responsive_web_grok_image_annotation_enabled": True,
    "responsive_web_jetfuel_frame": False,
    "responsive_web_grok_show_grok_translated_post": False,
    "c9s_tweet_anatomy_moderator_badge_enabled": True,
    "responsive_web_grok_annotations_enabled": True,
    "post_ctas_fetch_enabled": True,
    "responsive_web_grok_analyze_button_fetch_trends_enabled": False,
    "articles_preview_enabled": True,
    "responsive_web_grok_analyze_post_followups_enabled": False,
    "responsive_web_grok_analysis_button_from_backend": False,
    "responsive_web_grok_community_note_auto_translation_is_enabled": False,
    "responsive_web_grok_share_attachment_enabled": False,
    "responsive_web_twitter_article_tweet_consumption_enabled": True,
    "creator_subscriptions_quote_tweet_preview_enabled": True,
    "premium_content_api_read_enabled": False,
    "longform_notetweets_rich_text_read_enabled": True,
    "tweet_awards_web_tipping_enabled": False,
    "responsive_web_grok_imagine_annotation_enabled": False,
    "rweb_video_screen_enabled": False,
}

# Browser impersonation profiles for rotation (curl-cffi supported)
BROWSER_PROFILES = [
    "chrome131",
    "chrome124",
    "chrome123",
    "chrome120",
]

# Redis keys
REDIS_KEYS = {
    "token_pool": "syntax:tokens:pool",
    "token_set": "syntax:tokens:set:",
    "query_ids": "syntax:config:query_ids",
    "features": "syntax:config:features",
    "cf_cookies": "syntax:tokens:cf_cookies",
    "rate_limit": "syntax:ratelimit:",
}

# Token settings
TOKEN_CONFIG = {
    "cf_cookie_ttl": 1200,  # 20 minutes (refresh before 30min expiry)
    "guest_token_ttl": 3600,  # 1 hour (refresh before 2hr expiry)
    "max_requests_per_token": 400,  # Retire token after this many requests
    "pool_min_size": 50,  # Minimum tokens in pool
    "pool_target_size": 100,  # Target pool size
    "generation_interval": 5,  # Seconds between generating new token sets
}

# Environment variables
def get_redis_url() -> str:
    return os.getenv("REDIS_URL", "redis://localhost:6379")

def get_clickhouse_host() -> str:
    return os.getenv("CLICKHOUSE_HOST", "localhost")

def get_clickhouse_port() -> int:
    return int(os.getenv("CLICKHOUSE_PORT", "8123"))
