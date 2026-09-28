# ─────────────────────────────────────────────────────────────
# Kamal Express Agents — Dockerfile
#
# Intentionally lightweight — NO bundled browser.
# Browser automation connects to Chrome running on the HOST
# machine via CDP (Chrome DevTools Protocol).
# See: BROWSER_MODE=cdp / BROWSER_CDP_URL in .env
# ─────────────────────────────────────────────────────────────
FROM python:3.11-slim

RUN apt-get update && apt-get install -y curl && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .
RUN mkdir -p browser_profiles rag/documents rag/chroma_db

EXPOSE 8000

CMD ["uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8000", "--reload"]
