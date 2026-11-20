# ─────────────────────────────────────────────────────────────
# Kamal Express AI Platform — Production Dockerfile
# ─────────────────────────────────────────────────────────────
FROM python:3.11-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# System utilities and shared libraries required by headless Chromium / Playwright
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    git \
    wget \
    ca-certificates \
    libnss3 \
    libnspr4 \
    libatk1.0-0 \
    libatk-bridge2.0-0 \
    libcups2 \
    libdrm2 \
    libxkbcommon0 \
    libxcomposite1 \
    libxdamage1 \
    libxfixes3 \
    libxrandr2 \
    libgbm1 \
    libasound2 \
    libpango-1.0-0 \
    libcairo2 \
    fonts-liberation \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Install Playwright Chromium browser binary (system dependencies installed above)
RUN python -m playwright install chromium

# Copy application source code
COPY . .

# Set Python path so /app modules are always discoverable
ENV PYTHONPATH=/app

# Ensure data and cache directories exist
RUN mkdir -p data browser_profiles rag/documents rag/chroma_db

EXPOSE 8000

CMD ["uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8000"]
