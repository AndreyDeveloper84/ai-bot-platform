"""Production settings.

Hardening focuses on **fail-fast on boot**: any required env var missing
makes the WSGI worker explode before it can serve a single request with
a stale or empty value. Catch-block uses :class:`ImproperlyConfigured`
(NOT plain ``assert``) — Python invoked with ``-O`` strips asserts at
bytecode-compile time, which would silently disable the check.

Each fail-fast group ties back to the sprint that introduced the
dependency. New required env vars MUST add to the matching block.
"""

from __future__ import annotations

import os

from django.core.exceptions import ImproperlyConfigured

from .base import *  # noqa: F401,F403
from .base import (
    booking_via_ayla_rest_from,
    dev_bypass_enabled_from,
    payments_test_mode_from,
    secret_key_or_refuse,
)

DEBUG = False

# Mini App dev-bypass is DEAD here, and an environment that says otherwise is
# a misconfiguration worth a refused boot, not a silent override: the bypass
# lets a caller pick any BotUser + tenant by header, skipping init-data HMAC.
# (It also needs DEBUG=True, which is False above — the refusal is the
# second lock, so that one wrong line in an env file is noticed.)
if dev_bypass_enabled_from(os.environ.get("MINIAPP_DEV_BYPASS_ENABLED")):
    raise ImproperlyConfigured(
        "MINIAPP_DEV_BYPASS_ENABLED must not be enabled in production: it lets a caller "
        "skip Mini App init-data verification by header. Remove it from the environment."
    )
MINIAPP_DEV_BYPASS_ENABLED = False


# SECRET_KEY — the one every other secret rests on (sessions, CSRF, the
# master session signer, the HMAC of every encrypted field per ADR-0006).
# ``base.py`` falls back to a scaffold default that is public in this
# repository; a production worker that booted on it, or on the
# `.env.example` line, or on a `django-insecure-...` startproject key,
# would sign with a key anyone can read. Refused at import like the
# service tokens below. On a server with data the value is never rotated
# (DRF-2555) — this guard only refuses the known-bad values, it does not
# ask for a new one.
SECRET_KEY = secret_key_or_refuse(os.environ.get("DJANGO_SECRET_KEY"), contour="production")


# S3B (#1044) — Catalog sync now pulls Ayla's internal Bearer catalog
# (`/api/v1/internal/catalog/salon-services/`) with AYLA_INTERNAL_API_TOKEN.
# Missing token → sync silently 403s every 15 minutes and the CatalogService
# mirror drifts from source. Fail fast instead (re-points the retired
# MYSITE_CATALOG_SERVICE_TOKEN guard onto the Ayla s2s token).
#
# Reading os.environ directly (instead of importing the resolved symbol from
# .base) so test reloads of this module pick up the current env without also
# reloading base.py.
AYLA_INTERNAL_API_TOKEN = os.environ.get("AYLA_INTERNAL_API_TOKEN", "")
if not AYLA_INTERNAL_API_TOKEN:
    raise ImproperlyConfigured(
        "AYLA_INTERNAL_API_TOKEN is required in production. Set it to the "
        "shared service-to-service Bearer token Ayla validates (catalog sync "
        "+ the profile/recommendations/booking internal clients need it)."
    )


# Sprint 8 / E1 (DRF-710) — Sentry. Production cannot ship without
# error reporting wired; an unreported pipeline crash is invisible to
# on-call. Fail fast on boot — there's no "later" we can defer this to
# once traffic is on the platform.
SENTRY_DSN = os.environ.get("SENTRY_DSN", "")
if not SENTRY_DSN:
    raise ImproperlyConfigured(
        "SENTRY_DSN is required in production. Set it to the project's "
        "Sentry DSN. Local dev + tests run with empty DSN (no-op); "
        "production must report errors."
    )


# Sprint 7 / M4 (DRF-595) — ChromaDB authentication. ChromaDB stores
# every tenant's KB embeddings; an unauthenticated server lets anyone
# on the docker network read or wipe `tenant_<uuid>` collections. The
# `chromadb` container ships a Bearer-auth gate; the client must
# present a matching ``CHROMA_AUTH_TOKEN`` on every request. A missing
# value here would silently downgrade to anonymous and the FAQ skill
# would start serving 401s — fail fast on boot instead.
CHROMA_AUTH_TOKEN = os.environ.get("CHROMA_AUTH_TOKEN", "").strip()
if not CHROMA_AUTH_TOKEN:
    raise ImproperlyConfigured(
        "CHROMA_AUTH_TOKEN is required in production. "
        "Set it in the environment to a value matching the "
        "CHROMA_SERVER_AUTHN_CREDENTIALS configured on the ChromaDB "
        "container (see infra/README.md → 'ChromaDB Bearer auth')."
    )


# Sprint 10 / C3 (DRF-879) — mysite catalog webhook HMAC secret.
# Without this, the receiver's fail-closed signature check rejects
# every delivery and salons get 15-minute stale catalog state via the
# pull-side beat. Acceptable in dev/CI; production-broken silently. The
# secret must match mysite's outgoing webhook signing key (DRF-726).
MYSITE_WEBHOOK_HMAC_SECRET = os.environ.get("MYSITE_WEBHOOK_HMAC_SECRET", "")
if not MYSITE_WEBHOOK_HMAC_SECRET:
    raise ImproperlyConfigured(
        "MYSITE_WEBHOOK_HMAC_SECRET is required in production. "
        "Set it to the same shared secret configured on mysite "
        "(see Phase 1 / DRF-726). The receiver fails-closed when "
        "the secret is empty — every webhook delivery is rejected."
    )


# DRF-2340 — режим оплаты в бою называется явно. Умолчания здесь нет
# намеренно: до этой правки незаданная переменная означала «тест», то есть
# боевой контур молча выдавал бы людям заглушечные ссылки на оплату —
# «оплатил» без денег, и никакого сигнала об этом. Падение на загрузке —
# та же форма, что у AYLA_INTERNAL_API_TOKEN и SENTRY_DSN ниже: цена
# неверного ответа — выкладка, которая не стартует, а не человек с
# поддельной ссылкой.
_PAYMENTS_MODE_RAW = os.environ.get("AYLA_PAYMENTS_TEST_MODE")
if _PAYMENTS_MODE_RAW is None or not _PAYMENTS_MODE_RAW.strip():
    raise ImproperlyConfigured(
        "AYLA_PAYMENTS_TEST_MODE is required in production and has no default. "
        "Set it to 'false' to take real payments, or to 'true' deliberately "
        "(a contour that issues stub checkout links). Unset used to mean "
        "'true' silently — people would get a fake payment link."
    )
# Мусорное значение до этой строки не доходит: разбор общий, и ``base``
# читает ту же переменную при импорте — отказ приходит оттуда, с тем же
# именем в тексте. Здесь остаётся то, чего base знать не может: в бою у
# режима нет умолчания вообще.
AYLA_PAYMENTS_TEST_MODE = payments_test_mode_from(_PAYMENTS_MODE_RAW)


# DRF-2346 — путь записи в бою называется явно, умолчания здесь нет.
# Незаданная переменная означала «false», то есть местный двухшаговый путь
# отмены в контуре, где записи принадлежат Ayla: человек слышал «Запись
# отменена», а запись оставалась в состоянии «отмена запрошена», пока её не
# добьёт подметание, — и всё это при живой записи у источника. Та же цена
# неверного ответа, что у режима оплаты: выкладка, которая не стартует,
# дешевле человека, отменившего запись в никуда.
_BOOKING_PATH_RAW = os.environ.get("BOOKING_VIA_AYLA_REST")
if _BOOKING_PATH_RAW is None or not _BOOKING_PATH_RAW.strip():
    raise ImproperlyConfigured(
        "BOOKING_VIA_AYLA_REST is required in production and has no default. "
        "Set it to 'true' when bookings live in Ayla (ADR-0009), or to "
        "'false' deliberately for a contour on local tables. Unset used to "
        "mean 'false' silently."
    )
BOOKING_VIA_AYLA_REST = booking_via_ayla_rest_from(_BOOKING_PATH_RAW)


# Phase 2.2 — domain bus subscriber registry. Production activates
# AuditSubscriber by default so every dispatched DomainEvent gets
# mirrored into AuditLog (forensic chain-of-custody for billing
# disputes + 152-ФЗ compliance evidence). Operator can override by
# setting DOMAIN_EVENT_SUBSCRIBERS in the environment — the env value
# wins (base.py applies env first; this override only fires when env
# is silent).
#
# Rollback: set DOMAIN_EVENT_SUBSCRIBERS=apps.eventbus.dispatcher.NoopSubscriber
# in the deploy environment and restart workers. AuditLog stops growing
# from the bus; existing rows are untouched. See
# docs/runbooks/eventbus-subscriber-activation.md.
if not os.environ.get("DOMAIN_EVENT_SUBSCRIBERS"):
    DOMAIN_EVENT_SUBSCRIBERS = ["apps.eventbus.subscribers.AuditSubscriber"]
