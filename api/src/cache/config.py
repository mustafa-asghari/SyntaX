import os


class CacheConfig:
    # Startup/connect timeouts (seconds)
    CONNECT_TIMEOUT: float = float(os.getenv("CACHE_CONNECT_TIMEOUT", "3"))

    # Redis
    REDIS_URL: str = os.getenv("REDIS_URL", "redis://localhost:6379")

    # Typesense
    TYPESENSE_HOST: str = os.getenv("TYPESENSE_HOST", "localhost")
    TYPESENSE_PORT: int = int(os.getenv("TYPESENSE_PORT", "8108"))
    TYPESENSE_PROTOCOL: str = os.getenv("TYPESENSE_PROTOCOL", "http")
    TYPESENSE_API_KEY: str = os.getenv("TYPESENSE_API_KEY", "syntax-typesense-key")

    # ClickHouse
    CLICKHOUSE_HOST: str = os.getenv("CLICKHOUSE_HOST", "localhost")
    CLICKHOUSE_PORT: int = int(os.getenv("CLICKHOUSE_PORT", "8123"))
    CLICKHOUSE_USER: str = os.getenv("CLICKHOUSE_USER", "default")
    CLICKHOUSE_PASSWORD: str = os.getenv("CLICKHOUSE_PASSWORD", "")
    CLICKHOUSE_DB: str = os.getenv("CLICKHOUSE_DB", "syntax")

    # TTLs (seconds)
    TTL_SEARCH: int = 60
    TTL_TWEET: int = 1800       # 30 min
    TTL_TWEET_DETAIL: int = 300  # 5 min
    TTL_PROFILE: int = 60
    TTL_USER_TWEETS: int = 120
    TTL_SOCIAL: int = 120

    # SWR threshold (seconds) — responses older than this trigger background refresh
    SWR_THRESHOLD: int = 30

    # ClickHouse flush interval (seconds)
    CH_FLUSH_INTERVAL: int = 5
