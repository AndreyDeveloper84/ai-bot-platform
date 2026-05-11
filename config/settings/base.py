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

BASE_DIR = Path(__file__).resolve().parent.parent.parent

SECRET_KEY = os.environ.get(
    "DJANGO_SECRET_KEY",
    "django-insecure-sprint0-scaffold-only-replace-before-staging",
)

DEBUG = os.environ.get("DJANGO_DEBUG", "False").lower() == "true"

ALLOWED_HOSTS: list[str] = os.environ.get("DJANGO_ALLOWED_HOSTS", "").split(",")
ALLOWED_HOSTS = [h.strip() for h in ALLOWED_HOSTS if h.strip()]

# 20 platform apps scaffolded in Sprint 0. Each is empty (just AppConfig);
# models / views / migrations land sprint-by-sprint per docs/architecture.md.
LOCAL_APPS = [
    "apps.tenancy",
    "apps.identity",
    "apps.conversations",
    "apps.orchestrator",
    "apps.skills",
    "apps.tools",
    "apps.kb",
    "apps.channels",
    "apps.ingress",
    "apps.workers",
    "apps.consent",
    "apps.audit",
    "apps.events",
    "apps.experiments",
    "apps.voice",
    "apps.catalog",
    "apps.replay",
    "apps.promptreg",
    "apps.adminconsole",
    "apps.handoff",
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

# Audit-trail retention (per 6A-split decision in plan-eng-review 2026-05-11).
# Audit logs are forensic data — kept long; idempotency keys are short-lived.
# Different lifecycles, separate settings, separate cleanup tasks.
AUDIT_LOG_RETENTION_DAYS = int(os.environ.get("AUDIT_LOG_RETENTION_DAYS", "90"))

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
