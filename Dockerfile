# ai-bot-platform — Sprint 0 dev image.
# Production hardening (multi-stage, non-root user, healthcheck) lands in Sprint 9.
#
# Critical lesson from DRF-355 (mysite incident): `git` MUST be installed
# before `pip install`, because pyproject.toml pulls
# `ayla-ai-core@git+https://github.com/...@v0.6.0` which clones via git.
FROM python:3.12-slim AS dev

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# OS deps:
#   git           → required for git+https deps (DRF-355 lesson)
#   libpq-dev     → psycopg2 build
#   build-essential → wheels that need compilation (chromadb-client, etc.)
#   curl          → healthchecks
RUN apt-get update && apt-get install -y --no-install-recommends \
        git \
        libpq-dev \
        build-essential \
        curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY pyproject.toml ./
RUN pip install --upgrade pip && \
    pip install -e ".[dev]" "psycopg[binary]>=3.2"

COPY . .

EXPOSE 8000
CMD ["python", "manage.py", "runserver", "0.0.0.0:8000"]
