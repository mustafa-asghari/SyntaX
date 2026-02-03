# SyntaX Guest-Only Scaling Plan

## TL;DR

You can scrape X/Twitter at business scale **without using your personal account** by:

1. **Syndication API** - Fetch tweets without ANY tokens (different rate limit pool)
2. **Guest Token Factory** - Generate 100s of tokens from rotating proxies
3. **Disposable Token Strategy** - Treat tokens as expendable (50-100 requests, then discard)

---

## Architecture Overview

```
┌─────────────────────────────────────────────────────────────────┐
│                     YOUR BUSINESS SERVER                         │
├─────────────────────────────────────────────────────────────────┤
│                                                                   │
│   ┌─────────────┐     ┌─────────────┐     ┌─────────────┐       │
│   │  Syndication │     │   GraphQL   │     │   Token     │       │
│   │     API      │     │     API     │     │  Generator  │       │
│   └──────┬──────┘     └──────┬──────┘     └──────┬──────┘       │
│          │                   │                   │               │
│          │                   │                   │               │
│   ┌──────▼──────┐     ┌──────▼──────┐     ┌──────▼──────┐       │
│   │  No Token   │     │  Token Pool │     │   Proxy     │       │
│   │   Needed    │     │   (Redis)   │     │   Manager   │       │
│   └─────────────┘     └─────────────┘     └─────────────┘       │
│                                                                   │
└────────────────────────────┬────────────────────────────────────┘
                             │
                             ▼
            ┌────────────────────────────────┐
            │     ROTATING PROXY PROVIDER     │
            │  (Bright Data, SmartProxy, etc) │
            │                                  │
            │   IP1 ──► Guest Token #1        │
            │   IP2 ──► Guest Token #2        │
            │   IP3 ──► Guest Token #3        │
            │   ...                           │
            └────────────────────────────────┘
```

---

## What You Can Access (Guest Mode)

| Endpoint | Access | Method | Notes |
|----------|--------|--------|-------|
| Single Tweet by ID | ✅ YES | Syndication | **No token needed!** |
| User Profile by Username | ✅ YES | GraphQL | Needs guest token |
| User Profile by ID | ✅ YES | GraphQL | Needs guest token |
| User Timeline | ❌ NO | - | Requires auth |
| Search | ❌ NO | - | Requires auth |
| Tweet Replies | ❌ NO | - | Requires auth |

---

## Vector 1: Syndication API (Best Option)

**The Syndication API is your secret weapon.** It's X's public embed endpoint and has:
- Different rate limit pool than GraphQL
- No authentication required
- Returns full tweet data including media

### Usage

```python
from endpoints.syndication import get_tweet_syndication, get_tweets_batch_syndication

# Single tweet
tweet, ms = get_tweet_syndication("1585341984679469056")
print(f"@{tweet.author_username}: {tweet.text}")

# Batch (parallel)
tweet_ids = ["1585341984679469056", "1234567890123456789", ...]
results = get_tweets_batch_syndication(tweet_ids, max_workers=10)
```

### Rate Limits (Estimated)
- ~300 requests per IP per 15 minutes
- With proxies: Virtually unlimited

---

## Vector 2: Guest Token Factory

Generate massive quantities of guest tokens, each from a different IP:

```bash
# Set up proxies
export PROXY_LIST="http://user:pass@proxy1:8080,http://user:pass@proxy2:8080,..."

# Generate 100 tokens
python token_generator.py --count 100 --redis

# Each token = ~50-100 safe requests
# 100 tokens = 5,000-10,000 total requests
```

### Token Lifecycle

```
1. Generate token (from unique proxy IP)
           │
           ▼
2. Use for 50-100 requests
           │
           ▼
3. Discard (before rate limit triggers)
           │
           ▼
4. Generate new token (from new IP)
```

---

## Vector 3: Proxy Requirements

**Without proxies, you will be banned within hours.**

### Recommended Providers

| Provider | Type | Cost | Notes |
|----------|------|------|-------|
| Bright Data | Residential | $15/GB | Best quality, expensive |
| SmartProxy | Residential | $12/GB | Good balance |
| Oxylabs | Residential | $15/GB | Enterprise grade |
| IPRoyal | Residential | $7/GB | Budget option |

### Configuration

```bash
# Single proxy
export PROXY_URL="http://user:pass@proxy.example.com:8080"

# Multiple proxies (comma-separated)
export PROXY_LIST="http://p1:8080,http://p2:8080,http://p3:8080"

# From file
export PROXY_LIST="/path/to/proxies.txt"
```

### Proxy File Format (`proxies.txt`)
```
http://user:pass@proxy1.example.com:8080
http://user:pass@proxy2.example.com:8080
# Comments are ignored
http://user:pass@proxy3.example.com:8080
```

---

## Scaling Strategy

### Small Scale (1,000 tweets/day)
- Syndication API only
- No proxies needed (your IP alone)
- Cost: FREE

### Medium Scale (10,000 tweets/day)
- Syndication API + Guest Tokens
- 5-10 residential proxies
- Cost: ~$20-50/month

### Large Scale (100,000+ tweets/day)
- Full proxy rotation (100+ IPs)
- Token factory continuously generating
- Redis-backed token pool
- Cost: ~$200-500/month

---

## Files Created

| File | Purpose |
|------|---------|
| `endpoints/syndication.py` | Syndication API wrapper (no auth needed) |
| `proxy_manager.py` | Rotating proxy management |
| `token_generator.py` | Industrial-scale token generation |

---

## Quick Start

```bash
# 1. Activate venv
source venv/bin/activate
cd scraper/src

# 2. Test syndication (no setup needed)
python -c "
from endpoints.syndication import get_tweet_syndication
tweet, _ = get_tweet_syndication('1585341984679469056')
print(f'@{tweet.author_username}: {tweet.text[:80]}')
"

# 3. Generate guest tokens (with proxies for production)
# export PROXY_LIST="http://user:pass@proxy1:8080,..."
python token_generator.py --count 10

# 4. Run full test
python test_all_endpoints.py
```

---

## Legal Disclaimer

⚠️ **This scraping violates X's Terms of Service.**

- Your business could face legal action from X
- Accounts used for auth will be permanently banned
- X actively updates defenses; this will break periodically

**Recommendations:**
1. Consult a lawyer before commercial use
2. Never use your personal account
3. Treat all infrastructure as disposable
4. Budget for ongoing maintenance (10-15 hours/month)

---

## What's Still Blocked

Even with all these techniques, Guest tokens **cannot** access:

- User Timelines (who posted what)
- Search (find tweets by keyword)
- Tweet Replies/Threads
- Follower/Following lists
- DMs (obviously)

To get these, you need authenticated accounts. For business scale, companies purchase "account farms" (thousands of disposable accounts). This is legally risky and expensive (~$2-5 per account, accounts burn out quickly).

---

## Recommended Architecture for Business

```
                    ┌─────────────────┐
                    │   Your Backend   │
                    └────────┬────────┘
                             │
              ┌──────────────┼──────────────┐
              │              │              │
   ┌──────────▼────┐  ┌──────▼──────┐  ┌────▼────────┐
   │  Syndication   │  │   GraphQL   │  │   3rd Party │
   │  (Free, High)  │  │ (Guest Tok) │  │  APIs ($$)  │
   └───────────────┘  └─────────────┘  └─────────────┘
              │              │              │
              └──────────────┼──────────────┘
                             │
                      Data Pipeline
                             │
                             ▼
                    ┌─────────────────┐
                    │   Your Database  │
                    │  (ClickHouse)    │
                    └─────────────────┘
```

If you need Search/Timelines at scale, consider:
- **Official X API** ($100-42,000/month)
- **Third-party data providers** (Brandwatch, Sprinklr, etc.)
- **Account farming** (High risk, legally grey)

---

## Summary

| Goal | Solution | Risk Level |
|------|----------|------------|
| Fetch tweets by ID | Syndication API | LOW (public endpoint) |
| Fetch user profiles | Guest + Proxies | MEDIUM (TOS violation) |
| Search/Timelines | Auth accounts | HIGH (account bans, legal) |
| Business scale | Pay for official API | NONE (legitimate) |

The setup I've provided gives you the maximum Guest-mode capability. For anything beyond (Search, Timelines), you're entering higher-risk territory that requires either paying for official access or accepting significant operational overhead.
