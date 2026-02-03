# SyntaX Scraper Speed Max

Goal: maximize throughput (req/s) and minimize latency per request while keeping
rate limits manageable. Notes are based on the current code in `scraper/src`.

## Highest impact changes (ranked)

1. Reuse authenticated clients/sessions for SearchTimeline and TweetDetail.
   Right now `search_tweets()` and `get_tweet_detail()` create a new `XClient`
   per request and close it. That kills TLS reuse and HTTP/2 multiplexing.
   Create a per-account client cache in `AccountPool` (or a new `ClientPool`)
   so each account keeps a warm session + proxy pinning.

2. Create a client pool per proxy (guest and auth).
   Each proxy should own a long-lived `XClient` session to avoid TLS handshakes
   and TCP slow-start on every request. Keep the pool small and reuse it.

3. Parallelize token generation.
   `TokenManager._fill_pool()` is serial. Spawn N workers (N == proxy count or
   a fixed number) to generate tokens concurrently. This removes cold-start
   delays and keeps pool refill ahead of demand.

4. Move batch requests to async or per-worker sessions.
   `graphql_request_batch()` uses a shared session across threads. If the
   session is not thread-safe, it adds contention and can hurt speed. Use
   per-worker `XClient` instances or switch to an async `AsyncSession` and
   gather requests concurrently.

5. Trim response payloads.
   The current `FEATURES` / `TWEET_FEATURES` set is large and increases payload
   size. Create "minimal" feature sets per endpoint for max speed and only
   request fields you actually use.

6. Skip heavy parsing and storage when not needed.
   `Tweet.raw_json` stores full results; for high volume, skip it by default
   or make it opt-in. Also consider skipping video variant selection if you
   only need metadata (saves parsing time).

## Concrete refactors to implement

- Add an account-bound client cache:
  - Store `XClient` on `Account` (or in a dict keyed by account label).
  - Prewarm each client once (`prewarm_connection()`).
  - On release, keep the client alive; only close on shutdown.

- Add a proxy-bound client cache for guest requests:
  - Pool size == number of proxies (or a small fixed size).
  - Each client pins to one proxy to keep TLS warm and consistent.

- Make `TokenManager` concurrent:
  - Spawn a fixed-size worker pool for token creation.
  - Each worker uses a different proxy.
  - Backoff + jitter on failures to avoid hammering a bad proxy.

- Add a "minimal payload" mode:
  - Per endpoint, define a reduced `features` dict.
  - Optionally add `FIELD_TOGGLES` variations to remove extra fields.
  - Validate that the endpoint still returns required fields.

## Configuration knobs to push harder

- `TOKEN_CONFIG["pool_target_size"]`: increase to keep more warm tokens.
- `TOKEN_CONFIG["pool_min_size"]`: raise so pool refills earlier.
- `TOKEN_CONFIG["max_requests_per_token"]`: raise only if you have more proxies;
  otherwise you will hit IP-level limits faster.
- `PROXY_ROTATION=health`: avoid sick proxies that slow everything down.

## Observed hot spots in current code

- `scraper/src/endpoints/search.py` and `scraper/src/endpoints/tweet.py`:
  new `XClient` per request for auth endpoints.
- `scraper/src/token_manager.py`: serial token generation.
- `scraper/src/client.py`: per-request header/cookie build; fine, but can be
  optimized further only after the big wins above.

## Measurement plan (keep this in place)

1. Use `SpeedDebugger` in `scraper/src/debug.py` to measure cold vs warm.
2. Add a small benchmark harness that runs N concurrent requests for 60s and
   reports p50/p95 latency + throughput.
3. Track failures + rate limits separately from latency to avoid false wins.

## Suggested quick win patch order

1. Add client cache for auth accounts (biggest latency drop).
2. Add guest client pool per proxy.
3. Parallelize token generation.
4. Add minimal feature sets per endpoint.
5. Evaluate async/multi for batch requests.
