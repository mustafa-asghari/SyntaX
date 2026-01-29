FROM python:3.11-slim

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y \
    curl \
    libxml2-dev \
    libxslt1-dev \
    && rm -rf /var/lib/apt/lists/*

# Copy scraper first (API depends on it)
COPY scraper/requirements.txt ./scraper/requirements.txt
COPY scraper/src/ ./scraper/src/

# Copy API requirements
COPY api/requirements.txt ./api/requirements.txt

# Install all requirements
RUN pip install --no-cache-dir -r scraper/requirements.txt
RUN pip install --no-cache-dir -r api/requirements.txt

# Copy API source
COPY api/src/ ./api/src/

WORKDIR /app/api

# Expose port
EXPOSE 8000

# Run API
CMD ["uvicorn", "src.main:app", "--host", "0.0.0.0", "--port", "8000"]
