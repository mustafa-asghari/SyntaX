
import requests
import time
import json
import threading
import statistics
from concurrent.futures import ThreadPoolExecutor

LOCAL_BASE = "http://localhost:8000/v1"
RAILWAY_BASE = "https://syntax-production-ae67.up.railway.app/v1"
COMP_BASE = "https://api.twitterapi.io/twitter"
COMP_KEY = "new1_5e0eae591a60484585902c51373c585b"
COMP_HEADERS = {"X-API-Key": COMP_KEY}

def log(msg):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}")

def test_search_flow(base_url, label):
    query = f"benchmark_{int(time.time())}" # Unique query to ensure cold start
    url = f"{base_url}/search?q={query}"
    
    log(f"--- Testing {label} API (Query: {query}) ---")
    
    # 1. Cold Fetch
    start = time.perf_counter()
    resp = requests.get(url)
    elapsed = (time.perf_counter() - start) * 1000
    try:
        data = resp.json()
        meta = data.get("meta", {})
        log(f"1. Cold Live Fetch: {elapsed:.2f}ms | Status: {resp.status_code} | Layer: {meta.get('cache_layer')} | Meta Time: {meta.get('response_time_ms')}ms")
    except:
        log(f"1. Cold Fetch Failed: {resp.text}")

    # 2. Hot Fetch (Redis)
    start = time.perf_counter()
    resp = requests.get(url)
    elapsed = (time.perf_counter() - start) * 1000
    try:
        data = resp.json()
        meta = data.get("meta", {})
        log(f"2. Redis Hit:       {elapsed:.2f}ms | Status: {resp.status_code} | Layer: {meta.get('cache_layer')} | Meta Time: {meta.get('response_time_ms')}ms")
    except:
        log(f"2. Redis Fetch Failed")

    # 3. Force Fresh
    start = time.perf_counter()
    resp = requests.get(url + "&fresh=true")
    elapsed = (time.perf_counter() - start) * 1000
    try:
        data = resp.json()
        meta = data.get("meta", {})
        log(f"3. Force Fresh:     {elapsed:.2f}ms | Status: {resp.status_code} | Layer: {meta.get('cache_layer')} | Meta Time: {meta.get('response_time_ms')}ms")
    except:
        log(f"3. Force Fresh Failed")

def test_coalescing(base_url, label):
    log(f"\n--- Testing {label} Request Coalescing (10 concurrent) ---")
    query = f"coalesce_{int(time.time())}"
    url = f"{base_url}/search?q={query}"
    
    def fetch():
        start = time.perf_counter()
        r = requests.get(url)
        dur = (time.perf_counter() - start) * 1000
        return r.json(), dur

    with ThreadPoolExecutor(max_workers=10) as executor:
        futures = [executor.submit(fetch) for _ in range(10)]
        results = [f.result() for f in futures]
    
    live_count = 0
    wait_types = 0
    times = []
    
    for data, dur in results:
        meta = data.get("meta", {})
        layer = meta.get("cache_layer")
        times.append(dur)
        if layer == "live": live_count += 1
        if layer == "coalesced": wait_types += 1 # Assuming "coalesced" or similar logic handles shared promise
        
        # Check backend log for actual network calls (can't see here, but inferred from 'live' count)
    
    avg_time = statistics.mean(times)
    log(f"Stats: Avg Time {avg_time:.2f}ms | Live Responses: {live_count} (Should be 1) | Others: {10 - live_count}")
    # Note: Depending on implementation, 'coalesced' requests might verify as 'live' in meta or have special tag. 
    # Usually coalescing means 1 backend call, but all return the result. 
    # If the API returns "cache_layer": "live" for all of them, we check if they finished at the same time.

def test_competitor():
    log("\n--- Testing Competitor API ---")
    query = "bitcoin" # Use common query
    url = f"{COMP_BASE}/tweet/advanced_search?query={query}&queryType=Latest"
    
    # 1. Standard (Likely Cached)
    start = time.perf_counter()
    resp = requests.get(url, headers=COMP_HEADERS)
    elapsed = (time.perf_counter() - start) * 1000
    log(f"1. Standard Fetch:  {elapsed:.2f}ms | Status: {resp.status_code}")

    # 2. Fresh (Force) — wait 5s to avoid QPS limit
    time.sleep(5)
    start = time.perf_counter()
    resp = requests.get(url + "&fresh=true", headers=COMP_HEADERS)
    elapsed = (time.perf_counter() - start) * 1000
    log(f"2. Fresh Fetch:     {elapsed:.2f}ms | Status: {resp.status_code} | Body: {resp.text[:100]}")

if __name__ == "__main__":
    try:
        test_search_flow(LOCAL_BASE, "Local")
        test_coalescing(LOCAL_BASE, "Local")
        test_search_flow(RAILWAY_BASE, "Railway")
        test_coalescing(RAILWAY_BASE, "Railway")
        test_competitor()
    except Exception as e:
        log(f"Error: {e}")
