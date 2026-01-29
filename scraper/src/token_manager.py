"""
SyntaX Token Manager
Background service that continuously generates and maintains the token pool.
"""

import time
import signal
import sys

from .client import create_token_set
from .token_pool import TokenPool, get_pool
from .proxy_manager import get_proxy_manager
from .config import TOKEN_CONFIG


class TokenManager:
    """
    Background service that maintains the token pool.

    Runs continuously, generating new tokens to keep the pool
    at the target size. Tokens are pre-warmed so API requests
    never have to wait for token generation.
    """

    def __init__(self):
        self.pool = get_pool()
        self._proxy_manager = get_proxy_manager()
        self.running = False
        self.stats = {
            "tokens_created": 0,
            "tokens_failed": 0,
            "start_time": None,
        }

    def start(self):
        """Start the token manager."""
        self.running = True
        self.stats["start_time"] = time.time()

        # Handle shutdown signals
        signal.signal(signal.SIGINT, self._shutdown)
        signal.signal(signal.SIGTERM, self._shutdown)

        print(f"Token Manager started")
        print(f"  Target pool size: {TOKEN_CONFIG['pool_target_size']}")
        print(f"  Generation interval: {TOKEN_CONFIG['generation_interval']}s")
        print(f"  CF cookie TTL: {TOKEN_CONFIG['cf_cookie_ttl']}s")
        print()

        # Initial fill
        self._fill_pool()

        # Main loop
        while self.running:
            try:
                self._maintenance_cycle()
                time.sleep(TOKEN_CONFIG["generation_interval"])
            except Exception as e:
                print(f"Error in maintenance cycle: {e}")
                time.sleep(1)

    def _fill_pool(self):
        """Fill pool to target size."""
        current = self.pool.pool_size()
        target = TOKEN_CONFIG["pool_target_size"]

        if current >= target:
            return

        print(f"Filling pool: {current}/{target}")

        while self.pool.pool_size() < target and self.running:
            proxy_cfg = self._proxy_manager.get_proxy()
            proxy = proxy_cfg.to_curl_cffi_format() if proxy_cfg else None
            token_set = create_token_set(proxy=proxy)
            if proxy_cfg:
                self._proxy_manager.report_result(proxy_cfg, success=token_set is not None)
            if token_set:
                self.pool.add_token(token_set)
                self.stats["tokens_created"] += 1
                print(f"  Created token ({self.pool.pool_size()}/{target})")
            else:
                self.stats["tokens_failed"] += 1
                print(f"  Failed to create token")
                time.sleep(1)  # Back off on failure

    def _maintenance_cycle(self):
        """Run one maintenance cycle."""
        stats = self.pool.pool_stats()
        current_size = stats["size"]
        min_size = TOKEN_CONFIG["pool_min_size"]
        target_size = TOKEN_CONFIG["pool_target_size"]

        # Check if we need more tokens
        if current_size < min_size:
            print(f"Pool low ({current_size}/{min_size}), generating tokens...")
            self._fill_pool()
        elif current_size < target_size:
            # Generate one token per cycle to maintain pool
            proxy_cfg = self._proxy_manager.get_proxy()
            proxy = proxy_cfg.to_curl_cffi_format() if proxy_cfg else None
            token_set = create_token_set(proxy=proxy)
            if proxy_cfg:
                self._proxy_manager.report_result(proxy_cfg, success=token_set is not None)
            if token_set:
                self.pool.add_token(token_set)
                self.stats["tokens_created"] += 1

        # Log status periodically
        if self.stats["tokens_created"] % 10 == 0 and self.stats["tokens_created"] > 0:
            self._log_status()

    def _log_status(self):
        """Log current status."""
        stats = self.pool.pool_stats()
        uptime = time.time() - self.stats["start_time"]

        print(f"\n--- Token Manager Status ---")
        print(f"  Pool size: {stats['size']}")
        print(f"  Avg health: {stats['avg_health']:.2f}")
        print(f"  Tokens created: {self.stats['tokens_created']}")
        print(f"  Tokens failed: {self.stats['tokens_failed']}")
        print(f"  Uptime: {uptime/60:.1f} minutes")
        print()

    def _shutdown(self, signum, frame):
        """Handle shutdown signal."""
        print("\nShutting down Token Manager...")
        self.running = False
        self.pool.close()
        sys.exit(0)


def main():
    """Run the token manager."""
    manager = TokenManager()
    manager.start()


if __name__ == "__main__":
    main()
