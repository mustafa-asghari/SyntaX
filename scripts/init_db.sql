-- SyntaX ClickHouse Schema
-- Optimized for high-volume writes and fast analytics

-- Users table (scraped X profiles)
CREATE TABLE IF NOT EXISTS users (
    user_id String,
    username String,
    display_name String,
    bio String,
    location String,
    website String,
    followers_count UInt32,
    following_count UInt32,
    tweet_count UInt32,
    listed_count UInt32,
    is_verified UInt8,
    is_blue_verified UInt8,
    profile_image_url String,
    banner_url String,
    created_at DateTime64(3),
    scraped_at DateTime64(3) DEFAULT now64(3),
    raw_json String
) ENGINE = ReplacingMergeTree(scraped_at)
ORDER BY user_id;

-- Tweets table (scraped tweets)
CREATE TABLE IF NOT EXISTS tweets (
    tweet_id String,
    author_id String,
    author_username String,
    text String,
    created_at DateTime64(3),
    scraped_at DateTime64(3) DEFAULT now64(3),
    likes UInt32,
    retweets UInt32,
    replies UInt32,
    quotes UInt32,
    views UInt64,
    bookmarks UInt32,
    is_reply UInt8,
    reply_to_tweet_id String,
    is_retweet UInt8,
    is_quote UInt8,
    language String,
    source String,
    raw_json String
) ENGINE = MergeTree()
ORDER BY (created_at, tweet_id)
PARTITION BY toYYYYMM(created_at);

-- Usage events (API calls for billing)
CREATE TABLE IF NOT EXISTS usage_events (
    event_id UUID DEFAULT generateUUIDv4(),
    api_key_id String,
    endpoint String,
    method String,
    units_consumed UInt16,
    response_time_ms UInt16,
    status_code UInt16,
    ip_address String,
    created_at DateTime64(3) DEFAULT now64(3)
) ENGINE = MergeTree()
ORDER BY (created_at, api_key_id)
PARTITION BY toYYYYMMDD(created_at)
TTL created_at + INTERVAL 90 DAY;

-- Query IDs cache (for monitoring changes)
CREATE TABLE IF NOT EXISTS query_ids (
    operation_name String,
    query_id String,
    discovered_at DateTime64(3) DEFAULT now64(3),
    is_current UInt8 DEFAULT 1
) ENGINE = ReplacingMergeTree(discovered_at)
ORDER BY operation_name;

-- Token health metrics
CREATE TABLE IF NOT EXISTS token_metrics (
    token_id String,
    token_type String,  -- 'guest' or 'cf_cookie'
    requests_made UInt32,
    failures UInt32,
    avg_response_ms Float32,
    created_at DateTime64(3),
    expired_at DateTime64(3),
    recorded_at DateTime64(3) DEFAULT now64(3)
) ENGINE = MergeTree()
ORDER BY (recorded_at, token_id)
TTL recorded_at + INTERVAL 7 DAY;
