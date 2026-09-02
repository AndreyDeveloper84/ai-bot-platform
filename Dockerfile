# ai-bot-platform — Sprint 0 dev image.
# Production hardening (multi-stage, non-root user, healthcheck) lands in Sprint 9.
#
# Critical lesson from DRF-355 (mysite incident): `git` MUST be installed
# before dependencies are resolved, because pyproject.toml pulls
# `ayla-ai-core@git+https://github.com/...` which clones via git.
#
# Critical lesson from DRF-1437: this image is built FROM uv.lock, and the
# build fails rather than ship an environment that is anything else.
#
# What it replaced and why. The image used to install with
#
#     pip install -e ".[dev,ai-core]" "psycopg[binary]>=3.2"
#
# which re-resolves every unpinned range on every rebuild and never opens
# uv.lock. CI installs with `uv sync --frozen`, i.e. from the lock. The two
# had been drifting apart silently for weeks; measured in
# ayla-bot-staging-worker-1 on 2026-09-01, against the same commit CI was
# green on:
#
#   * 78 packages at versions the lock never selected — seven a whole major
#     apart: anthropic 0.101.0 -> 1.2.0, cryptography 48 -> 50,
#     django-redis 6 -> 7, kubernetes 35 -> 36, protobuf 6 -> 7,
#     rpds-py 0.30 -> 2026.6.3, websockets 16 -> 17;
#   * 10 packages installed that appear in no lock at all — httpx2 and
#     httpcore2 among them, dragged in by the anthropic major.
#
# The anthropic major is the one that surfaced: it moved to `httpx2`, the
# proxied SDK client stopped constructing, and the bot answered nobody
# (DRF-1437). The other 77 were equally unverified — they had simply not
# been unlucky yet. Nobody chose any of it; a pinless range plus a rebuild
# chose it.
FROM python:3.12-slim AS dev

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    # The environment lives OUTSIDE /app on purpose. Every service in
    # docker-compose.staging.yml bind-mounts `./:/app`, so an /app/.venv
    # baked at build time is replaced by the host directory at run time and
    # simply vanishes. /opt/venv survives the mount.
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    # Use the interpreter this image already has. Without this, uv is free to
    # fetch a managed CPython of its own — a second, invisible way for the
    # runtime to stop being the thing that was built.
    UV_PYTHON_DOWNLOADS=never \
    UV_PYTHON=/usr/local/bin/python3.12 \
    # Hardlinks across the layer boundary are not available; copying is both
    # correct and quiet here.
    UV_LINK_MODE=copy \
    PATH="/opt/venv/bin:$PATH"

# OS deps:
#   git           → required for git+https deps (DRF-355 lesson)
#   libpq-dev     → psycopg build
#   build-essential → wheels that need compilation (chromadb-client, etc.)
#   curl          → healthchecks
RUN apt-get update && apt-get install -y --no-install-recommends \
        git \
        libpq-dev \
        build-essential \
        curl \
    && rm -rf /var/lib/apt/lists/*

# uv is pinned like everything else. An unpinned installer is the same
# defect one level up: a resolver whose behaviour can change under a
# rebuild nobody asked for.
RUN pip install --upgrade pip && pip install "uv==0.11.12"

WORKDIR /app

# Build-time secret for cloning private GitHub repos (ayla-ai-core).
# Pattern from beautygo_backend/Dockerfile: pass via
# `docker build --build-arg GH_DEPLOY_TOKEN=...`. The URL-rewrite makes
# git clone authenticate transparently. Consumed at build time only — not
# baked into the runtime image (empty default = public repos build fine).
ARG GH_DEPLOY_TOKEN=""
RUN if [ -n "$GH_DEPLOY_TOKEN" ]; then \
      git config --global url."https://${GH_DEPLOY_TOKEN}@github.com/".insteadOf "https://github.com/"; \
    fi

# Dependencies first, from the lock and nothing but the lock, in their own
# layer: a source-only change must not re-resolve or re-download anything.
#
# `--locked` rather than `--frozen`: --frozen installs the lock without
# looking at pyproject.toml, so a dependency added to pyproject and never
# re-locked would build green and deploy the old set. --locked asserts the
# two agree and fails the build if they do not. That assertion is the whole
# point of this file.
#
# README.md is copied because pyproject.toml declares `readme = "README.md"`
# and setuptools reads it while building project metadata.
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --locked --extra dev --extra ai-core --no-install-project

# Now the source, and the project itself on top of the already-resolved deps.
COPY . .
RUN uv sync --locked --extra dev --extra ai-core

# The build refuses to produce an image whose runtime environment is not the
# lock. `uv sync --locked` above already guarantees that for everything uv
# installed; this layer catches what uv did NOT install — anything added to
# this file later that reaches /opt/venv by another route. Verified both
# ways before shipping: a clean build passes it, and a build with
# `uv pip install --python /opt/venv/bin/python httpx2==2.12.0` spliced in
# fails here, naming httpx2, httpcore2, truststore and a bumped idna.
#
# A trap worth knowing, found while proving the above. Plain `pip` in this
# image is /usr/local/bin/pip — the BASE interpreter's pip, not this
# environment's. `RUN pip install x` therefore installs into
# /usr/local/lib/python3.12/site-packages, which /opt/venv/bin/python never
# reads: the install is silently inert, and this guard correctly stays
# green because the runtime environment did not change. To touch the
# runtime environment at all, go through the lock — that is the only
# supported way, and it is the point of this file.
#
# The same command audits an already-running container:
#   docker exec <container> python /app/tools/env_guard.py --against-lock
RUN python tools/env_guard.py --against-lock

EXPOSE 8000
CMD ["python", "manage.py", "runserver", "0.0.0.0:8000"]
