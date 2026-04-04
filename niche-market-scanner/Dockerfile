FROM python:3.11-slim
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc libffi-dev && rm -rf /var/lib/apt/lists/*
COPY pyproject.toml .
COPY src/ src/
RUN pip install --no-cache-dir ".[dev]"
COPY config/ config/
VOLUME /app/data
ENV KALSHI_ENV=demo
ENTRYPOINT ["python", "-m", "niche_scanner.main"]
