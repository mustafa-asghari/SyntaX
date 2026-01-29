"""
SyntaX Industrial Token Generator
High-volume guest token generation with proxy rotation.

This is the KEY to scaling guest-only scraping:
- Each token is generated from a different IP (via proxy)
- Tokens from different IPs have INDEPENDENT rate limits
- Generate 100s of tokens → Each gets ~50-100 requests → 5,000-10,000 requests total

Usage:
    export PROXY_LIST="http://user:pass@proxy1:8080,http://user:pass@proxy2:8080,..."
    python token_generator.py --count 100 --output tokens.json
"""

import os
import time
import json
import random
import argparse
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Optional, List, Dict, Any, Tuple
from dataclasses import asdict

from client import TokenSet, create_token_set, _txn_generator
from config import BROWSER_PROFILES
from proxy_manager import ProxyManager, get_proxy_manager
from debug import C


def generate_token_with_proxy(
    proxy_manager: ProxyManager,
    browser: Optional[str] = None,
) -> Tuple[Optional[TokenSet], Optional[str], float]:
    """
    Generate a single token using a proxy.
    
    Returns:
        Tuple of (TokenSet or None, proxy_url used, generation_time_ms)
    """
    browser = browser or random.choice(BROWSER_PROFILES)
    
    # Get a proxy
    proxy_config = proxy_manager.get_proxy()
    proxy_dict = proxy_config.to_curl_cffi_format() if proxy_config else None
    proxy_url = proxy_config.url if proxy_config else "direct"
    
    start = time.perf_counter()
    
    try:
        token_set = create_token_set(browser=browser, proxy=proxy_dict)
        elapsed = (time.perf_counter() - start) * 1000
        
        if proxy_config:
            proxy_manager.report_result(proxy_config, success=token_set is not None)
        
        return token_set, proxy_url, elapsed
        
    except Exception as e:
        elapsed = (time.perf_counter() - start) * 1000
        
        if proxy_config:
            proxy_manager.report_result(proxy_config, success=False)
        
        print(f"  {C.RED}✗{C.RESET} Token generation failed: {e}")
        return None, proxy_url, elapsed


def generate_tokens_batch(
    count: int,
    proxy_manager: Optional[ProxyManager] = None,
    max_workers: int = 20,
    progress_callback: Optional[callable] = None,
) -> List[TokenSet]:
    """
    Generate multiple tokens in parallel using different proxies.
    
    Each token will be generated from a different IP (if enough proxies).
    
    Args:
        count: Number of tokens to generate
        proxy_manager: Proxy manager to use (uses global if None)
        max_workers: Max parallel token generations
        progress_callback: Optional callback(completed, total) for progress
        
    Returns:
        List of successfully generated TokenSets
    """
    pm = proxy_manager or get_proxy_manager()
    
    # Ensure txn generator is ready
    _txn_generator._ensure_initialized()
    
    tokens = []
    completed = 0
    lock = threading.Lock()
    
    def _generate_one(idx: int) -> Optional[TokenSet]:
        nonlocal completed
        
        browser = BROWSER_PROFILES[idx % len(BROWSER_PROFILES)]
        ts, proxy, ms = generate_token_with_proxy(pm, browser)
        
        with lock:
            completed += 1
            if progress_callback:
                progress_callback(completed, count)
        
        return ts
    
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = [executor.submit(_generate_one, i) for i in range(count)]
        
        for future in as_completed(futures):
            try:
                ts = future.result()
                if ts:
                    tokens.append(ts)
            except Exception as e:
                print(f"  {C.RED}Generation error: {e}{C.RESET}")
    
    return tokens


def save_tokens(tokens: List[TokenSet], output_path: str) -> None:
    """Save tokens to JSON file."""
    data = [
        {
            "guest_token": t.guest_token,
            "csrf_token": t.csrf_token,
            "created_at": t.created_at,
        }
        for t in tokens
    ]
    
    with open(output_path, "w") as f:
        json.dump(data, f, indent=2)


def load_tokens(input_path: str) -> List[TokenSet]:
    """Load tokens from JSON file."""
    with open(input_path) as f:
        data = json.load(f)
    
    return [
        TokenSet(
            guest_token=t["guest_token"],
            csrf_token=t["csrf_token"],
            created_at=t["created_at"],
        )
        for t in data
    ]


def main():
    parser = argparse.ArgumentParser(description="Generate guest tokens at scale")
    parser.add_argument("--count", "-c", type=int, default=10, help="Number of tokens to generate")
    parser.add_argument("--output", "-o", type=str, default=None, help="Output file (JSON)")
    parser.add_argument("--workers", "-w", type=int, default=20, help="Max parallel workers")
    parser.add_argument("--redis", "-r", action="store_true", help="Store in Redis pool")
    args = parser.parse_args()
    
    print(f"\n{'═' * 60}")
    print(f"  {C.BOLD}{C.CYAN}SyntaX Industrial Token Generator{C.RESET}")
    print(f"{'═' * 60}\n")
    
    # Check proxies
    pm = get_proxy_manager()
    if pm.has_proxies:
        print(f"  {C.GREEN}✓{C.RESET} Proxies loaded: {pm.count}")
    else:
        print(f"  {C.YELLOW}⚠{C.RESET} No proxies configured (using direct connection)")
        print(f"  {C.DIM}  Set PROXY_LIST or PROXY_URL env var for scaling{C.RESET}")
    
    print(f"  Target: {args.count} tokens")
    print(f"  Workers: {args.workers}")
    print()
    
    # Progress callback
    def show_progress(done, total):
        pct = (done / total) * 100
        bar = "█" * int(pct / 5) + "░" * (20 - int(pct / 5))
        print(f"\r  [{bar}] {done}/{total} ({pct:.0f}%)", end="", flush=True)
    
    # Generate
    start = time.perf_counter()
    tokens = generate_tokens_batch(
        count=args.count,
        proxy_manager=pm,
        max_workers=args.workers,
        progress_callback=show_progress,
    )
    elapsed = time.perf_counter() - start
    
    print()  # Newline after progress bar
    print()
    
    # Results
    success_rate = (len(tokens) / args.count) * 100 if args.count > 0 else 0
    
    print(f"  {C.GREEN}✓{C.RESET} Generated: {len(tokens)}/{args.count} tokens ({success_rate:.0f}%)")
    print(f"  {C.DIM}Time: {elapsed:.1f}s ({elapsed/args.count*1000:.0f}ms per token){C.RESET}")
    
    if pm.has_proxies:
        stats = pm.stats()
        print(f"  {C.DIM}Proxy health: {stats['avg_health']*100:.0f}%{C.RESET}")
    
    # Save
    if args.output:
        save_tokens(tokens, args.output)
        print(f"\n  {C.GREEN}✓{C.RESET} Saved to {args.output}")
    
    # Store in Redis
    if args.redis:
        try:
            from token_pool import TokenPool
            pool = TokenPool()
            for ts in tokens:
                pool.add_token(ts)
            print(f"\n  {C.GREEN}✓{C.RESET} Added {len(tokens)} tokens to Redis pool")
            print(f"  {C.DIM}Pool size: {pool.pool_size()}{C.RESET}")
            pool.close()
        except Exception as e:
            print(f"\n  {C.RED}✗{C.RESET} Redis error: {e}")
    
    # Estimate capacity
    requests_per_token = 75  # Conservative estimate
    total_capacity = len(tokens) * requests_per_token
    
    print(f"\n  {C.BOLD}Estimated capacity:{C.RESET}")
    print(f"    ~{total_capacity:,} API requests")
    print(f"    ~{total_capacity:,} tweets/users fetchable")
    print(f"\n{'═' * 60}\n")


if __name__ == "__main__":
    main()
