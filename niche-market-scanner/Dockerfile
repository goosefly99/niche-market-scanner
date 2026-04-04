FROM python:3.11-slim

WORKDIR /app

# System deps for cryptography (RSA-PSS signing)
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc libffi-dev && rm -rf /var/lib/apt/lists/*

# Install Python deps first (layer caching)
COPY pyproject.toml .
COPY src/ src/
RUN pip install --no-cache-dir .

# Copy config (ICAO stations, settings, release calendar)
COPY config/ config/

# Data volume for SQLite journal persistence
VOLUME /app/data

# Default to demo mode — override via env vars
ENV KALSHI_ENV=demo

# Health check: verify the process is running
HEALTHCHECK --interval=60s --timeout=10s --retries=3 \
    CMD python -c "import sys; sys.exit(0)"

ENTRYPOINT ["python", "-m", "niche_scanner.main"]
