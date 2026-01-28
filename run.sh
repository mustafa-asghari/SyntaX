#!/bin/bash

# SyntaX Runner Script

set -e

# Colors
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

echo -e "${GREEN}SyntaX - The Fastest X API${NC}"
echo "=================================="

case "$1" in
    "setup")
        echo "Setting up environment..."
        cp .env.example .env
        echo "Created .env file"
        ;;

    "start")
        echo "Starting services with Docker..."
        docker-compose up -d redis clickhouse
        echo "Waiting for services..."
        sleep 3
        echo -e "${GREEN}Services started!${NC}"
        echo "  Redis: localhost:6379"
        echo "  ClickHouse: localhost:8123"
        ;;

    "stop")
        echo "Stopping services..."
        docker-compose down
        ;;

    "test")
        echo "Running scraper test..."
        cd scraper
        pip install -r requirements.txt -q
        python -m src.test_scrape $2
        ;;

    "discover")
        echo "Discovering query IDs..."
        cd scraper
        pip install -r requirements.txt -q
        python -m src.query_monitor
        ;;

    "api")
        echo "Starting API server..."
        cd api
        pip install -r requirements.txt -q
        uvicorn src.main:app --reload --host 0.0.0.0 --port 8000
        ;;

    "tokens")
        echo "Starting token manager..."
        cd scraper
        pip install -r requirements.txt -q
        python -m src.token_manager
        ;;

    "docker-test")
        echo "Building and running scraper in Docker..."
        docker-compose build scraper
        docker-compose run --rm scraper python -m src.test_scrape $2
        ;;

    *)
        echo "Usage: ./run.sh <command>"
        echo ""
        echo "Commands:"
        echo "  setup       - Create .env from template"
        echo "  start       - Start Redis and ClickHouse containers"
        echo "  stop        - Stop all containers"
        echo "  test [user] - Test scraper (default: elonmusk)"
        echo "  discover    - Discover query IDs from X bundles"
        echo "  api         - Start API server"
        echo "  tokens      - Start token manager"
        echo "  docker-test - Test scraper in Docker"
        ;;
esac
