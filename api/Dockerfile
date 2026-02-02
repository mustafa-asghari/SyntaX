FROM python:3.11-slim

WORKDIR /app

# Install system dependencies for curl-cffi and lxml
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    libxml2-dev \
    libxslt1-dev \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# Copy and install scraper deps first (API imports scraper modules)
COPY scraper/requirements.txt ./scraper/requirements.txt
RUN pip install --no-cache-dir -r scraper/requirements.txt

# Copy and install API deps
COPY api/requirements.txt ./api/requirements.txt
RUN pip install --no-cache-dir -r api/requirements.txt

# Copy source code
COPY scraper/src/ ./scraper/src/
COPY api/src/ ./api/src/
COPY scripts/ ./scripts/

# Set PYTHONPATH so uvicorn can find modules from any working directory
# - /app/api       → so "src.main" resolves to api/src/main.py
# - /app/scraper/src → so "from client import ..." works (scraper absolute imports)
# - /app/api/src   → so "from cache import ..." works
ENV PYTHONPATH="/app/api"

WORKDIR /app/api

EXPOSE 8000

CMD ["sh", "-c", "uvicorn src.main:app --host 0.0.0.0 --port ${PORT:-8000} --workers ${WORKERS:-2} --loop uvloop --log-level info"]
