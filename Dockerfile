# syntax=docker/dockerfile:1

# Shared production image for the FastAPI API and gauntlet worker.
# Build: docker build -t dialect-data-factory:latest .
# Default CMD serves the API; Compose overrides command for worker/migrations.

ARG PYTHON_VERSION=3.12
ARG NODE_VERSION=22

FROM node:${NODE_VERSION}-bookworm-slim AS frontend-builder

WORKDIR /build/frontend/web

COPY frontend/web/package.json frontend/web/package-lock.json ./
RUN npm ci

COPY frontend/web/ ./
COPY contracts/api_types.py /build/contracts/api_types.py
COPY app/api/admin_tune.py /build/app/api/admin_tune.py
RUN npm run test:contract && npm run build

FROM ghcr.io/astral-sh/uv:python${PYTHON_VERSION}-bookworm-slim AS python-deps

WORKDIR /app

ENV UV_LINK_MODE=copy

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

FROM python:${PYTHON_VERSION}-slim-bookworm AS runtime

ARG BUILD_DATE
ARG VCS_REF
ARG VERSION=0.1.0

LABEL org.opencontainers.image.title="Dialect Data Factory" \
    org.opencontainers.image.description="Shared production image for FastAPI API and gauntlet worker" \
    org.opencontainers.image.version="${VERSION}" \
    org.opencontainers.image.created="${BUILD_DATE}" \
    org.opencontainers.image.revision="${VCS_REF}" \
    org.opencontainers.image.vendor="Dialect Data Factory" \
    com.neva.component="shared-production" \
    com.neva.python.version="${PYTHON_VERSION}"

WORKDIR /app

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/app/.venv/bin:${PATH}" \
    PYTHONPATH="/app" \
    DATA_DIR="/app/data"

RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg postgresql-client \
    && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

COPY --from=python-deps /app/.venv /app/.venv

COPY pyproject.toml uv.lock ./
COPY app/ app/
COPY worker/ worker/
COPY contracts/ contracts/
COPY scripts/ scripts/
COPY deckgen/ deckgen/

COPY --from=frontend-builder /build/frontend/web/dist frontend/web/dist

RUN groupadd --gid 1000 neva \
    && useradd --uid 1000 --gid neva --home-dir /home/neva --shell /usr/sbin/nologin neva \
    && mkdir -p /app/data/audio /app/data/decks /app/data/corpus \
    && chown -R neva:neva /app

USER neva

EXPOSE 8000

VOLUME ["/app/data"]

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
