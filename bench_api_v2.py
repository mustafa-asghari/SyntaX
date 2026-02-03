#!/usr/bin/env python3
"""
Fair head-to-head API benchmark — SyntaX vs Competitor.

Fairness rules:
  1. Same query, same moment — both APIs get the EXACT same request
  2. Alternating order — odd rounds SyntaX first, even rounds competitor first
     (eliminates "who goes first" bias)
  3. Shared warmup — both TCP+TLS sessions pre-warmed before any timing
  4. Same timeout, same HTTP client, same thread
  5. Unique queries for cold tests, common queries for cache tests
  6. Percentile stats (P50, P95, min, max) not just averages
  7. Multiple rounds with jitter to avoid burst bias
"""

import os
import re
import sys
import time
import random
import statistics
import threading
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests

# ── Config ──────────────────────────────────────────────────

SYNTAX_BASE = os.environ.get("SYNTAX_URL", "https://api.syntaxapi.dev")
COMP_BASE = "https://api.twitterapi.io"
COMP_KEY = os.environ.get("COMP_API_KEY", "new1_f72dd2710635464f87e509f197cd75f5")

TIMEOUT = (5, 15)
ROUNDS = 10          # requests per test
COMMON_QUERIES = ["bitcoin", "ethereum", "AI", "python", "elon musk"]

# ── Helpers ─────────────────────────────────────────────────

_log_lines = []


def log(msg=""):
    line = f"[{time.strftime('%H:%M:%S')}] {msg}" if msg else ""
    print(line)
    _log_lines.append(line)


def save_results():
    os.makedirs("bench_results", exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = f"bench_results/bench_{ts}.txt"
    with open(path, "w") as f:
        f.write(f"SyntaX vs Competitor Benchmark — {datetime.now().isoformat()}\n")
        f.write("=" * 70 + "\n\n")
        f.write("\n".join(_log_lines) + "\n")
    print(f"\nResults saved to {path}")


def timed_get(session, url, headers=None):
    """Single timed GET. Returns (status, ttfb_ms, total_ms, size_bytes, cf_cache)."""
    start = time.perf_counter()

    resp = session.get(url, headers=headers, timeout=TIMEOUT, stream=True)
    first_byte = resp.raw.read(1)  # actual TTFB — first byte received
    ttfb = time.perf_counter()

    rest = resp.raw.read()
    done = time.perf_counter()

    body = first_byte + rest
    ttfb_ms = (ttfb - start) * 1000
    total_ms = (done - start) * 1000
    cf_cache = resp.headers.get("cf-cache-status", "")
    cache_layer = resp.headers.get("x-cache-layer", "")

    return {
        "status": resp.status_code,
        "ttfb_ms": round(ttfb_ms, 2),
        "total_ms": round(total_ms, 2),
        "size": len(body),
        "cf_cache": cf_cache,
        "cache_layer": cache_layer,
    }


def warmup(session, url):
    """Pre-warm TCP+TLS. Not timed."""
    try:
        session.get(url, timeout=TIMEOUT)
    except Exception:
        pass


def pstats(values):
    """Compute percentile stats from a list of floats."""
    if not values:
        return {"n": 0}
    s = sorted(values)
    n = len(s)
    return {
        "n": n,
        "min": round(s[0], 1),
        "p50": round(s[n // 2], 1),
        "avg": round(statistics.mean(s), 1),
        "p95": round(s[int(n * 0.95)], 1),
        "max": round(s[-1], 1),
    }


def fmt_stats(label, values, unit="ms"):
    """Format percentile stats as a single line."""
    st = pstats(values)
    if st["n"] == 0:
        return f"  {label}: no data"
    return (
        f"  {label}: "
        f"min={st['min']}{unit}  "
        f"P50={st['p50']}{unit}  "
        f"avg={st['avg']}{unit}  "
        f"P95={st['p95']}{unit}  "
        f"max={st['max']}{unit}  "
        f"(n={st['n']})"
    )


# ── Build request URLs ──────────────────────────────────────

def syntax_search_url(query):
    return f"{SYNTAX_BASE}/v1/search?q={query}"


def comp_search_url(query):
    return f"{COMP_BASE}/twitter/tweet/advanced_search?query={query}&queryType=Top"


# ── Test 1: Cached / warm query ────────────────────────────

def test_cached(rounds=ROUNDS):
    """
    Both APIs hit with common queries that should be cached.
    Alternating order per round for fairness.
    """
    log("=" * 70)
    log("TEST 1: CACHED / WARM QUERIES")
    log(f"  {rounds} rounds, alternating who goes first, common queries")
    log("=" * 70)

    syn_session = requests.Session()
    comp_session = requests.Session()

    # Warmup TCP+TLS for both
    warmup(syn_session, f"{SYNTAX_BASE}/health")
    warmup(comp_session, f"{COMP_BASE}/twitter/tweet/advanced_search?query=test&queryType=Top")

    # Prime caches — hit each query once so both APIs have it cached
    for q in COMMON_QUERIES:
        try:
            syn_session.get(syntax_search_url(q), timeout=TIMEOUT)
        except Exception:
            pass
        try:
            comp_session.get(comp_search_url(q), headers={"X-API-Key": COMP_KEY}, timeout=TIMEOUT)
        except Exception:
            pass
    time.sleep(1)  # let caches settle

    syn_results = []
    comp_results = []

    for i in range(rounds):
        q = COMMON_QUERIES[i % len(COMMON_QUERIES)]

        if i % 2 == 0:
            # SyntaX first
            sr = timed_get(syn_session, syntax_search_url(q))
            cr = timed_get(comp_session, comp_search_url(q), headers={"X-API-Key": COMP_KEY})
        else:
            # Competitor first
            cr = timed_get(comp_session, comp_search_url(q), headers={"X-API-Key": COMP_KEY})
            sr = timed_get(syn_session, syntax_search_url(q))

        syn_results.append(sr)
        comp_results.append(cr)

        first = "SYN" if i % 2 == 0 else "CMP"
        log(
            f"  Round {i+1:2d} [{first} first] q={q:12s} | "
            f"SYN: {sr['ttfb_ms']:7.1f}ms ttfb {sr['total_ms']:7.1f}ms total (cf={sr['cf_cache']:>7s}) | "
            f"CMP: {cr['ttfb_ms']:7.1f}ms ttfb {cr['total_ms']:7.1f}ms total"
        )

        time.sleep(0.1)  # small gap to avoid burst effects

    log()
    log("  CACHED SUMMARY (TTFB):")
    log(fmt_stats("SyntaX ", [r["ttfb_ms"] for r in syn_results]))
    log(fmt_stats("Competi", [r["ttfb_ms"] for r in comp_results]))
    log()
    log("  CACHED SUMMARY (Total):")
    log(fmt_stats("SyntaX ", [r["total_ms"] for r in syn_results]))
    log(fmt_stats("Competi", [r["total_ms"] for r in comp_results]))

    # Winner
    syn_avg = statistics.mean(r["ttfb_ms"] for r in syn_results)
    comp_avg = statistics.mean(r["ttfb_ms"] for r in comp_results)
    diff = comp_avg - syn_avg
    if diff > 0:
        log(f"\n  >> SyntaX faster by {diff:.1f}ms avg TTFB")
    else:
        log(f"\n  >> Competitor faster by {-diff:.1f}ms avg TTFB")

    syn_session.close()
    comp_session.close()

    return syn_results, comp_results


# ── Test 2: Cold / unique query (cache miss) ───────────────

def test_cold(rounds=5):
    """
    Both APIs get a unique never-seen query — guaranteed cache miss.
    Same query to both at the same time.
    """
    log()
    log("=" * 70)
    log("TEST 2: COLD / UNIQUE QUERIES (cache miss)")
    log(f"  {rounds} rounds, unique query each round, alternating order")
    log("=" * 70)

    syn_session = requests.Session()
    comp_session = requests.Session()

    warmup(syn_session, f"{SYNTAX_BASE}/health")
    warmup(comp_session, f"{COMP_BASE}/twitter/tweet/advanced_search?query=warmup&queryType=Top")

    syn_results = []
    comp_results = []

    for i in range(rounds):
        q = f"xbench{int(time.time()*1000)}{random.randint(100,999)}"

        if i % 2 == 0:
            sr = timed_get(syn_session, syntax_search_url(q))
            cr = timed_get(comp_session, comp_search_url(q), headers={"X-API-Key": COMP_KEY})
        else:
            cr = timed_get(comp_session, comp_search_url(q), headers={"X-API-Key": COMP_KEY})
            sr = timed_get(syn_session, syntax_search_url(q))

        syn_results.append(sr)
        comp_results.append(cr)

        log(
            f"  Round {i+1:2d} q={q} | "
            f"SYN: {sr['ttfb_ms']:7.1f}ms ttfb {sr['total_ms']:7.1f}ms total (layer={sr['cache_layer']}) | "
            f"CMP: {cr['ttfb_ms']:7.1f}ms ttfb {cr['total_ms']:7.1f}ms total"
        )

        time.sleep(0.5)  # gap between cold rounds

    log()
    log("  COLD SUMMARY (TTFB):")
    log(fmt_stats("SyntaX ", [r["ttfb_ms"] for r in syn_results]))
    log(fmt_stats("Competi", [r["ttfb_ms"] for r in comp_results]))
    log()
    log("  COLD SUMMARY (Total):")
    log(fmt_stats("SyntaX ", [r["total_ms"] for r in syn_results]))
    log(fmt_stats("Competi", [r["total_ms"] for r in comp_results]))

    syn_avg = statistics.mean(r["ttfb_ms"] for r in syn_results)
    comp_avg = statistics.mean(r["ttfb_ms"] for r in comp_results)
    diff = comp_avg - syn_avg
    if diff > 0:
        log(f"\n  >> SyntaX faster by {diff:.1f}ms avg TTFB")
    else:
        log(f"\n  >> Competitor faster by {-diff:.1f}ms avg TTFB")

    syn_session.close()
    comp_session.close()

    return syn_results, comp_results


# ── Test 3: Response size comparison ────────────────────────

def test_response_size():
    """Compare response payload sizes for the same query."""
    log()
    log("=" * 70)
    log("TEST 3: RESPONSE SIZE")
    log("=" * 70)

    syn_session = requests.Session()
    comp_session = requests.Session()

    for q in ["bitcoin", "AI"]:
        sr = timed_get(syn_session, syntax_search_url(q))
        cr = timed_get(comp_session, comp_search_url(q), headers={"X-API-Key": COMP_KEY})
        log(f"  q={q:10s} | SYN: {sr['size']:,} bytes | CMP: {cr['size']:,} bytes | ratio: {sr['size']/max(cr['size'],1):.2f}x")

    syn_session.close()
    comp_session.close()


# ── Test 4: Concurrent load ────────────────────────────────

def test_concurrent(n=10):
    """Fire N requests at both APIs simultaneously."""
    log()
    log("=" * 70)
    log(f"TEST 4: CONCURRENT LOAD ({n} simultaneous requests)")
    log("=" * 70)

    q = "bitcoin"

    # Pre-create and warm sessions
    syn_sessions = []
    comp_sessions = []
    for _ in range(n):
        s = requests.Session()
        warmup(s, f"{SYNTAX_BASE}/health")
        syn_sessions.append(s)
        c = requests.Session()
        warmup(c, f"{COMP_BASE}/twitter/tweet/advanced_search?query=warmup&queryType=Top")
        comp_sessions.append(c)

    barrier = threading.Barrier(n * 2, timeout=15)

    def fire(session, url, headers=None):
        barrier.wait()
        return timed_get(session, url, headers=headers)

    with ThreadPoolExecutor(max_workers=n * 2) as pool:
        syn_futures = [
            pool.submit(fire, syn_sessions[i], syntax_search_url(q))
            for i in range(n)
        ]
        comp_futures = [
            pool.submit(fire, comp_sessions[i], comp_search_url(q), {"X-API-Key": COMP_KEY})
            for i in range(n)
        ]

        syn_results = [f.result() for f in syn_futures]
        comp_results = [f.result() for f in comp_futures]

    for s in syn_sessions:
        s.close()
    for c in comp_sessions:
        c.close()

    log("  CONCURRENT TTFB:")
    log(fmt_stats("SyntaX ", [r["ttfb_ms"] for r in syn_results]))
    log(fmt_stats("Competi", [r["ttfb_ms"] for r in comp_results]))
    log()
    log("  CONCURRENT TOTAL:")
    log(fmt_stats("SyntaX ", [r["total_ms"] for r in syn_results]))
    log(fmt_stats("Competi", [r["total_ms"] for r in comp_results]))

    return syn_results, comp_results


# ── Main ────────────────────────────────────────────────────

def main():
    log(f"SyntaX vs Competitor — Fair Benchmark")
    log(f"Date: {datetime.now().isoformat()}")
    log(f"SyntaX:     {SYNTAX_BASE}")
    log(f"Competitor: {COMP_BASE}")
    log(f"Runner:     {os.uname().nodename}")
    log()

    cached_syn, cached_comp = test_cached()
    log()
    cold_syn, cold_comp = test_cold()
    test_response_size()
    conc_syn, conc_comp = test_concurrent()

    # ── Final Verdict ──
    log()
    log("=" * 70)
    log("FINAL VERDICT")
    log("=" * 70)

    for label, syn, comp in [
        ("Cached TTFB", [r["ttfb_ms"] for r in cached_syn], [r["ttfb_ms"] for r in cached_comp]),
        ("Cold TTFB",   [r["ttfb_ms"] for r in cold_syn],   [r["ttfb_ms"] for r in cold_comp]),
        ("Concurrent",  [r["ttfb_ms"] for r in conc_syn],   [r["ttfb_ms"] for r in conc_comp]),
    ]:
        sa = statistics.mean(syn) if syn else 0
        ca = statistics.mean(comp) if comp else 0
        diff = ca - sa
        winner = "SyntaX" if diff > 0 else "Competitor"
        log(f"  {label:16s}: SYN avg {sa:7.1f}ms | CMP avg {ca:7.1f}ms | {winner} wins by {abs(diff):.1f}ms")

    log()
    save_results()


if __name__ == "__main__":
    main()
