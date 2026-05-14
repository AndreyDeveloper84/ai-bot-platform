"""Base Django settings shared across local / staging / production.

Sprint 0 / A1 keeps this minimal: enough to boot Django, run check + migrate,
and execute smoke tests. Settings expand sprint-by-sprint:
- Sprint 1: Celery + Redis + LLM circuit breaker
- Sprint 2: tenancy middleware, TenantContext
- Sprint 5: replay sampling, PII redaction
- Sprint 6: experiments, sticky bucketing
"""

from __future__ import annotations

import os
from pathlib import Path

from celery.schedules import crontab  # type: ignore[import-untyped]

BASE_DIR = Path(__file__).resolve().parent.parent.parent

SECRET_KEY = os.environ.get(
    "DJANGO_SECRET_KEY",
    "django-insecure-sprint0-scaffold-only-replace-before-staging",
)

DEBUG = os.environ.get("DJANGO_DEBUG", "False").lower() == "true"

ALLOWED_HOSTS: list[str] = os.environ.get("DJANGO_ALLOWED_HOSTS", "").split(",")
ALLOWED_HOSTS = [h.strip() for h in ALLOWED_HOSTS if h.strip()]

# 20 platform apps scaffolded in Sprint 0 + apps.persona added in Sprint 2 / E1.
# Each is empty (just AppConfig); models / views / migrations land
# sprint-by-sprint per docs/architecture.md.
LOCAL_APPS = [
    "apps.tenancy",
    "apps.identity",
    "apps.conversations",
    "apps.orchestrator",
    "apps.persona",
    "apps.skills",
    "apps.tools",
    "apps.kb",
    "apps.channels",
    "apps.ingress",
    "apps.workers",
    "apps.consent",  # Sprint 3 / A1 — ConsentRecord first-class
    # apps.handoff was scaffolded in Sprint 0; Sprint 3 / C1 lands AdminTask model.
    # apps.promptreg was scaffolded in Sprint 0; Sprint 4 / A1 lands PromptVersion model.
    # apps.experiments was scaffolded in Sprint 0; Sprint 4 / B1 lands Experiment + UserAssignment + Holdout.
    # apps.replay was scaffolded in Sprint 0; Sprint 5 / A1 lands ReplayTrace model.
    "apps.audit",
    "apps.events",
    "apps.experiments",
    "apps.voice",
    "apps.catalog",
    "apps.replay",
    "apps.promptreg",
    "apps.adminconsole",
    "apps.handoff",
    # Sprint 8 / T1 (DRF-705) — observability package owns OTel + Sentry + JSON logs.
    "apps.observability",
]

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "rest_framework",
    *LOCAL_APPS,
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    # Tenant context: resolves X-Tenant header → request.tenant + ContextVar.
    # Must sit after AuthenticationMiddleware (some auth paths inform tenant
    # resolution in later sprints). See ADR-0001 + ADR-0003.
    "apps.tenancy.middleware.TenantContextMiddleware",
]

# Multi-tenant scope enforcement. Tri-value:
#   audit  — default in prod/staging. Missing X-Tenant header → request.tenant=None,
#            audit-log the miss for analysis. Tolerant during rollout.
#   strict — fail-fast. Missing/unknown header on /api/v1/* (except /auth/*) → 400.
#            Used in tests via tests/conftest.py autouse fixture; flipped on in
#            prod after Sprint 8 shadow soak per ADR-0001.
#   off    — no tenant resolution. Reserved for environments with multi-tenancy
#            fully disabled (none today).
STRICT_TENANT_SCOPE = os.environ.get("STRICT_TENANT_SCOPE", "audit")

# Sprint 8 / F2 (DRF-731) — STRICT_TENANT_SCOPE post-flip monitor armed.
# Operator sets this to the ISO 8601 flip timestamp at the same moment
# they roll STRICT_TENANT_SCOPE=strict in /etc/ai-bot-platform/.env.
# apps.observability.tasks.monitor_post_flip_violations reads it; while
# the value is set AND less than 24h old, the task runs every 15 min
# (self-rescheduling) and pages on any tenant_scope_violation audit row.
# After 24h it auto-stops; operator clears the env var per the runbook.
STRICT_SCOPE_FLIP_AT = os.environ.get("STRICT_SCOPE_FLIP_AT", "")

# Audit-trail retention (per 6A-split decision in plan-eng-review 2026-05-11).
# Audit logs are forensic data — kept long; idempotency keys are short-lived.
# Different lifecycles, separate settings, separate cleanup tasks.
AUDIT_LOG_RETENTION_DAYS = int(os.environ.get("AUDIT_LOG_RETENTION_DAYS", "90"))
IDEMPOTENCY_KEY_RETENTION_DAYS = int(os.environ.get("IDEMPOTENCY_KEY_RETENTION_DAYS", "7"))

# Sprint 5 / A3 — Replay infrastructure config (PHASE0_DESIGN §7.1).
# Per-env sample rate so prod/staging stay at 100% (1 tenant, low traffic;
# ~30MB/30d retention) while tests default to 0 to avoid noisy row creation
# from unrelated test runs. Test code that exercises the recorder explicitly
# bumps `settings.REPLAY_SAMPLE_RATE_TEST = 1.0` in the test fixture.
REPLAY_SAMPLE_RATE_PROD = float(os.environ.get("REPLAY_SAMPLE_RATE_PROD", "1.0"))
REPLAY_SAMPLE_RATE_STAGING = float(os.environ.get("REPLAY_SAMPLE_RATE_STAGING", "1.0"))
REPLAY_SAMPLE_RATE_TEST = float(os.environ.get("REPLAY_SAMPLE_RATE_TEST", "0.0"))

# 30 days per design §7.1 expires_at; A4 cleanup task evicts past-expiry rows.
REPLAY_RETENTION_DAYS = int(os.environ.get("REPLAY_RETENTION_DAYS", "30"))

# Redaction allowlist — strings the regex layer should NOT replace. Useful for
# brand / master / service names that look like phones or emails. Comma-
# separated env var; B3 implements lookup.
REPLAY_REDACTION_ALLOWLIST: list[str] = [
    p.strip() for p in os.environ.get("REPLAY_REDACTION_ALLOWLIST", "").split(",") if p.strip()
]

# Sprint 3 / B4 — event fanout adapter registry. Each entry is the
# dotted import path of an :class:`apps.events.fanout.EventFanout`
# implementation. Default is the no-op adapter — Phase 0 keeps events
# in DB only. Phase 1 adds MixpanelFanout / GA4Fanout / WarehouseFanout.
EVENT_FANOUTS: list[str] = [
    p.strip()
    for p in os.environ.get("EVENT_FANOUTS", "apps.events.fanout.NoopFanout").split(",")
    if p.strip()
]

# Sprint 2 / C1 — short-term Redis memory window depth + TTL.
# Caller (apps/orchestrator/memory/short_term.py) reads these on every
# append; runtime-changeable via settings override in tests.
SHORT_TERM_MEMORY_DEPTH = int(os.environ.get("SHORT_TERM_MEMORY_DEPTH", "20"))
SHORT_TERM_MEMORY_TTL_SECONDS = int(os.environ.get("SHORT_TERM_MEMORY_TTL_SECONDS", str(24 * 3600)))

# Sprint 2 / D2 + D4 — MAX channel configuration.
MAX_API_BASE = os.environ.get("MAX_API_BASE", "https://botapi.max.ru")
MAX_BOT_TOKEN = os.environ.get("MAX_BOT_TOKEN", "")
MAX_WEBHOOK_SECRET = os.environ.get("MAX_WEBHOOK_SECRET", "")

# Sprint 9 / I1 (DRF-825) — Ayla nutrition backend.
# Empty defaults make the lazy singleton fail loudly on first use rather
# than silently 500ing on a misconfigured prod box.
AYLA_BASE_URL = os.environ.get("AYLA_BASE_URL", "")
AYLA_SERVICE_TOKEN = os.environ.get("AYLA_SERVICE_TOKEN", "")

# Sprint 2 / E2 — admin chat for breaker state-transition alerts.
# Empty (default) → telegram_alert is a no-op. Set to the operator's
# MAX chat id to receive 🚨 messages on breaker open/close.
ADMIN_MAX_CHAT_ID = os.environ.get("ADMIN_MAX_CHAT_ID", "")

# Sprint 2 / E3 — Celery broker + beat schedule for retention tasks.
# CELERY_BROKER_URL falls through to REDIS_URL so dev/prod share one
# Redis instance for both queue + cache + streams.
CELERY_BROKER_URL = os.environ.get(
    "CELERY_BROKER_URL",
    os.environ.get("REDIS_URL", "redis://localhost:6379/0"),
)
CELERY_RESULT_BACKEND = os.environ.get("CELERY_RESULT_BACKEND", "")
CELERY_TASK_ACKS_LATE = True
CELERY_TASK_REJECT_ON_WORKER_LOST = True
CELERY_WORKER_PREFETCH_MULTIPLIER = 1

# Beat schedule — keep retention tasks on separate cadences per the
# 6A-split rule (AuditLog 90d daily sweep vs IdempotencyKey 7d hourly
# sweep). Cron times in UTC; the Django Celery beat runs in
# `apps/audit` and `apps/tools` modules.
CELERY_BEAT_SCHEDULE = {
    "cleanup_old_audit_logs": {
        "task": "apps.audit.tasks.cleanup_old_audit_logs",
        # Daily 03:00 UTC — quiet hour for the formula-tela tenant.
        "schedule": crontab(hour="3", minute="0"),
    },
    "cleanup_old_idempotency_keys": {
        "task": "apps.tools.tasks.cleanup_old_idempotency_keys",
        # Hourly :15 — offset from on-the-hour spikes (webhook bursts,
        # cron jobs from other systems often fire at :00).
        "schedule": crontab(minute="15"),
    },
    "catalog_sync_every_15min": {
        # Sprint 7 / C5 (DRF-579) — fan-out catalog sync across every
        # active tenant. Cadence matched to apps.catalog.services.sync
        # advisory-lock TTL (1.5x). The task carries a 12-min soft time
        # limit so an overrun fires before the next beat fires a
        # parallel run.
        "task": "apps.catalog.tasks.sync_catalog_for_all_tenants",
        "schedule": crontab(minute="*/15"),
    },
    "cleanup_expired_replay_traces": {
        "task": "apps.replay.tasks.cleanup_expired_traces",
        # Daily 04:00 UTC — offset from the 03:00 audit cleanup so the
        # worker pool isn't slammed by both sweeps simultaneously.
        "schedule": crontab(hour="4", minute="0"),
    },
    "recompute_profiles_daily": {
        "task": "apps.identity.tasks.recompute_profiles_daily",
        # Daily 03:30 UTC — between the 03:00 audit cleanup and the
        # 04:00 replay cleanup; spike absorbed in tiers across the worker pool.
        "schedule": crontab(hour="3", minute="30"),
    },
    # Sprint 8 / S4 (DRF-719) — daily shadow-delta sweep.
    # 08:00 МСК = 05:00 UTC — runs AFTER the mysite CSV publisher's
    # 04:00 МСК export window so the ground-truth file is on disk.
    # Telegram digest at 09:00 МСК handled by the task itself (no separate
    # beat entry — keeps the delta + digest atomic per day per tenant).
    "compute_shadow_delta_daily": {
        "task": "apps.observability.tasks.compute_shadow_delta",
        "schedule": crontab(hour="5", minute="0"),
    },
}

# Sprint 7 / L7 (DRF-585) — Anthropic daily-token cost cap. Counter
# stored in Redis as `anthropic_tokens:<YYYY-MM-DD>` (TTL 24h, natural
# UTC-midnight rollover). On overrun the provider raises
# LLMProviderQuotaExceeded; the L5 router falls back to OpenAI.
ANTHROPIC_DAILY_TOKEN_CAP = int(os.environ.get("ANTHROPIC_DAILY_TOKEN_CAP", "1000000"))


# Sprint 1 / C1 channel token map. Format env CHANNEL_TOKEN_TO_TENANT_SLUG:
# ``"token1=tenant-a,token2=tenant-b"``. Sprint 4 replaces with
# encrypted-on-tenant lookup (ADR-0006).
CHANNEL_TOKEN_TO_TENANT_SLUG = os.environ.get("CHANNEL_TOKEN_TO_TENANT_SLUG", "")

# Sprint 8 / T1 (DRF-705) — OpenTelemetry configuration.
# Empty endpoint = no-op exporter (local dev + tests).
# Sample rate is Decision 3 from sprint-8-observability-shadow.md.
OTEL_EXPORTER_OTLP_ENDPOINT = os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT", "")
OTEL_TRACES_SAMPLE_RATE = float(os.environ.get("OTEL_TRACES_SAMPLE_RATE", "0.05"))
DEPLOYMENT_ENVIRONMENT = os.environ.get("DJANGO_ENV", "local")
SERVICE_VERSION = os.environ.get("SERVICE_VERSION", "0.0.0")

# Sprint 8 / E1 (DRF-710) — Sentry error reporting + PII scrubber.
# Empty DSN = no-op (local dev + tests never ship events upstream).
# Production fail-fast lives in config/settings/production.py.
SENTRY_DSN = os.environ.get("SENTRY_DSN", "")
SENTRY_ENVIRONMENT = os.environ.get("SENTRY_ENVIRONMENT", "local")
SENTRY_TRACES_SAMPLE_RATE = float(os.environ.get("SENTRY_TRACES_SAMPLE_RATE", "0.05"))

# Sprint 8 / S3 (DRF-718) — Shadow-mode ground-truth source.
# mysite exports its Telegram conversation logs as `<YYYY-MM-DD>.csv`
# files in this directory; the daily delta task reads them. Empty path =
# delta math returns the no-ground-truth summary (Sprint 9 hardens).
SHADOW_GROUND_TRUTH_PATH = os.environ.get("SHADOW_GROUND_TRUTH_PATH", "")

# Sprint 7 / M4 (DRF-595) — ChromaDB authentication.
# Sprint 7 ships the ChromaDB server behind a static Bearer token. The
# `chromadb` service in docker-compose mounts `CHROMA_SERVER_AUTHN_*`
# env vars and refuses unauthenticated requests with 401. Platform-side
# the token is read into ``CHROMA_AUTH_TOKEN`` and threaded into
# :func:`apps.kb.chromadb_client._build_chromadb_client`.
#
# Empty default keeps local dev / tests working (PersistentClient
# bypasses auth entirely; HttpClient with empty token connects
# unauthenticated against a same-network container). Production is the
# only environment that must have a non-empty value — enforced in
# :mod:`config.settings.production`.
CHROMA_HTTP_HOST = os.environ.get("CHROMA_HTTP_HOST", "")
CHROMA_HTTP_PORT = int(os.environ.get("CHROMA_HTTP_PORT", "8001"))
CHROMA_AUTH_TOKEN = os.environ.get("CHROMA_AUTH_TOKEN", "")

# Sprint 7 / C8 (DRF-578) — Catalog sync (mysite → platform mirror).
# Sync service (C4 / DRF-575) pulls Service/Master/FAQ/HelpArticle from
# `mysite/api/v1/catalog/*` via HTTP and upserts into apps.catalog
# mirrors. Per-tenant Redis advisory lock guards against concurrent
# runs; the TTL is intentionally ≥ 1.5× the 15-minute beat cadence so
# a slow run can't race itself.
MYSITE_CATALOG_BASE_URL = os.environ.get("MYSITE_CATALOG_BASE_URL", "https://formulatela58.ru")
MYSITE_CATALOG_SERVICE_TOKEN = os.environ.get("MYSITE_CATALOG_SERVICE_TOKEN", "")
CATALOG_SYNC_LOCK_TTL_SECONDS = int(os.environ.get("CATALOG_SYNC_LOCK_TTL_SECONDS", str(25 * 60)))
CATALOG_SYNC_HTTP_TIMEOUT = int(os.environ.get("CATALOG_SYNC_HTTP_TIMEOUT", "30"))
CATALOG_SYNC_HTTP_RETRIES = int(os.environ.get("CATALOG_SYNC_HTTP_RETRIES", "3"))

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"
ASGI_APPLICATION = "config.asgi.application"

# Database routing:
#   - When POSTGRES_HOST is set (docker compose / staging / prod) → Postgres.
#   - Otherwise SQLite for fast local boot without docker.
# Full DATABASE_URL parsing lands in Sprint 9 (production hardening).
if os.environ.get("POSTGRES_HOST"):
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.postgresql",
            "NAME": os.environ.get("POSTGRES_DB", "ai_bot_platform"),
            "USER": os.environ.get("POSTGRES_USER", "platform"),
            "PASSWORD": os.environ.get("POSTGRES_PASSWORD", "platform"),
            "HOST": os.environ["POSTGRES_HOST"],
            "PORT": os.environ.get("POSTGRES_PORT", "5432"),
        }
    }
else:
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": str(BASE_DIR / "db.sqlite3"),
        }
    }

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "ru"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
