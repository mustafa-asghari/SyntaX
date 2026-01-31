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
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, List, Dict, Any

from config import TOKEN_CONFIG


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
    def from_env(cls) -> "AccountPool":
        """Load accounts from environment or default file paths."""
        import os

        # Check for accounts file path in env
        path = os.environ.get("ACCOUNTS_FILE", "")
        if path and Path(path).exists():
            return cls.from_file(path)

        # Try default locations
        for default in [
            Path(__file__).resolve().parent.parent.parent / "accounts.json",
            Path(__file__).resolve().parent / "accounts.json",
        ]:
            if default.exists():
                return cls.from_file(str(default))

        return cls()

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
