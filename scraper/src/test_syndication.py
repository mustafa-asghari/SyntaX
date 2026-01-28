"""
Test the syndication API which is publicly accessible.
This helps us verify basic connectivity without auth issues.
"""

from curl_cffi import requests
import orjson


def test_syndication_api():
    """Test the syndication API for a tweet."""

    # Syndication API - publicly accessible
    # Example: Get tweet embed data
    tweet_id = "1234567890"  # Example tweet
    url = f"https://syndication.twitter.com/srv/timeline-profile/screen-name/elonmusk"

    print(f"Testing: {url}")

    resp = requests.get(
        url,
        impersonate="chrome120",
        headers={
            "accept": "*/*",
            "accept-language": "en-US,en;q=0.9",
        },
        timeout=15,
    )

    print(f"Status: {resp.status_code}")
    print(f"Response length: {len(resp.text)}")
    print(f"Response preview: {resp.text[:500] if resp.text else 'empty'}")

    return resp.status_code == 200


def test_api_x_com():
    """Test api.x.com directly."""

    url = "https://api.x.com/1.1/guest/activate.json"

    print(f"\nTesting guest token endpoint: {url}")

    bearer = "AAAAAAAAAAAAAAAAAAAAANRILgAAAAAAnNwIzUejRCOuH5E6I8xnZz4puTs%3D1Zv7ttfk8LF81IUq16cHjhLTvJu4FA33AGWWjCpTnA"

    resp = requests.post(
        url,
        headers={
            "authorization": f"Bearer {bearer}",
        },
        impersonate="chrome120",
        timeout=15,
    )

    print(f"Status: {resp.status_code}")
    print(f"Response: {resp.text[:500] if resp.text else 'empty'}")

    return resp.status_code == 200


def test_graphql_endpoint():
    """Test GraphQL endpoint directly with curl."""

    # First get a guest token
    bearer = "AAAAAAAAAAAAAAAAAAAAANRILgAAAAAAnNwIzUejRCOuH5E6I8xnZz4puTs%3D1Zv7ttfk8LF81IUq16cHjhLTvJu4FA33AGWWjCpTnA"

    # Get CF cookie first
    print("\n1. Getting CF cookie from x.com...")
    resp = requests.get("https://x.com", impersonate="chrome120", timeout=15)
    cf_cookie = None
    for key in resp.cookies:
        if key == "__cf_bm":
            cf_cookie = resp.cookies[key]
            break
    print(f"   CF Cookie: {cf_cookie[:30] if cf_cookie else 'None'}...")

    # Get guest token
    print("\n2. Getting guest token...")
    resp = requests.post(
        "https://api.x.com/1.1/guest/activate.json",
        headers={"authorization": f"Bearer {bearer}"},
        cookies={"__cf_bm": cf_cookie} if cf_cookie else {},
        impersonate="chrome120",
        timeout=15,
    )
    print(f"   Status: {resp.status_code}")

    if resp.status_code != 200:
        print(f"   Failed to get guest token: {resp.text}")
        return False

    guest_token = resp.json().get("guest_token")
    print(f"   Guest token: {guest_token}")

    # Try GraphQL request
    print("\n3. Testing GraphQL endpoint...")

    import secrets
    csrf = secrets.token_hex(16)

    variables = orjson.dumps({"screen_name": "elonmusk", "withGrokTranslatedBio": False}).decode()
    features = orjson.dumps({
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
    }).decode()
    field_toggles = orjson.dumps({"withPayments": False, "withAuxiliaryUserLabels": True}).decode()

    # Try both URL formats
    urls = [
        "https://x.com/i/api/graphql/-oaLodhGbbnzJBACb1kk2Q/UserByScreenName",
        "https://api.x.com/graphql/-oaLodhGbbnzJBACb1kk2Q/UserByScreenName",
        "https://twitter.com/i/api/graphql/-oaLodhGbbnzJBACb1kk2Q/UserByScreenName",
    ]

    for url in urls:
        print(f"\n   Trying: {url}")

        resp = requests.get(
            url,
            params={
                "variables": variables,
                "features": features,
                "fieldToggles": field_toggles,
            },
            headers={
                "authorization": f"Bearer {bearer}",
                "x-guest-token": guest_token,
                "x-csrf-token": csrf,
                "x-twitter-active-user": "yes",
                "x-twitter-client-language": "en",
                "content-type": "application/json",
                "accept": "*/*",
                "origin": "https://x.com",
                "referer": "https://x.com/",
            },
            cookies={
                "__cf_bm": cf_cookie,
                "guest_id": f"v1%3A{guest_token}",
                "gt": guest_token,
                "ct0": csrf,
            } if cf_cookie else {"gt": guest_token, "ct0": csrf},
            impersonate="chrome120",
            timeout=15,
        )

        print(f"   Status: {resp.status_code}")
        if resp.text:
            print(f"   Response: {resp.text[:300]}")

        if resp.status_code == 200:
            return True

    return False


if __name__ == "__main__":
    print("="*60)
    print("SyntaX Connectivity Test")
    print("="*60)

    test_api_x_com()
    test_graphql_endpoint()
