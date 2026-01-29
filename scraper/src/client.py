"""
SyntaX HTTP Client
High-performance HTTP client using curl-cffi with Chrome TLS fingerprinting.
Includes x-client-transaction-id generation for X's anti-bot bypass.
"""

import secrets
import random
import time
from typing import Optional, Dict, Any, Tuple
from dataclasses import dataclass
from urllib.parse import urlparse

import bs4
import orjson
from curl_cffi import requests
from x_client_transaction import ClientTransaction
from x_client_transaction.utils import get_ondemand_file_url

from config import (
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
    guest_token: str
    csrf_token: str
    created_at: float
    cf_cookie: Optional[str] = None
    request_count: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "guest_token": self.guest_token,
            "csrf_token": self.csrf_token,
            "created_at": self.created_at,
            "cf_cookie": self.cf_cookie or "",
            "request_count": self.request_count,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "TokenSet":
        return cls(
            guest_token=data["guest_token"],
            csrf_token=data["csrf_token"],
            created_at=float(data["created_at"]),
            cf_cookie=data.get("cf_cookie") or None,
            request_count=int(data.get("request_count", 0)),
        )


class TransactionIDGenerator:
    """
    Generates valid x-client-transaction-id headers.

    X requires this cryptographic header on all GraphQL requests.
    It's computed from the homepage HTML, an obfuscated JS file,
    and the specific request path.
    """

    def __init__(self):
        self._ct: Optional[ClientTransaction] = None
        self._initialized_at: float = 0
        self._ttl: int = 1800  # Re-init every 30 min

    def _ensure_initialized(self, browser: str = "chrome131"):
        """Initialize or refresh the transaction generator."""
        if self._ct and (time.time() - self._initialized_at) < self._ttl:
            return

        session = requests.Session(impersonate=browser)
        try:
            home = session.get(X_HOME_URL, timeout=15)
            soup = bs4.BeautifulSoup(home.content, "html.parser")
            ondemand_url = get_ondemand_file_url(soup)
            ondemand_js = session.get(ondemand_url, timeout=30).text
            self._ct = ClientTransaction(soup, ondemand_js)
            self._initialized_at = time.time()
        finally:
            session.close()

    def generate(self, method: str, path: str) -> str:
        """Generate a transaction ID for the given method and path."""
        self._ensure_initialized()
        return self._ct.generate_transaction_id(method=method, path=path)


# Module-level singleton
_txn_generator = TransactionIDGenerator()


class XClient:
    """
    High-performance X API client.

    Uses curl-cffi to impersonate Chrome's TLS fingerprint, bypassing Cloudflare.
    Generates valid x-client-transaction-id headers for anti-bot bypass.
    Response times: 50-300ms.
    """

    def __init__(self, token_set: Optional[TokenSet] = None, browser: str = "chrome131"):
        self.token_set = token_set
        self._session = None
        self._browser = browser

    @property
    def session(self) -> requests.Session:
        """Lazy-load session with connection pooling."""
        if self._session is None:
            self._session = requests.Session(impersonate=self._browser)
        return self._session

    def _get_headers(self, url: str, method: str = "GET") -> Dict[str, str]:
        """Build headers for X API request, including transaction ID."""
        if not self.token_set:
            raise ValueError("No token set configured")

        path = urlparse(url).path
        txn_id = _txn_generator.generate(method=method, path=path)

        return {
            "authorization": f"Bearer {BEARER_TOKEN}",
            "x-guest-token": self.token_set.guest_token,
            "x-csrf-token": self.token_set.csrf_token,
            "x-client-transaction-id": txn_id,
            "x-twitter-active-user": "yes",
            "x-twitter-client-language": "en",
            "content-type": "application/json",
            "accept": "*/*",
            "accept-language": "en-US,en;q=0.9",
            "origin": "https://x.com",
            "referer": "https://x.com/",
            "sec-fetch-dest": "empty",
            "sec-fetch-mode": "cors",
            "sec-fetch-site": "same-site",
        }

    def _get_cookies(self) -> Dict[str, str]:
        """Build cookies for X API request."""
        if not self.token_set:
            raise ValueError("No token set configured")

        cookies = {
            "guest_id": f"v1%3A{self.token_set.guest_token}",
            "gt": self.token_set.guest_token,
            "ct0": self.token_set.csrf_token,
        }
        if self.token_set.cf_cookie:
            cookies["__cf_bm"] = self.token_set.cf_cookie

        return cookies

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

        headers = self._get_headers(url)
        cookies = self._get_cookies()

        if debug:
            print(f"  URL: {url}")
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


def get_guest_token(browser: str = "chrome131") -> Optional[str]:
    """Get a guest token from X API."""
    try:
        response = requests.post(
            GUEST_TOKEN_URL,
            headers={"authorization": f"Bearer {BEARER_TOKEN}"},
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

    Generates:
    1. Guest token (authenticates requests)
    2. CSRF token (required header)
    """
    browser = browser or random.choice(BROWSER_PROFILES)

    guest_token = get_guest_token(browser)
    if not guest_token:
        return None

    csrf_token = secrets.token_hex(16)

    return TokenSet(
        guest_token=guest_token,
        csrf_token=csrf_token,
        created_at=time.time(),
    )


def test_connection() -> bool:
    """Test if we can connect to X API and get user data."""
    token_set = create_token_set()
    if not token_set:
        print("Failed to create token set")
        return False

    print(f"Token set created!")
    print(f"  Guest Token: {token_set.guest_token}")

    client = XClient(token_set=token_set)
    try:
        data, elapsed = client.graphql_request(
            query_id="-oaLodhGbbnzJBACb1kk2Q",
            operation_name="UserByScreenName",
            variables={"screen_name": "elonmusk", "withGrokTranslatedBio": False},
            field_toggles={"withPayments": False, "withAuxiliaryUserLabels": True},
            debug=True,
        )
        print(f"\nResponse in {elapsed:.0f}ms")
        user = data.get("data", {}).get("user", {}).get("result", {})
        core = user.get("core", {})
        legacy = user.get("legacy", {})
        print(f"User: @{core.get('screen_name')} - {core.get('name')}")
        print(f"Followers: {legacy.get('followers_count')}")
        return True
    except Exception as e:
        print(f"Error: {e}")
        return False
    finally:
        client.close()


if __name__ == "__main__":
    test_connection()
