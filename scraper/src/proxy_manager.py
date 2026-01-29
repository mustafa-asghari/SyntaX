"""
SyntaX Proxy Manager
Manages rotating proxies for high-volume scraping.

For business-scale scraping, you MUST use proxies to avoid IP bans.
Recommended providers: Bright Data, SmartProxy, Oxylabs (residential proxies).

Environment Variables:
    PROXY_LIST: Comma-separated list of proxies, or path to file
    PROXY_URL: Single proxy URL (for testing)
    PROXY_ROTATION: 'random' or 'round_robin' (default: random)
"""

import os
import random
import threading
from typing import Optional, List, Dict, Any
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(slots=True)
class ProxyConfig:
    """Proxy configuration."""
    url: str  # Full proxy URL (e.g., http://user:pass@host:port)
    protocol: str = "http"
    failures: int = 0
    successes: int = 0
    last_used: float = 0.0
    
    @property
    def health_score(self) -> float:
        """Calculate health score (0-1). Higher = healthier."""
        if self.successes + self.failures == 0:
            return 1.0
        return self.successes / (self.successes + self.failures)
    
    def to_curl_cffi_format(self) -> Dict[str, str]:
        """Return proxy dict for curl_cffi."""
        return {
            "http": self.url,
            "https": self.url,
        }


class ProxyManager:
    """
    Manages a pool of rotating proxies.
    
    Usage:
        pm = ProxyManager.from_env()
        proxy = pm.get_proxy()
        
        # Use proxy...
        
        pm.report_result(proxy, success=True)
    """
    
    def __init__(self, proxies: Optional[List[str]] = None, rotation: str = "random"):
        self._proxies: List[ProxyConfig] = []
        self._rotation = rotation
        self._index = 0
        self._lock = threading.Lock()
        
        if proxies:
            for p in proxies:
                self._proxies.append(ProxyConfig(url=p))
    
    @classmethod
    def from_env(cls) -> "ProxyManager":
        """
        Load proxies from environment.
        
        Supports:
            PROXY_URL: Single proxy
            PROXY_LIST: Comma-separated list or path to file
        """
        proxies = []
        
        # Single proxy
        single = os.environ.get("PROXY_URL")
        if single:
            proxies.append(single)
        
        # Proxy list (comma-separated or file path)
        proxy_list = os.environ.get("PROXY_LIST", "")
        if proxy_list:
            # Check if it's a file path
            path = Path(proxy_list)
            if path.exists() and path.is_file():
                with open(path) as f:
                    for line in f:
                        line = line.strip()
                        if line and not line.startswith("#"):
                            proxies.append(line)
            else:
                # Comma-separated
                proxies.extend([p.strip() for p in proxy_list.split(",") if p.strip()])
        
        rotation = os.environ.get("PROXY_ROTATION", "random")
        
        return cls(proxies=proxies, rotation=rotation)
    
    @property
    def has_proxies(self) -> bool:
        """Check if any proxies are configured."""
        return len(self._proxies) > 0
    
    @property
    def count(self) -> int:
        """Number of proxies in pool."""
        return len(self._proxies)
    
    def get_proxy(self) -> Optional[ProxyConfig]:
        """
        Get the next proxy to use.
        
        Returns None if no proxies configured.
        """
        if not self._proxies:
            return None
        
        with self._lock:
            if self._rotation == "round_robin":
                proxy = self._proxies[self._index % len(self._proxies)]
                self._index += 1
            elif self._rotation == "health":
                # Prefer healthier proxies
                sorted_proxies = sorted(self._proxies, key=lambda p: p.health_score, reverse=True)
                # Pick from top 30% with some randomness
                top_n = max(1, len(sorted_proxies) // 3)
                proxy = random.choice(sorted_proxies[:top_n])
            else:
                # Random
                proxy = random.choice(self._proxies)
            
            import time
            proxy.last_used = time.time()
            return proxy
    
    def report_result(self, proxy: ProxyConfig, success: bool) -> None:
        """Report success/failure for a proxy."""
        with self._lock:
            if success:
                proxy.successes += 1
            else:
                proxy.failures += 1
                
                # Remove proxy if it fails too much
                if proxy.failures > 10 and proxy.health_score < 0.3:
                    try:
                        self._proxies.remove(proxy)
                        print(f"[ProxyManager] Removed unhealthy proxy: {proxy.url[:30]}...")
                    except ValueError:
                        pass
    
    def add_proxy(self, url: str) -> None:
        """Add a new proxy to the pool."""
        with self._lock:
            self._proxies.append(ProxyConfig(url=url))
    
    def stats(self) -> Dict[str, Any]:
        """Get proxy pool statistics."""
        if not self._proxies:
            return {"count": 0, "avg_health": 0, "total_requests": 0}
        
        total_success = sum(p.successes for p in self._proxies)
        total_fail = sum(p.failures for p in self._proxies)
        
        return {
            "count": len(self._proxies),
            "avg_health": sum(p.health_score for p in self._proxies) / len(self._proxies),
            "total_requests": total_success + total_fail,
            "success_rate": total_success / (total_success + total_fail) if (total_success + total_fail) > 0 else 0,
        }


# Global singleton
_proxy_manager: Optional[ProxyManager] = None


def get_proxy_manager() -> ProxyManager:
    """Get the global proxy manager instance."""
    global _proxy_manager
    if _proxy_manager is None:
        _proxy_manager = ProxyManager.from_env()
    return _proxy_manager


# ── Test ─────────────────────────────────────────────────────

def test_proxy_manager():
    """Test proxy manager."""
    print("\n=== Proxy Manager Test ===\n")
    
    # Test with fake proxies
    pm = ProxyManager(proxies=[
        "http://user1:pass1@proxy1.example.com:8080",
        "http://user2:pass2@proxy2.example.com:8080",
        "http://user3:pass3@proxy3.example.com:8080",
    ])
    
    print(f"Proxies loaded: {pm.count}")
    
    # Simulate usage
    for i in range(10):
        proxy = pm.get_proxy()
        if proxy:
            success = random.random() > 0.3  # 70% success rate
            pm.report_result(proxy, success)
            print(f"  Request {i+1}: {proxy.url[:30]}... -> {'✓' if success else '✗'}")
    
    print(f"\nStats: {pm.stats()}")


if __name__ == "__main__":
    test_proxy_manager()
