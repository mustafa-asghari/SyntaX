"""
SyntaX Authenticated Account Pool
Manages a pool of logged-in X accounts for auth-gated endpoints only.

Guest tokens handle: UserByScreenName, UserByRestId, TweetResultByRestId, UserTweets
Auth accounts handle: SearchTimeline, TweetDetail, Followers, Following, Mentions

Each account is pinned to a proxy IP so X can't correlate them.
"""

import json
import time
import threading
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, List, Dict, Any

from curl_cffi import requests as curl_requests

from config import TOKEN_CONFIG

# TLS pre-warm target
_PREWARM_URL = "https://api.x.com/"
_PREWARM_TIMEOUT = (3, 2)
import os as _os
_MAX_SESSIONS_PER_ACCOUNT = int(_os.environ.get("MAX_SESSIONS_PER_ACCOUNT", "10"))


@dataclass
class Account:
    """A single authenticated X account."""
    auth_token: str
    ct0: str
    label: str = ""  # friendly name (e.g. "account-1")
    proxy: Optional[str] = None  # proxy URL pinned to this account
    request_count: int = 0
    last_used: float = 0.0
    rate_limited_until: float = 0.0  # timestamp when 429 cooldown expires
    failures: int = 0

    def __post_init__(self):
        self._sessions: deque = deque()
        self._session_lock = threading.Lock()

    @property
    def is_available(self) -> bool:
        """Check if account is usable (not rate-limited)."""
        if self.rate_limited_until > time.time():
            return False
        return True

    @property
    def proxy_dict(self) -> Optional[Dict[str, str]]:
        """Return proxy in curl_cffi format."""
        if not self.proxy:
            return None
        return {"http": self.proxy, "https": self.proxy}

    def _create_warm_session(self) -> curl_requests.Session:
        """Create a new session and pre-warm TLS to api.x.com."""
        session = curl_requests.Session(impersonate="chrome131")
        if self.proxy:
            session.proxies = self.proxy_dict
        try:
            session.head(_PREWARM_URL, headers={"User-Agent": "Mozilla/5.0"},
                         timeout=_PREWARM_TIMEOUT)
            session.cookies.clear()
        except Exception:
            pass  # best-effort
        return session

    def acquire_session(self) -> curl_requests.Session:
        """Pop a warm session from the pool, or create a plain one.

        On-demand sessions skip the HEAD prewarm — the TLS handshake
        will happen on the real API request anyway, so an extra HEAD
        would only add latency.
        """
        with self._session_lock:
            if self._sessions:
                session = self._sessions.popleft()
                session.cookies.clear()
                return session
        # Pool empty — plain session (no HEAD), TLS handshakes on first real request
        session = curl_requests.Session(impersonate="chrome131")
        if self.proxy:
            session.proxies = self.proxy_dict
        return session

    def release_session(self, session: curl_requests.Session) -> None:
        """Return a session to the pool (or close it if pool is full)."""
        session.cookies.clear()
        with self._session_lock:
            if len(self._sessions) < _MAX_SESSIONS_PER_ACCOUNT:
                self._sessions.append(session)
                return
        session.close()


class AccountPool:
    """
    Thread-safe pool of authenticated X accounts.

    Rotates across accounts round-robin, skipping any that are
    rate-limited (429). Each account is pinned to its own proxy IP.

    Usage:
        pool = AccountPool.from_file("accounts.json")
        account = pool.acquire()
        # ... make request ...
        pool.release(account, success=True)
    """

    def __init__(self, accounts: Optional[List[Account]] = None):
        self._accounts: List[Account] = accounts or []
        self._index = 0
        self._lock = threading.Lock()

    @classmethod
    def from_file(cls, path: str) -> "AccountPool":
        """Load accounts from a JSON file.

        Expected format:
        [
            {
                "auth_token": "abc123...",
                "ct0": "def456...",
                "label": "account-1",
                "proxy": "http://user:pass@proxy1:8080"
            },
            ...
        ]
        """
        p = Path(path)
        if not p.exists():
            print(f"[AccountPool] No accounts file at {path}")
            return cls()

        with open(p) as f:
            data = json.load(f)

        accounts = []
        for entry in data:
            accounts.append(Account(
                auth_token=entry["auth_token"],
                ct0=entry["ct0"],
                label=entry.get("label", f"account-{len(accounts)+1}"),
                proxy=entry.get("proxy"),
            ))

        print(f"[AccountPool] Loaded {len(accounts)} accounts")
        return cls(accounts)

    @classmethod
    def from_json_string(cls, raw: str) -> "AccountPool":
        """Load accounts from a JSON string (e.g. from an env var)."""
        data = json.loads(raw)
        accounts = []
        for entry in data:
            accounts.append(Account(
                auth_token=entry["auth_token"],
                ct0=entry["ct0"],
                label=entry.get("label", f"account-{len(accounts)+1}"),
                proxy=entry.get("proxy"),
            ))
        print(f"[AccountPool] Loaded {len(accounts)} accounts from env")
        return cls(accounts)

    @classmethod
    def from_env(cls) -> "AccountPool":
        """Load accounts from environment or default file paths.

        Priority:
        1. ACCOUNTS_JSON env var  — raw JSON string (for cloud deploys)
        2. ACCOUNTS_FILE env var  — path to a JSON file
        3. Default file locations — accounts.json in project root or scraper/src
        """
        import os

        # 1. Raw JSON in env var (Railway, Render, Fly, etc.)
        raw = os.environ.get("ACCOUNTS_JSON", "")
        if raw:
            return cls.from_json_string(raw)

        # 2. Check for accounts file path in env
        path = os.environ.get("ACCOUNTS_FILE", "")
        if path and Path(path).exists():
            return cls.from_file(path)

        # 3. Try default locations
        for default in [
            Path(__file__).resolve().parent.parent.parent / "accounts.json",
            Path(__file__).resolve().parent / "accounts.json",
        ]:
            if default.exists():
                return cls.from_file(str(default))

        return cls()

    def prewarm_all(self, sessions_per_account: int = 2) -> None:
        """Pre-warm TLS sessions for all accounts (call on startup)."""
        for account in self._accounts:
            for _ in range(sessions_per_account):
                session = account._create_warm_session()
                account.release_session(session)
        if self._accounts:
            print(f"[AccountPool] Pre-warmed {sessions_per_account} sessions "
                  f"for {len(self._accounts)} accounts")

    @property
    def has_accounts(self) -> bool:
        return len(self._accounts) > 0

    @property
    def count(self) -> int:
        return len(self._accounts)

    @property
    def available_count(self) -> int:
        with self._lock:
            return sum(1 for a in self._accounts if a.is_available)

    def acquire(self) -> Optional[Account]:
        """Get the next available account (round-robin, skip rate-limited).

        Returns None if no accounts are available.
        """
        with self._lock:
            if not self._accounts:
                return None

            # Try each account once (round-robin)
            for _ in range(len(self._accounts)):
                account = self._accounts[self._index % len(self._accounts)]
                self._index += 1

                if account.is_available:
                    account.last_used = time.time()
                    return account

            return None  # All accounts rate-limited

    def release(self, account: Account, success: bool = True,
                status_code: int = 200) -> None:
        """Return an account after use. Handles rate limiting and failures."""
        with self._lock:
            account.request_count += 1

            if success:
                account.failures = 0
            else:
                account.failures += 1

                if status_code == 429:
                    # Rate limited — cooldown 15 minutes
                    account.rate_limited_until = time.time() + 900
                    print(f"[AccountPool] {account.label} rate-limited, "
                          f"cooldown until {time.strftime('%H:%M:%S', time.localtime(account.rate_limited_until))}")
                elif status_code == 403:
                    # Forbidden — longer cooldown (might be suspended)
                    account.rate_limited_until = time.time() + 3600
                    print(f"[AccountPool] {account.label} got 403, "
                          f"1hr cooldown")

    def stats(self) -> Dict[str, Any]:
        """Get pool statistics."""
        with self._lock:
            now = time.time()
            return {
                "total": len(self._accounts),
                "available": sum(1 for a in self._accounts if a.is_available),
                "rate_limited": sum(1 for a in self._accounts if a.rate_limited_until > now),
                "total_requests": sum(a.request_count for a in self._accounts),
                "accounts": [
                    {
                        "label": a.label,
                        "requests": a.request_count,
                        "available": a.is_available,
                        "has_proxy": bool(a.proxy),
                    }
                    for a in self._accounts
                ],
            }


# ── Singleton ──────────────────────────────────────────────

_account_pool: Optional[AccountPool] = None


def get_account_pool() -> AccountPool:
    """Get the global account pool instance."""
    global _account_pool
    if _account_pool is None:
        _account_pool = AccountPool.from_env()
    return _account_pool
