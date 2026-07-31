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

DEBUG = False


# Sprint 7 / C8 (DRF-578) — Catalog sync requires a service-token to
# auth against `mysite/api/v1/catalog/*`. Missing token → sync silently
# 401s every 15 minutes, the catalog mirrors drift from source, and the
# FAQ skill (DRF-589) starts retrieving stale chunks. Fail fast instead.
#
# Reading os.environ directly (instead of importing the resolved
# `MYSITE_CATALOG_SERVICE_TOKEN` symbol from .base) so test reloads of
# this module pick up the current env without also reloading base.py.
MYSITE_CATALOG_SERVICE_TOKEN = os.environ.get("MYSITE_CATALOG_SERVICE_TOKEN", "")
if not MYSITE_CATALOG_SERVICE_TOKEN:
    raise ImproperlyConfigured(
        "MYSITE_CATALOG_SERVICE_TOKEN is required in production. "
        "Set it in the environment (matching the token configured on "
        "mysite via M2 / DRF-593)."
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
CHROMA_AUTH_TOKEN = os.environ.get("CHROMA_AUTH_TOKEN", "")
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


# W0-B3 — events ingest HMAC secret. The ingest endpoint
# (``POST /api/v1/internal/events/ingest``) is unconditionally routed
# (config/urls.py) and every consumer family registers at app-ready
# (apps/eventbus/apps.py) — the ingest feature is structurally ALWAYS
# enabled in any booted deploy. The codebase has no off-switch setting
# for it and W0-B3 deliberately does not invent one: a flag here would
# gate only this validation while the endpoint stays live regardless.
# With an empty secret the receiver fails closed (401 ``no_secret`` on
# every delivery, apps/eventbus/ingest_security.py) — i.e. silent 100%
# event loss that only surfaces as Ayla-side retry exhaustion. Fail
# fast on boot instead. Absence outside strict production does not
# block startup (base.py keeps the empty default).
EVENT_INGEST_HMAC_SECRET = os.environ.get("EVENT_INGEST_HMAC_SECRET", "")
if not EVENT_INGEST_HMAC_SECRET:
    raise ImproperlyConfigured(
        "EVENT_INGEST_HMAC_SECRET is required in production. The Ayla "
        "events ingest endpoint is unconditionally routed "
        "(config/urls.py) and fails closed without the secret — every "
        "delivery would be rejected with 401 no_secret. Set it to the "
        "shared secret configured on Ayla (event-contract.md §6.2; "
        "quarterly rotation)."
    )


# W0-B3 — LLM provider selection. Supported provider names mirror the
# router registry (``_PROVIDER_NAMES`` in apps/llm/router.py); the
# registry is deliberately NOT imported here — settings load must not
# import apps. An unsupported org-wide default previously fell through
# to OpenAI with only a log warning; in production that's a silent
# vendor misroute, so fail fast. Non-production keeps the router's
# warn-and-fallback behaviour unchanged.
_LLM_SUPPORTED_PROVIDERS = ("openai", "anthropic")  # apps/llm/router.py::_PROVIDER_NAMES
LLM_PROVIDER = os.environ.get("LLM_PROVIDER", "openai").strip().lower() or "openai"
if LLM_PROVIDER not in _LLM_SUPPORTED_PROVIDERS:
    raise ImproperlyConfigured(
        f"LLM_PROVIDER={LLM_PROVIDER!r} is not a supported provider. "
        f"Supported: {', '.join(_LLM_SUPPORTED_PROVIDERS)} "
        "(see apps/llm/router.py::_PROVIDER_NAMES)."
    )


# W0-B3 — Anthropic credential requirement. ANTHROPIC_API_KEY is
# required only when an existing CONFIGURED provider path selects
# anthropic: the org-wide default (LLM_PROVIDER) or any per-skill
# override (SKILL_LLM_PROVIDER, e.g. {"intent": "anthropic"}). The
# router's tier-1 per-tenant override lives in the Tenant.features DB
# column and cannot be boot-validated. Presence check only — no API
# call, no credential verification, and the value is never logged.
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
_skill_llm_provider = globals().get("SKILL_LLM_PROVIDER", {}) or {}
_anthropic_routed = LLM_PROVIDER == "anthropic" or any(
    isinstance(choice, str) and choice.strip().lower() == "anthropic"
    for choice in _skill_llm_provider.values()
)
if _anthropic_routed and not ANTHROPIC_API_KEY:
    raise ImproperlyConfigured(
        "ANTHROPIC_API_KEY is required in production: a configured "
        "provider path selects anthropic (LLM_PROVIDER or "
        "SKILL_LLM_PROVIDER). Set ANTHROPIC_API_KEY in the environment "
        "or route those paths back to openai."
    )
