"""
SyntaX HTTP Client
High-performance HTTP client using curl-cffi with Chrome TLS fingerprinting.
Includes x-client-transaction-id generation for X's anti-bot bypass.

Speed optimizations:
- Module-level session pool (reuse TLS connections)
- Background TransactionIDGenerator init (no cold-start penalty)
- Pre-built header templates (avoid dict rebuilds)
- Session pre-warming (TLS handshake on init, not first request)
- HTTP/2 multiplexing for parallel requests
- Aggressive timeouts for fail-fast behavior
- __slots__ on hot dataclasses for faster attribute access
- Reduced lock contention with RLock
- Batch parallel request support via thread pool
"""

import os
import secrets
import random
import time
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Optional, Dict, Any, Tuple, List, Callable
from dataclasses import dataclass, field
from urllib.parse import urlparse
from functools import lru_cache

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
    TOKEN_CONFIG,
)

from debug import RequestDebug, SpeedDebugger

# ── Constants ───────────────────────────────────────────────
# Aggressive but safe timeouts (X API typically responds in <500ms)
_CONNECT_TIMEOUT = 5
_READ_TIMEOUT = 8
_DEFAULT_TIMEOUT = (_CONNECT_TIMEOUT, _READ_TIMEOUT)

# Thread pool for parallel requests (reused across all clients)
_PARALLEL_EXECUTOR: Optional[ThreadPoolExecutor] = None
_PARALLEL_EXECUTOR_LOCK = threading.Lock()

def _get_executor(max_workers: int = 10) -> ThreadPoolExecutor:
    """Get or create the shared thread pool executor."""
    global _PARALLEL_EXECUTOR
    if _PARALLEL_EXECUTOR is None:
        with _PARALLEL_EXECUTOR_LOCK:
            if _PARALLEL_EXECUTOR is None:
                _PARALLEL_EXECUTOR = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="syntax_req")
    return _PARALLEL_EXECUTOR


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
    # Full cookie jar collected from visiting x.com — includes guest_id,
    # guest_id_marketing, guest_id_ads, personalization_id, __cf_bm, etc.
    # These are required for SearchTimeline and TweetDetail with guest tokens.
    session_cookies: Optional[Dict[str, str]] = None

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
            "session_cookies": self.session_cookies or {},
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "TokenSet":
        return cls(
            guest_token=data["guest_token"],
            csrf_token=data["csrf_token"],
            created_at=float(data["created_at"]),
            cf_cookie=data.get("cf_cookie") or None,
            request_count=int(data.get("request_count", 0)),
            session_cookies=data.get("session_cookies") or None,
        )




# ── Transaction ID Generator ────────────────────────────────

class TransactionIDGenerator:
    """
    Generates valid x-client-transaction-id headers.
    Initialized eagerly in a background thread to avoid cold-start penalty.
    TTL: 2 hours (X rotates keys slowly).

    Also captures session cookies from the x.com visit so create_token_set
    doesn't need a second trip — saves ~400-600ms per token creation.
    """
    __slots__ = ('_ct', '_initialized_at', '_ttl', '_lock', '_ready',
                 '_session_cookies')

    def __init__(self):
        self._ct: Optional[ClientTransaction] = None
        self._initialized_at: float = 0
        self._ttl: int = 7200  # 2 hours
        self._lock = threading.RLock()
        self._ready = threading.Event()
        self._session_cookies: Dict[str, str] = {}

    def start_background_init(self):
        """Start initialization in background thread."""
        t = threading.Thread(target=self._init_sync, daemon=True)
        t.start()

    def _init_sync(self, browser: str = "chrome131"):
        """Initialize the transaction generator (blocking).
        Captures session cookies from the x.com visit as a side effect."""
        try:
            session = requests.Session(impersonate=browser)
            try:
                home = session.get(X_HOME_URL, timeout=_DEFAULT_TIMEOUT)
                # Capture cookies from x.com visit (reused by create_token_set)
                cookies = {name: value for name, value in session.cookies.items()}
                soup = bs4.BeautifulSoup(home.content, "lxml")
                ondemand_url = get_ondemand_file_url(soup)
                ondemand_js = session.get(ondemand_url, timeout=(_CONNECT_TIMEOUT, 15)).text
            finally:
                session.close()

            with self._lock:
                self._ct = ClientTransaction(soup, ondemand_js)
                self._initialized_at = time.time()
                self._session_cookies = cookies
            self._ready.set()
        except Exception as e:
            print(f"[TxnGen] Init error: {e}")
            self._ready.set()

    def _ensure_initialized(self):
        """Ensure generator is ready, re-init if TTL expired."""
        if self._ct is not None and (time.time() - self._initialized_at) < self._ttl:
            return

        if not self._ready.is_set():
            self._ready.wait(timeout=20)
            if self._ct is not None:
                return

        self._init_sync()

    def get_session_cookies(self) -> Dict[str, str]:
        """Get cached session cookies from the x.com visit.
        Avoids a second x.com round-trip during token creation."""
        self._ensure_initialized()
        with self._lock:
            return dict(self._session_cookies)

    def generate(self, method: str, path: str) -> str:
        """Generate a transaction ID for the given method and path."""
        self._ensure_initialized()
        with self._lock:
            return self._ct.generate_transaction_id(method=method, path=path)


_txn_generator = TransactionIDGenerator()
# Start background init immediately on module import
_txn_generator.start_background_init()


# ── Pre-built Header Templates ──────────────────────────────
# Frozen dicts for minimal copy overhead (tuple items for faster iteration)

_HEADER_TEMPLATE_GUEST = {
    "authorization": f"Bearer {BEARER_TOKEN}",
    "x-twitter-active-user": "yes",
    "x-twitter-client-language": "en",
    "x-twitter-client": "Guest",
    "content-type": "application/json",
    "accept": "*/*",
    "accept-language": "en-US,en;q=0.9",
    "origin": "https://x.com",
    "referer": "https://x.com/",
    "sec-fetch-dest": "empty",
    "sec-fetch-mode": "cors",
    "sec-fetch-site": "same-site",
    "connection": "keep-alive",  # Explicit keep-alive
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
    "connection": "keep-alive",  # Explicit keep-alive
}

# Pre-serialized features/field_toggles (computed once at import time)
_FEATURES_JSON = orjson.dumps(FEATURES).decode()
_FIELD_TOGGLES_JSON = orjson.dumps(FIELD_TOGGLES).decode()


# ── XClient ─────────────────────────────────────────────────

class XClient:
    """
    High-performance X API client.

    Uses curl-cffi for Chrome TLS fingerprint, session pooling for
    connection reuse, and pre-built headers for minimal per-request overhead.
    
    Features for maximum speed:
    - HTTP/2 multiplexing (curl_cffi default)
    - Aggressive timeouts
    - Pre-built headers and cached JSON
    - Batch parallel request support
    - Proxy support for distributed scraping
    """
    __slots__ = ('token_set', '_browser', '_session', '_headers_cache', '_proxy',
                 '_token_pool_ref', '_external_session')

    def __init__(
        self,
        token_set: Optional[TokenSet] = None,
        browser: str = "chrome131",
        proxy: Optional[Dict[str, str]] = None,
        token_pool_ref=None,
        session: Optional[requests.Session] = None,
    ):
        """
        Initialize XClient.

        Args:
            token_set: Token credentials for authentication
            browser: Browser profile to impersonate
            proxy: Proxy dict {"http": "url", "https": "url"} for all requests
            token_pool_ref: Optional token pool — when set, auto-rotates to a
                fresh token when the current one expires or hits request limit
            session: Optional pre-warmed curl-cffi session. If provided, the
                caller owns the lifecycle — close() becomes a no-op for it.
        """
        self.token_set = token_set
        self._browser = browser
        self._session = session
        self._external_session = session is not None
        self._headers_cache: Optional[Dict[str, str]] = None
        self._proxy = proxy
        self._token_pool_ref = token_pool_ref

    @property
    def session(self) -> requests.Session:
        """Lazy-init session. One session per client = clean cookies + connection reuse."""
        if self._session is None:
            self._session = requests.Session(impersonate=self._browser)
            # Set proxy on session if configured
            if self._proxy:
                self._session.proxies = self._proxy
        return self._session

    def prewarm_connection(self) -> None:
        """Pre-warm TLS connection to api.x.com.
        Call this during initialization to avoid cold-start latency on first request.
        Uses a lightweight HEAD request that X's API accepts.
        """
        try:
            # HEAD request to establish TLS without fetching data
            self.session.head(
                "https://api.x.com/",
                headers={"User-Agent": "Mozilla/5.0"},
                timeout=(_CONNECT_TIMEOUT, 2),  # Short read timeout for HEAD
            )
            self.session.cookies.clear()
        except Exception:
            pass  # Swallow errors - pre-warming is best-effort

    def _get_headers(self, url: str, method: str = "GET") -> Dict[str, str]:
        """Build headers with transaction ID. Uses pre-built template."""
        if not self.token_set:
            raise ValueError("No token set configured")

        # Extract path once (urlparse is relatively expensive)
        path = urlparse(url).path
        txn_id = _txn_generator.generate(method=method, path=path)

        # Use dict literal copy for speed (faster than .copy())
        if self.token_set.is_authenticated:
            headers = {**_HEADER_TEMPLATE_AUTH, "x-csrf-token": self.token_set.ct0}
        else:
            headers = {
                **_HEADER_TEMPLATE_GUEST,
                "x-guest-token": self.token_set.guest_token,
                "x-csrf-token": self.token_set.csrf_token,
            }

        headers["x-client-transaction-id"] = txn_id
        return headers

    def _get_cookies(self) -> Dict[str, str]:
        """Build cookies for X API request.

        For guest tokens, merges the full session cookie jar (collected
        from visiting x.com during token creation) with the guest/csrf
        tokens.  This is required for SearchTimeline and TweetDetail.
        """
        if not self.token_set:
            raise ValueError("No token set configured")

        if self.token_set.is_authenticated:
            return {
                "auth_token": self.token_set.auth_token,
                "ct0": self.token_set.ct0,
            }

        # Start with session cookies from x.com (guest_id_marketing,
        # guest_id_ads, personalization_id, __cf_bm, etc.)
        cookies: Dict[str, str] = {}
        if self.token_set.session_cookies:
            cookies.update(self.token_set.session_cookies)

        # Overlay the guest-specific tokens
        cookies.update({
            "guest_id": f"v1%3A{self.token_set.guest_token}",
            "gt": self.token_set.guest_token,
            "ct0": self.token_set.csrf_token,
        })

        if self.token_set.cf_cookie:
            cookies["__cf_bm"] = self.token_set.cf_cookie

        return cookies

    def _check_token_health(self) -> None:
        """Check token age and request count; auto-rotate from pool if expired."""
        if not self.token_set:
            return
        age = time.time() - self.token_set.created_at
        expired = age > TOKEN_CONFIG["guest_token_ttl"]
        exhausted = self.token_set.request_count >= TOKEN_CONFIG["max_requests_per_token"]

        if (expired or exhausted) and self._token_pool_ref is not None:
            # Return old token (if not expired) and grab a fresh one
            if not expired:
                self._token_pool_ref.return_token(self.token_set, success=True)
            new_token = self._token_pool_ref.get_token()
            if new_token:
                self.token_set = new_token
                # Reset session cookies for the new token
                if self._session:
                    self._session.cookies.clear()
            else:
                # Pool empty — create one on-demand
                from config import BROWSER_PROFILES
                fresh = create_token_set(browser=random.choice(BROWSER_PROFILES), proxy=self._proxy)
                if fresh:
                    self.token_set = fresh
                    if self._session:
                        self._session.cookies.clear()

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
        # Pre-request: rotate token if expired/exhausted (no delay)
        self._check_token_health()

        rd = debug_obj

        url = f"{GRAPHQL_BASE_URL}/{query_id}/{operation_name}"

        if rd:
            rd.phase("build params")

        # Use pre-serialized features when using defaults (common case)
        if features is None:
            features_json = _FEATURES_JSON
        else:
            features_json = orjson.dumps(features).decode()

        params = {
            "variables": orjson.dumps(variables).decode(),
            "features": features_json,
        }
        if field_toggles:
            # Check if it's the default field_toggles
            if field_toggles is FIELD_TOGGLES:
                params["fieldToggles"] = _FIELD_TOGGLES_JSON
            else:
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
            timeout=_DEFAULT_TIMEOUT,  # Use aggressive timeout
        )

        elapsed_ms = (time.perf_counter() - start_time) * 1000

        # Clear response cookies so X can't track/throttle across requests
        # TLS connection stays warm (cookies are HTTP-level, not TCP-level)
        self.session.cookies.clear()

        if rd:
            rd.phase("parse response")
            rd.status_code = response.status_code
            rd.response_size = len(response.content) if response.content else 0

        if debug and response.status_code != 200:
            print(f"  Status: {response.status_code}")
            print(f"  Response: {response.text[:500] if response.text else 'empty'}")

        response.raise_for_status()

        if self.token_set:
            self.token_set.request_count += 1

        # orjson.loads is already fast, but we ensure we use bytes directly
        data = orjson.loads(response.content)

        if rd:
            rd.end()

        return data, elapsed_ms

    def graphql_request_batch(
        self,
        requests_data: List[Dict[str, Any]],
        max_workers: int = 5,
    ) -> List[Tuple[Dict[str, Any], float]]:
        """
        Execute multiple GraphQL requests in parallel.
        
        Args:
            requests_data: List of request configs, each with:
                - query_id: str
                - operation_name: str
                - variables: Dict
                - features: Optional[Dict]
                - field_toggles: Optional[Dict]
            max_workers: Max concurrent requests (default 5 to avoid rate limits)
            
        Returns:
            List of (response_data, elapsed_ms) in same order as input.
        """
        def _do_request(req: Dict[str, Any]) -> Tuple[Dict[str, Any], float]:
            return self.graphql_request(
                query_id=req["query_id"],
                operation_name=req["operation_name"],
                variables=req["variables"],
                features=req.get("features"),
                field_toggles=req.get("field_toggles"),
            )

        executor = _get_executor(max_workers)
        futures = [executor.submit(_do_request, req) for req in requests_data]
        
        results = []
        for future in futures:
            try:
                results.append(future.result())
            except Exception as e:
                results.append(({"error": str(e)}, 0.0))
        
        return results

    def close(self):
        """Close the session. No-op if session was externally provided."""
        if self._session and not self._external_session:
            self._session.close()
        self._session = None


# ── Token Creation ──────────────────────────────────────────

# Pre-built header for guest token (never changes)
_GUEST_TOKEN_HEADER = {"authorization": f"Bearer {BEARER_TOKEN}"}

def get_guest_token(
    browser: str = "chrome131",
    session: Optional[requests.Session] = None,
    proxy: Optional[Dict[str, str]] = None,
) -> Optional[str]:
    """Get a guest token from X API.
    
    Args:
        browser: Browser profile to impersonate
        session: Optional session for connection reuse
        proxy: Optional proxy dict {"http": "url", "https": "url"}
        
    If a session is provided, uses it (warms TLS as side effect).
    Otherwise creates a one-off request.
    
    IMPORTANT: When scaling, generate each token from a DIFFERENT proxy IP.
    Tokens generated from the same IP share the same "trust pool" and will
    be rate-limited together.
    """
    try:
        if session:
            response = session.post(
                GUEST_TOKEN_URL,
                headers=_GUEST_TOKEN_HEADER,
                timeout=_DEFAULT_TIMEOUT,
                proxies=proxy,
            )
        else:
            response = requests.post(
                GUEST_TOKEN_URL,
                headers=_GUEST_TOKEN_HEADER,
                impersonate=browser,
                timeout=_DEFAULT_TIMEOUT,
                proxies=proxy,
            )
        response.raise_for_status()
        data = orjson.loads(response.content)
        return data.get("guest_token")
    except Exception as e:
        print(f"Error getting guest token: {e}")
        return None


def _collect_session_cookies(
    browser: str = "chrome131",
    session: Optional[requests.Session] = None,
    proxy: Optional[Dict[str, str]] = None,
) -> Dict[str, str]:
    """Visit x.com to collect the full guest session cookie jar.

    X sets cookies on the homepage that are required for certain
    endpoints (SearchTimeline, TweetDetail) to work with guest tokens:
    guest_id, guest_id_marketing, guest_id_ads, personalization_id,
    __cf_bm, etc.

    Returns a dict of cookie name → value.
    """
    try:
        if session:
            resp = session.get(
                X_HOME_URL,
                timeout=_DEFAULT_TIMEOUT,
                proxies=proxy,
            )
        else:
            resp = requests.get(
                X_HOME_URL,
                impersonate=browser,
                timeout=_DEFAULT_TIMEOUT,
                proxies=proxy,
            )

        # Extract cookies from the response
        cookies = {}
        if session:
            for name, value in session.cookies.items():
                cookies[name] = value
        else:
            for name, value in resp.cookies.items():
                cookies[name] = value
        return cookies
    except Exception as e:
        print(f"Warning: could not collect session cookies: {e}")
        return {}


def create_token_set(
    browser: Optional[str] = None,
    session: Optional[requests.Session] = None,
    proxy: Optional[Dict[str, str]] = None,
) -> Optional[TokenSet]:
    """Create a full guest session for X API requests.

    Uses cached session cookies from the txn generator's x.com visit
    (eliminates a ~400-600ms round-trip). Only the guest token activation
    requires a network call (~200-300ms).

    Args:
        browser: Browser profile to impersonate
        session: Optional session for connection reuse
        proxy: Optional proxy for this token generation
    """
    browser = browser or random.choice(BROWSER_PROFILES)

    # Reuse cookies cached by the txn generator (it already visited x.com)
    session_cookies = _txn_generator.get_session_cookies()

    # If txn generator had no cookies (init failed), fall back to direct fetch
    if not session_cookies:
        session_cookies = _collect_session_cookies(browser, session=session, proxy=proxy)
        if session:
            session.cookies.clear()

    # Get guest token (the only network call needed)
    guest_token = get_guest_token(browser, session=session, proxy=proxy)
    if not guest_token:
        return None

    csrf_token = secrets.token_hex(16)
    cf_cookie = session_cookies.get("__cf_bm")

    return TokenSet(
        guest_token=guest_token,
        csrf_token=csrf_token,
        created_at=time.time(),
        cf_cookie=cf_cookie,
        session_cookies=session_cookies,
    )


def token_set_from_account(account) -> TokenSet:
    """Create an authenticated TokenSet from an Account object.

    Used for auth-gated endpoints (Search, TweetDetail, Followers, etc.).
    The Account comes from AccountPool.
    """
    return TokenSet(
        guest_token="",
        csrf_token="",
        created_at=time.time(),
        auth_token=account.auth_token,
        ct0=account.ct0,
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
