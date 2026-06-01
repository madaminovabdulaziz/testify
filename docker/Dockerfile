# syntax=docker/dockerfile:1.7

# ---------- Stage 1: build dependencies ----------
FROM python:3.12-slim AS builder

WORKDIR /build

RUN pip install --no-cache-dir uv

COPY pyproject.toml ./
# uv.lock is optional during early development; copied if present.
COPY uv.loc[k] ./

RUN if [ -f uv.lock ]; then \
        uv export --no-dev --format requirements-txt > requirements.txt ; \
    else \
        uv pip compile pyproject.toml -o requirements.txt ; \
    fi && \
    pip install --no-cache-dir --target /deps -r requirements.txt

# ---------- Stage 2: runtime ----------
FROM python:3.12-slim

RUN useradd -m -u 1000 botuser && \
    apt-get update && \
    apt-get install -y --no-install-recommends curl && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY --from=builder /deps /deps

ENV PYTHONPATH=/deps:/app \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

COPY --chown=botuser:botuser app/ ./app/
COPY --chown=botuser:botuser alembic/ ./alembic/
COPY --chown=botuser:botuser alembic.ini ./

USER botuser

EXPOSE 8080

CMD ["python", "-m", "app.main"]
