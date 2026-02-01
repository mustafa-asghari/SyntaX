
import requests
import time
import statistics
import threading
import os
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor

LOCAL_BASE = "http://localhost:8000/v1"
RAILWAY_BASE = "https://syntax-production-ae67.up.railway.app/v1"
COMP_BASE = "https://api.twitterapi.io/twitter"
COMP_KEY = "new1_f72dd2710635464f87e509f197cd75f5"
COMP_HEADERS = {"X-API-Key": COMP_KEY}
TIMEOUT = (5, 30)


def _now_ns():
    return time.perf_counter_ns()


def _elapsed_ms(start_ns: int) -> float:
    return (time.perf_counter_ns() - start_ns) / 1_000_000.0


_log_lines = []

def log(msg):
    line = f"[{time.strftime('%H:%M:%S')}] {msg}"
    print(line)
    _log_lines.append(line)


def save_results():
    os.makedirs("bench_results", exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = f"bench_results/bench_{ts}.txt"
    with open(path, "w") as f:
        f.write(f"SyntaX Benchmark — {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
        f.write("=" * 60 + "\n\n")
        f.write("\n".join(_log_lines) + "\n")
    print(f"\nResults saved to {path}")


def _request_json(session, url, headers=None):
    start_ns = _now_ns()
    resp = session.get(url, headers=headers, timeout=TIMEOUT)
    elapsed_ms = _elapsed_ms(start_ns)
    data = None
    try:
        data = resp.json()
    except Exception:
        pass
    return resp, data, elapsed_ms


def _warmup(session, base_url):
    """Send a throwaway request to establish TCP+TLS connection."""
    try:
        session.get(f"{base_url}/../health", timeout=TIMEOUT)
    except Exception:
        pass


def test_search_flow(base_url, label, session):
    _warmup(session, base_url)
    query = f"benchmark_{int(time.time())}"
    url = f"{base_url}/search?q={query}"

    log(f"--- Testing {label} API (Query: {query}) ---")

    # 1. Cold Fetch (cache miss, but TCP is warm)
    resp, data, elapsed = _request_json(session, url)
    if data is not None:
        meta = data.get("meta", {})
        log(f"1. Cold Live Fetch: {elapsed:.2f}ms (E2E) | {meta.get('response_time_ms')}ms (Server) | Layer: {meta.get('cache_layer')} | Status: {resp.status_code}")
    else:
        log(f"1. Cold Fetch Failed: {resp.text[:200]}")

    # 2. Hot Fetch (Redis)
    resp, data, elapsed = _request_json(session, url)
    if data is not None:
        meta = data.get("meta", {})
        log(f"2. Redis Hit:       {elapsed:.2f}ms (E2E) | {meta.get('response_time_ms')}ms (Server) | Layer: {meta.get('cache_layer')} | Status: {resp.status_code}")
    else:
        log(f"2. Redis Fetch Failed: {resp.text[:200]}")

    # 3. Force Fresh
    resp, data, elapsed = _request_json(session, url + "&fresh=true")
    if data is not None:
        meta = data.get("meta", {})
        log(f"3. Force Fresh:     {elapsed:.2f}ms (E2E) | {meta.get('response_time_ms')}ms (Server) | Layer: {meta.get('cache_layer')} | Status: {resp.status_code}")
    else:
        log(f"3. Force Fresh Failed: {resp.text[:200]}")


def test_coalescing(base_url, label):
    log(f"\n--- Testing {label} Request Coalescing (10 concurrent) ---")
    query = f"coalesce_{int(time.time())}"
    url = f"{base_url}/search?q={query}"

    # Pre-create sessions and warm them up so TLS doesn't stagger requests
    sessions = []
    for _ in range(10):
        s = requests.Session()
        _warmup(s, base_url)
        sessions.append(s)

    # Use a barrier so all threads fire at the same instant
    barrier = threading.Barrier(10, timeout=10)

    def fetch(idx):
        barrier.wait()  # all threads release together
        start_ns = _now_ns()
        resp = sessions[idx].get(url, timeout=TIMEOUT)
        elapsed_ms = _elapsed_ms(start_ns)
        data = None
        try:
            data = resp.json()
        except Exception:
            pass
        return data, elapsed_ms, resp.status_code

    with ThreadPoolExecutor(max_workers=10) as executor:
        futures = [executor.submit(fetch, i) for i in range(10)]
        results = [f.result() for f in futures]

    # Clean up sessions
    for s in sessions:
        s.close()

    live_count = 0
    coalesced_count = 0
    redis_count = 0
    times = []
    layers = []

    for data, dur, status in results:
        if data is None:
            continue
        meta = data.get("meta", {})
        layer = meta.get("cache_layer", "unknown")
        times.append(dur)
        layers.append(layer)
        if layer == "live":
            live_count += 1
        elif layer == "coalesced":
            coalesced_count += 1
        elif layer == "redis":
            redis_count += 1

    avg_time = statistics.mean(times) if times else 0
    p50 = sorted(times)[len(times) // 2] if times else 0
    p99 = sorted(times)[int(len(times) * 0.99)] if times else 0
    log(f"Stats: Avg {avg_time:.0f}ms | P50 {p50:.0f}ms | P99 {p99:.0f}ms")
    log(f"Layers: live={live_count} coalesced={coalesced_count} redis={redis_count} (live should be 1)")


def test_competitor(session):
    log("\n--- Testing Competitor API ---")
    query = f"benchmark_{int(time.time())}"  # unique query for fair cold comparison
    url = f"{COMP_BASE}/tweet/advanced_search?query={query}&queryType=Latest"

    _warmup(session, COMP_BASE.rsplit("/", 1)[0])

    # 1. Cold Fetch
    resp, _data, elapsed = _request_json(session, url, headers=COMP_HEADERS)
    log(f"1. Cold Fetch:      {elapsed:.2f}ms | Status: {resp.status_code}")

    # 2. Cached Fetch (same query)
    time.sleep(5)
    resp, _data, elapsed = _request_json(session, url, headers=COMP_HEADERS)
    log(f"2. Cached Fetch:    {elapsed:.2f}ms | Status: {resp.status_code}")

    # 3. Common query
    time.sleep(5)
    common_url = f"{COMP_BASE}/tweet/advanced_search?query=bitcoin&queryType=Latest"
    resp, _data, elapsed = _request_json(session, common_url, headers=COMP_HEADERS)
    log(f"3. Common Query:    {elapsed:.2f}ms | Status: {resp.status_code}")


if __name__ == "__main__":
    try:
        local_session = requests.Session()
        railway_session = requests.Session()
        competitor_session = requests.Session()

        test_search_flow(LOCAL_BASE, "Local", local_session)
        test_coalescing(LOCAL_BASE, "Local")
        test_search_flow(RAILWAY_BASE, "Railway", railway_session)
        test_coalescing(RAILWAY_BASE, "Railway")
        test_competitor(competitor_session)
    except Exception as e:
        log(f"Error: {e}")
    finally:
        try:
            local_session.close()
            railway_session.close()
            competitor_session.close()
        except Exception:
            pass
        save_results()
