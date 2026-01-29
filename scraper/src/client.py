"""
SyntaX HTTP Client
High-performance HTTP client using curl-cffi with Chrome TLS fingerprinting.
Includes x-client-transaction-id generation for X's anti-bot bypass.

Speed optimizations:
- Module-level session pool (reuse TLS connections)
- Background TransactionIDGenerator init (no cold-start penalty)
- Pre-built header templates (avoid dict rebuilds)
- Session pre-warming (TLS handshake on init, not first request)
"""

import os
import secrets
import random
import time
import threading
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

from debug import RequestDebug, SpeedDebugger


# ── Token Set ───────────────────────────────────────────────

@dataclass
class TokenSet:
    """A complete set of tokens needed for X API requests."""
    guest_token: str
    csrf_token: str
    created_at: float
    cf_cookie: Optional[str] = None
    auth_token: Optional[str] = None
    ct0: Optional[str] = None
    request_count: int = 0

    @property
    def is_authenticated(self) -> bool:
        return bool(self.auth_token and self.ct0)

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




# ── Transaction ID Generator ────────────────────────────────

class TransactionIDGenerator:
    """
    Generates valid x-client-transaction-id headers.
    Initialized eagerly in a background thread to avoid cold-start penalty.
    TTL: 2 hours (X rotates keys slowly).
    """

    def __init__(self):
        self._ct: Optional[ClientTransaction] = None
        self._initialized_at: float = 0
        self._ttl: int = 7200  # 2 hours
        self._lock = threading.Lock()
        self._ready = threading.Event()

    def start_background_init(self):
        """Start initialization in background thread."""
        t = threading.Thread(target=self._init_sync, daemon=True)
        t.start()

    def _init_sync(self, browser: str = "chrome131"):
        """Initialize the transaction generator (blocking).
        Uses its own throwaway session to avoid polluting the API session with cookies."""
        try:
            session = requests.Session(impersonate=browser)
            try:
                home = session.get(X_HOME_URL, timeout=15)
                soup = bs4.BeautifulSoup(home.content, "html.parser")
                ondemand_url = get_ondemand_file_url(soup)
                ondemand_js = session.get(ondemand_url, timeout=30).text
            finally:
                session.close()

            with self._lock:
                self._ct = ClientTransaction(soup, ondemand_js)
                self._initialized_at = time.time()
            self._ready.set()
        except Exception as e:
            print(f"[TxnGen] Init error: {e}")
            self._ready.set()  # Unblock waiters even on failure

    def _ensure_initialized(self):
        """Ensure generator is ready, re-init if TTL expired."""
        if self._ct and (time.time() - self._initialized_at) < self._ttl:
            return

        if not self._ready.is_set():
            # Wait for background init (max 30s)
            self._ready.wait(timeout=30)
            if self._ct:
                return

        # Need fresh init (TTL expired or background init failed)
        self._init_sync()

    def generate(self, method: str, path: str) -> str:
        """Generate a transaction ID for the given method and path."""
        self._ensure_initialized()
        with self._lock:
            return self._ct.generate_transaction_id(method=method, path=path)


_txn_generator = TransactionIDGenerator()
# Start background init immediately on module import
_txn_generator.start_background_init()


# ── Pre-built Header Templates ──────────────────────────────

_HEADER_TEMPLATE_GUEST = {
    "authorization": f"Bearer {BEARER_TOKEN}",
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

_HEADER_TEMPLATE_AUTH = {
    "authorization": f"Bearer {BEARER_TOKEN}",
    "x-twitter-active-user": "yes",
    "x-twitter-client-language": "en",
    "x-twitter-auth-type": "OAuth2Session",
    "content-type": "application/json",
    "accept": "*/*",
    "accept-language": "en-US,en;q=0.9",
    "origin": "https://x.com",
    "referer": "https://x.com/",
    "sec-fetch-dest": "empty",
    "sec-fetch-mode": "cors",
    "sec-fetch-site": "same-site",
}


# ── XClient ─────────────────────────────────────────────────

class XClient:
    """
    High-performance X API client.

    Uses curl-cffi for Chrome TLS fingerprint, session pooling for
    connection reuse, and pre-built headers for minimal per-request overhead.
    """

    def __init__(self, token_set: Optional[TokenSet] = None, browser: str = "chrome131"):
        self.token_set = token_set
        self._browser = browser
        self._session = None

    @property
    def session(self) -> requests.Session:
        """Lazy-init session. One session per client = clean cookies + connection reuse."""
        if self._session is None:
            self._session = requests.Session(impersonate=self._browser)
        return self._session

    def _get_headers(self, url: str, method: str = "GET") -> Dict[str, str]:
        """Build headers with transaction ID. Uses pre-built template."""
        if not self.token_set:
            raise ValueError("No token set configured")

        path = urlparse(url).path
        txn_id = _txn_generator.generate(method=method, path=path)

        if self.token_set.is_authenticated:
            headers = _HEADER_TEMPLATE_AUTH.copy()
            headers["x-csrf-token"] = self.token_set.ct0
        else:
            headers = _HEADER_TEMPLATE_GUEST.copy()
            headers["x-guest-token"] = self.token_set.guest_token
            headers["x-csrf-token"] = self.token_set.csrf_token

        headers["x-client-transaction-id"] = txn_id
        return headers

    def _get_cookies(self) -> Dict[str, str]:
        """Build cookies for X API request."""
        if not self.token_set:
            raise ValueError("No token set configured")

        if self.token_set.is_authenticated:
            return {
                "auth_token": self.token_set.auth_token,
                "ct0": self.token_set.ct0,
            }

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
        debug_obj: Optional[RequestDebug] = None,
    ) -> Tuple[Dict[str, Any], float]:
        """
        Make a GraphQL request to X API.

        Args:
            debug_obj: Optional RequestDebug for detailed timing breakdown.

        Returns:
            Tuple of (response_data, response_time_ms)
        """
        rd = debug_obj

        url = f"{GRAPHQL_BASE_URL}/{query_id}/{operation_name}"

        if rd:
            rd.phase("build params")

        params = {
            "variables": orjson.dumps(variables).decode(),
            "features": orjson.dumps(features or FEATURES).decode(),
        }
        if field_toggles:
            params["fieldToggles"] = orjson.dumps(field_toggles).decode()

        if rd:
            rd.phase("build headers")

        headers = self._get_headers(url)
        cookies = self._get_cookies()

        if rd:
            rd.phase("network request")

        start_time = time.perf_counter()

        response = self.session.get(
            url,
            params=params,
            headers=headers,
            cookies=cookies,
            timeout=15,
        )

        elapsed_ms = (time.perf_counter() - start_time) * 1000

        # Clear response cookies so X can't track/throttle across requests
        # TLS connection stays warm (cookies are HTTP-level, not TCP-level)
        self.session.cookies.clear()

        if rd:
            rd.phase("parse response")
            rd.status_code = response.status_code
            rd.response_size = len(response.content) if response.content else 0

        if debug or response.status_code != 200:
            print(f"  Status: {response.status_code}")
            print(f"  Response: {response.text[:500] if response.text else 'empty'}")

        response.raise_for_status()

        if self.token_set:
            self.token_set.request_count += 1

        data = orjson.loads(response.content)

        if rd:
            rd.end()

        return data, elapsed_ms

    def close(self):
        """Close the session."""
        if self._session:
            self._session.close()
            self._session = None


# ── Token Creation ──────────────────────────────────────────

def get_guest_token(browser: str = "chrome131", session: Optional[requests.Session] = None) -> Optional[str]:
    """Get a guest token from X API.
    If a session is provided, uses it (warms TLS as side effect). Otherwise creates a one-off request."""
    try:
        if session:
            response = session.post(
                GUEST_TOKEN_URL,
                headers={"authorization": f"Bearer {BEARER_TOKEN}"},
                timeout=10,
            )
        else:
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


def create_token_set(browser: Optional[str] = None, session: Optional[requests.Session] = None) -> Optional[TokenSet]:
    """Create a guest token set for X API requests.
    If a session is provided, uses it (warms TLS to api.x.com as side effect)."""
    browser = browser or random.choice(BROWSER_PROFILES)

    guest_token = get_guest_token(browser, session=session)
    if not guest_token:
        return None

    csrf_token = secrets.token_hex(16)

    return TokenSet(
        guest_token=guest_token,
        csrf_token=csrf_token,
        created_at=time.time(),
    )


def create_auth_token_set(
    auth_token: Optional[str] = None,
    ct0: Optional[str] = None,
) -> Optional[TokenSet]:
    """
    Create an authenticated token set using browser cookies.

    Args:
        auth_token: The auth_token cookie from a logged-in X session.
        ct0: The ct0 cookie from a logged-in X session.

    If not provided, reads from environment variables X_AUTH_TOKEN and X_CT0.
    """
    auth_token = auth_token or os.environ.get("X_AUTH_TOKEN")
    ct0 = ct0 or os.environ.get("X_CT0")

    if not auth_token or not ct0:
        return None

    return TokenSet(
        guest_token="",
        csrf_token="",
        created_at=time.time(),
        auth_token=auth_token,
        ct0=ct0,
    )


# ── Test ────────────────────────────────────────────────────

def test_connection() -> bool:
    """Test if we can connect to X API and get user data."""
    debugger = SpeedDebugger()

    # Init timing — run txn generator + token creation in parallel
    # create_token uses the client's session → warms TLS to api.x.com
    # txn generator fetches x.com → independent, runs in parallel
    init_rd = debugger.new_request("Initialization")
    init_rd.phase("parallel init (txn + token + TLS)")

    browser = random.choice(BROWSER_PROFILES)
    client = XClient(browser=browser)  # no token yet, just create session
    token_result = [None]

    def _create_token_thread():
        # Uses client's session → TLS handshake to api.x.com happens here
        # So first real API request reuses the warm connection
        token_result[0] = create_token_set(browser, session=client.session)
        # Clear cookies from token creation so they don't pollute API requests
        client.session.cookies.clear()

    t = threading.Thread(target=_create_token_thread, daemon=True)
    t.start()
    _txn_generator._ensure_initialized()  # runs concurrently with token creation
    t.join(timeout=15)

    token_set = token_result[0]
    init_rd.end()
    debugger.set_init_debug(init_rd)

    if not token_set:
        print("Failed to create token set")
        return False

    client.token_set = token_set

    # Cold request (TLS already warmed)
    rd1 = debugger.new_request("UserByScreenName (1st)")
    try:
        data, elapsed = client.graphql_request(
            query_id="-oaLodhGbbnzJBACb1kk2Q",
            operation_name="UserByScreenName",
            variables={"screen_name": "elonmusk", "withGrokTranslatedBio": False},
            field_toggles={"withPayments": False, "withAuxiliaryUserLabels": True},
            debug_obj=rd1,
        )
        user = data.get("data", {}).get("user", {}).get("result", {})
        core = user.get("core", {})
        legacy = user.get("legacy", {})
        print(f"\n  @{core.get('screen_name')} - {core.get('name')} | {legacy.get('followers_count'):,} followers")
    except Exception as e:
        print(f"Error: {e}")
        return False

    # Warm request
    rd2 = debugger.new_request("UserByScreenName (warm)")
    try:
        client.graphql_request(
            query_id="-oaLodhGbbnzJBACb1kk2Q",
            operation_name="UserByScreenName",
            variables={"screen_name": "jack", "withGrokTranslatedBio": False},
            field_toggles={"withPayments": False, "withAuxiliaryUserLabels": True},
            debug_obj=rd2,
        )
    except Exception as e:
        print(f"Error: {e}")

    # Warm request 2
    rd3 = debugger.new_request("UserByRestId (warm)")
    try:
        client.graphql_request(
            query_id="Bbaot8ySMtJD7K2t01gW7A",
            operation_name="UserByRestId",
            variables={"userId": "44196397", "withGrokTranslatedBio": False},
            field_toggles={"withPayments": False, "withAuxiliaryUserLabels": True},
            debug_obj=rd3,
        )
    except Exception as e:
        print(f"Error: {e}")

    debugger.print_summary()
    return True


if __name__ == "__main__":
    test_connection()
