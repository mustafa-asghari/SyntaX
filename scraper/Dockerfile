FROM python:3.11-slim

WORKDIR /app

# Install system dependencies for curl-cffi and lxml
RUN apt-get update && apt-get install -y \
    curl \
    libxml2-dev \
    libxslt1-dev \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements first for caching
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy source code
COPY src/ ./src/

# Default command: run test script
CMD ["python", "-m", "src.test_scrape"]
