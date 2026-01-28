"""
SyntaX HTTP Client
High-performance HTTP client using curl-cffi with Chrome TLS fingerprinting.
This is the core component that bypasses Cloudflare and enables fast requests.
"""

import secrets
import random
import time
from typing import Optional, Dict, Any, Tuple
from dataclasses import dataclass

import orjson
from curl_cffi import requests
from curl_cffi.requests import Response

from .config import (
    BEARER_TOKEN,
    GRAPHQL_BASE_URL,
    GUEST_TOKEN_URL,
    X_HOME_URL,
    BROWSER_PROFILES,
    FEATURES,
    FIELD_TOGGLES,
)


@dataclass
class TokenSet:
    """A complete set of tokens needed for X API requests."""
    cf_cookie: str
    guest_token: str
    csrf_token: str
    created_at: float
    request_count: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "cf_cookie": self.cf_cookie,
            "guest_token": self.guest_token,
            "csrf_token": self.csrf_token,
            "created_at": self.created_at,
            "request_count": self.request_count,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "TokenSet":
        return cls(
            cf_cookie=data["cf_cookie"],
            guest_token=data["guest_token"],
            csrf_token=data["csrf_token"],
            created_at=data["created_at"],
            request_count=data.get("request_count", 0),
        )


class XClient:
    """
    High-performance X API client.

    Uses curl-cffi to impersonate Chrome's TLS fingerprint, bypassing Cloudflare.
    Response times: 50-200ms (vs 2000-5000ms for browser scraping).
    """

    def __init__(self, token_set: Optional[TokenSet] = None, browser: str = "chrome120"):
        self.token_set = token_set
        self._session = None
        self._browser = browser

    @property
    def session(self) -> requests.Session:
        """Lazy-load session with connection pooling."""
        if self._session is None:
            self._session = requests.Session(impersonate=self._browser)
        return self._session

    def _get_headers(self) -> Dict[str, str]:
        """Build headers for X API request."""
        if not self.token_set:
            raise ValueError("No token set configured")

        return {
            "authorization": f"Bearer {BEARER_TOKEN}",
            "x-guest-token": self.token_set.guest_token,
            "x-csrf-token": self.token_set.csrf_token,
            "x-twitter-active-user": "yes",
            "x-twitter-client-language": "en",
            "content-type": "application/json",
            "accept": "*/*",
            "accept-language": "en-US,en;q=0.9",
            "origin": "https://x.com",
            "referer": "https://x.com/",
            "sec-ch-ua": '"Not_A Brand";v="8", "Chromium";v="120", "Google Chrome";v="120"',
            "sec-ch-ua-mobile": "?0",
            "sec-ch-ua-platform": '"macOS"',
            "sec-fetch-dest": "empty",
            "sec-fetch-mode": "cors",
            "sec-fetch-site": "same-origin",
        }

    def _get_cookies(self) -> Dict[str, str]:
        """Build cookies for X API request."""
        if not self.token_set:
            raise ValueError("No token set configured")

        return {
            "__cf_bm": self.token_set.cf_cookie,
            "guest_id": f"v1%3A{self.token_set.guest_token}",
            "gt": self.token_set.guest_token,
            "ct0": self.token_set.csrf_token,
        }

    def graphql_request(
        self,
        query_id: str,
        operation_name: str,
        variables: Dict[str, Any],
        features: Optional[Dict[str, bool]] = None,
        field_toggles: Optional[Dict[str, bool]] = None,
        debug: bool = False,
    ) -> Tuple[Dict[str, Any], float]:
        """
        Make a GraphQL request to X API.

        Returns:
            Tuple of (response_data, response_time_ms)
        """
        url = f"{GRAPHQL_BASE_URL}/{query_id}/{operation_name}"

        params = {
            "variables": orjson.dumps(variables).decode(),
            "features": orjson.dumps(features or FEATURES).decode(),
        }

        if field_toggles:
            params["fieldToggles"] = orjson.dumps(field_toggles).decode()

        headers = self._get_headers()
        cookies = self._get_cookies()

        if debug:
            print(f"  URL: {url}")
            print(f"  Headers: {headers}")
            print(f"  Cookies: {list(cookies.keys())}")

        start_time = time.perf_counter()

        response = self.session.get(
            url,
            params=params,
            headers=headers,
            cookies=cookies,
            timeout=15,
        )

        elapsed_ms = (time.perf_counter() - start_time) * 1000

        if debug or response.status_code != 200:
            print(f"  Status: {response.status_code}")
            print(f"  Response: {response.text[:500] if response.text else 'empty'}")

        response.raise_for_status()

        if self.token_set:
            self.token_set.request_count += 1

        return orjson.loads(response.content), elapsed_ms

    def close(self):
        """Close the session."""
        if self._session:
            self._session.close()
            self._session = None


def get_cf_cookie(browser: str = "chrome120") -> Optional[str]:
    """
    Get a fresh Cloudflare __cf_bm cookie.

    This is the critical first step - Cloudflare sets this cookie
    to verify the TLS fingerprint matches a real browser.
    """
    try:
        response = requests.get(
            X_HOME_URL,
            impersonate=browser,
            timeout=15,
        )

        # curl-cffi cookies is a dict-like object
        cookies = response.cookies

        # Try different access methods
        if hasattr(cookies, 'get'):
            cf_cookie = cookies.get("__cf_bm")
            if cf_cookie:
                return cf_cookie

        # Try as dict
        if isinstance(cookies, dict):
            return cookies.get("__cf_bm")

        # Try iteration
        for key in cookies:
            if key == "__cf_bm":
                return cookies[key]

        # Check Set-Cookie headers directly
        set_cookie = response.headers.get("set-cookie", "")
        if "__cf_bm=" in set_cookie:
            # Extract value from Set-Cookie header
            for part in set_cookie.split(";"):
                if "__cf_bm=" in part:
                    return part.split("=", 1)[1].strip()

        print(f"No __cf_bm cookie found. Cookies: {dict(cookies) if cookies else 'None'}")
        return None

    except Exception as e:
        print(f"Error getting CF cookie: {e}")
        import traceback
        traceback.print_exc()
        return None


def get_guest_token(cf_cookie: str, browser: str = "chrome120") -> Optional[str]:
    """
    Get a guest token from X API.

    Requires a valid Cloudflare cookie.
    """
    try:
        response = requests.post(
            GUEST_TOKEN_URL,
            headers={
                "authorization": f"Bearer {BEARER_TOKEN}",
            },
            cookies={"__cf_bm": cf_cookie},
            impersonate=browser,
            timeout=10,
        )

        response.raise_for_status()
        data = orjson.loads(response.content)
        return data.get("guest_token")

    except Exception as e:
        print(f"Error getting guest token: {e}")
        return None


def create_token_set(browser: Optional[str] = None) -> Optional[TokenSet]:
    """
    Create a complete token set for X API requests.

    This generates:
    1. Cloudflare __cf_bm cookie (bypasses bot detection)
    2. Guest token (authenticates requests)
    3. CSRF token (required header)
    """
    browser = browser or random.choice(BROWSER_PROFILES)

    # Step 1: Get Cloudflare cookie
    cf_cookie = get_cf_cookie(browser)
    if not cf_cookie:
        return None

    # Step 2: Get guest token
    guest_token = get_guest_token(cf_cookie, browser)
    if not guest_token:
        return None

    # Step 3: Generate CSRF token
    csrf_token = secrets.token_hex(16)

    return TokenSet(
        cf_cookie=cf_cookie,
        guest_token=guest_token,
        csrf_token=csrf_token,
        created_at=time.time(),
    )


# Quick test function
def test_connection() -> bool:
    """Test if we can connect to X API."""
    token_set = create_token_set()
    if not token_set:
        print("Failed to create token set")
        return False

    print(f"Token set created successfully!")
    print(f"  CF Cookie: {token_set.cf_cookie[:20]}...")
    print(f"  Guest Token: {token_set.guest_token}")
    print(f"  CSRF Token: {token_set.csrf_token}")
    return True


if __name__ == "__main__":
    test_connection()
