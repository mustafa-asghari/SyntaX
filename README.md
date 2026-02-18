# SyntaX API

High-performance data API designed for retrieving and analyzing social media data with low latency.
This repository contains the API layer, database schemas, and dashboard components.

> **Note**: The core data engine (`scraper` module) is held in a private repository for security and IP protection. This repository demonstrates the API architecture, caching strategies, and data models.

## Features

- **High Performance**: Optimized for speed with multi-layer caching (Redis, Typesense, ClickHouse).
- **Scalable Architecture**: Built with FastAPI and designed to scale horizontally.
- **Rich Data**: Endpoints for user profiles, tweets, search, and social graph.
- **Analytics Ready**: Integrated with ClickHouse for real-time analytics.

## Tech Stack

- **Framework**: FastAPI (Python)
- **Database**: ClickHouse (Analytics), Redis (Cache/Queue)
- **Search**: Typesense
- **Deployment**: Docker Compose

## API Endpoints

### Users
- `GET /v1/users/{username}` - Get user profile
- `GET /v1/users/id/{user_id}` - Get user profile by ID
- `GET /v1/users/{user_id}/tweets` - Get user timeline
- `GET /v1/users/{user_id}/followers` - Get followers
- `GET /v1/users/{user_id}/following` - Get following

### Tweets
- `GET /v1/tweets/{tweet_id}` - Get single tweet
- `GET /v1/tweets/{tweet_id}/detail` - Get tweet with replies
- `GET /v1/search` - Search tweets

## Getting Started

### Prerequisites
- Docker and Docker Compose
- Python 3.10+

### Setup

1. **Clone the repository**
   ```bash
   git clone <repo-url>
   cd SyntaX
   ```

2. **Environment Configuration**
   Copy the example environment file:
   ```bash
   cp .env.example .env
   ```
   Update `.env` with your secure credentials.

3. **Start Infrastructure**
   Run the specific services (Database & Cache):
   ```bash
   docker-compose up -d redis clickhouse typesense
   ```

4. **Run API Locally**
   ```bash
   pip install -r api/requirements.txt
   uvicorn api.src.main:app --reload
   ```

## Architecture

The system uses a tiered caching strategy:
1. **L1 Memory/Redis**: Hot data and session caching.
2. **L2 Search (Typesense)**: Fast indexed search for recent content.
3. **L3 Data Warehouse (ClickHouse)**: Long-term storage and heavy analytics.

## License

Private. All rights reserved.
