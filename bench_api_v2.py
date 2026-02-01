
import requests
import time
import statistics
from concurrent.futures import ThreadPoolExecutor

LOCAL_BASE = "http://localhost:8000/v1"
RAILWAY_BASE = "https://syntax-production-ae67.up.railway.app/v1"
COMP_BASE = "https://api.twitterapi.io/twitter"
COMP_KEY = "new1_5e0eae591a60484585902c51373c585b"
COMP_HEADERS = {"X-API-Key": COMP_KEY}
TIMEOUT = (5, 30)

def _now_ns():
    return time.perf_counter_ns()

def _elapsed_ms(start_ns: int) -> float:
    return (time.perf_counter_ns() - start_ns) / 1_000_000.0

def log(msg):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}")

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

def test_search_flow(base_url, label, session):
    query = f"benchmark_{int(time.time())}" # Unique query to ensure cold start
    url = f"{base_url}/search?q={query}"
    
    log(f"--- Testing {label} API (Query: {query}) ---")
    
    # 1. Cold Fetch
    resp, data, elapsed = _request_json(session, url)
    if data is not None:
        meta = data.get("meta", {})
        log(f"1. Cold Live Fetch: {elapsed:.2f}ms | Status: {resp.status_code} | Layer: {meta.get('cache_layer')} | Meta Time: {meta.get('response_time_ms')}ms")
    else:
        log(f"1. Cold Fetch Failed: {resp.text[:200]}")

    # 2. Hot Fetch (Redis)
    resp, data, elapsed = _request_json(session, url)
    if data is not None:
        meta = data.get("meta", {})
        log(f"2. Redis Hit:       {elapsed:.2f}ms | Status: {resp.status_code} | Layer: {meta.get('cache_layer')} | Meta Time: {meta.get('response_time_ms')}ms")
    else:
        log(f"2. Redis Fetch Failed: {resp.text[:200]}")

    # 3. Force Fresh
    resp, data, elapsed = _request_json(session, url + "&fresh=true")
    if data is not None:
        meta = data.get("meta", {})
        log(f"3. Force Fresh:     {elapsed:.2f}ms | Status: {resp.status_code} | Layer: {meta.get('cache_layer')} | Meta Time: {meta.get('response_time_ms')}ms")
    else:
        log(f"3. Force Fresh Failed: {resp.text[:200]}")

def test_coalescing(base_url, label):
    log(f"\n--- Testing {label} Request Coalescing (10 concurrent) ---")
    query = f"coalesce_{int(time.time())}"
    url = f"{base_url}/search?q={query}"
    
    def fetch():
        session = requests.Session()
        resp, data, elapsed = _request_json(session, url)
        session.close()
        return data, elapsed, resp.status_code

    with ThreadPoolExecutor(max_workers=10) as executor:
        futures = [executor.submit(fetch) for _ in range(10)]
        results = [f.result() for f in futures]
    
    live_count = 0
    times = []
    
    for data, dur, status in results:
        if data is None:
            continue
        meta = data.get("meta", {})
        layer = meta.get("cache_layer")
        times.append(dur)
        if layer == "live": live_count += 1
        
        # Check backend log for actual network calls (can't see here, but inferred from 'live' count)
    
    avg_time = statistics.mean(times) if times else 0
    log(f"Stats: Avg Time {avg_time:.2f}ms | Live Responses: {live_count} (Should be 1) | Others: {10 - live_count}")
    # Note: Depending on implementation, 'coalesced' requests might verify as 'live' in meta or have special tag. 
    # Usually coalescing means 1 backend call, but all return the result. 
    # If the API returns "cache_layer": "live" for all of them, we check if they finished at the same time.

def test_competitor(session):
    log("\n--- Testing Competitor API ---")
    query = "bitcoin" # Use common query
    url = f"{COMP_BASE}/tweet/advanced_search?query={query}&queryType=Latest"
    
    # 1. Standard (Likely Cached)
    resp, _data, elapsed = _request_json(session, url, headers=COMP_HEADERS)
    log(f"1. Standard Fetch:  {elapsed:.2f}ms | Status: {resp.status_code}")

    # 2. Fresh (Force) — wait 5s to avoid QPS limit
    time.sleep(5)
    resp, _data, elapsed = _request_json(session, url + "&fresh=true", headers=COMP_HEADERS)
    log(f"2. Fresh Fetch:     {elapsed:.2f}ms | Status: {resp.status_code} | Body: {resp.text[:100]}")

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
