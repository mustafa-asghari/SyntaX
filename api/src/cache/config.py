import os
from urllib.parse import urlparse


def _env_bool(name: str, default: str = "false") -> bool:
    return os.getenv(name, default).strip().lower() in ("1", "true", "yes", "y", "on")


def _apply_typesense_url(host: str, port: int, protocol: str) -> tuple[str, int, str]:
    raw = os.getenv("TYPESENSE_URL", "").strip()
    if not raw:
        return host, port, protocol
    parsed = urlparse(raw if "://" in raw else f"http://{raw}")
    new_host = parsed.hostname or host
    new_port = parsed.port or port
    new_protocol = parsed.scheme or protocol
    return new_host, new_port, new_protocol


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
    TYPESENSE_ENABLED: bool = _env_bool("TYPESENSE_ENABLED", "true")

    TYPESENSE_HOST, TYPESENSE_PORT, TYPESENSE_PROTOCOL = _apply_typesense_url(
        TYPESENSE_HOST, TYPESENSE_PORT, TYPESENSE_PROTOCOL
    )

    # ClickHouse
    CLICKHOUSE_HOST: str = os.getenv("CLICKHOUSE_HOST", "localhost")
    CLICKHOUSE_PORT: int = int(os.getenv("CLICKHOUSE_PORT", "8123"))
    CLICKHOUSE_USER: str = os.getenv("CLICKHOUSE_USER", "default")
    CLICKHOUSE_PASSWORD: str = os.getenv("CLICKHOUSE_PASSWORD", "")
    CLICKHOUSE_DB: str = os.getenv("CLICKHOUSE_DB", "syntax")
    CLICKHOUSE_BOOTSTRAP: bool = _env_bool("CLICKHOUSE_BOOTSTRAP", "true")
    CLICKHOUSE_INIT_SQL_PATH: str = os.getenv("CLICKHOUSE_INIT_SQL_PATH", "/app/scripts/init_db.sql")

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

    # Cross-process coalescing (Redis lock)
    COALESCE_LOCK_TTL: int = int(os.getenv("COALESCE_LOCK_TTL", "3"))
    COALESCE_WAIT_TIMEOUT: float = float(os.getenv("COALESCE_WAIT_TIMEOUT", "2"))
    COALESCE_WAIT_INTERVAL: float = float(os.getenv("COALESCE_WAIT_INTERVAL", "0.05"))
