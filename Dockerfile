# ─────────────────────────────────────────────────────────────
# Kamal Express AI Platform — Production Dockerfile
# ─────────────────────────────────────────────────────────────
FROM python:3.11-slim

# System utilities for healthchecks & network operations
RUN apt-get update && apt-get install -y \
    curl \
    git \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Install Playwright browser engine & system dependencies (headless mode support)
RUN playwright install --with-deps chromium

# Copy application source code
COPY . .

# Ensure data and cache directories exist
RUN mkdir -p data browser_profiles rag/documents rag/chroma_db

EXPOSE 8080

CMD ["uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8080"]
