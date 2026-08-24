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
from django.core.exceptions import ImproperlyConfigured

# T-02 — strict parsers for the pilot ingest allowlists (see the
# EVENT_INGEST_ALLOWED_* block below). Imported under private aliases so
# they don't leak into the settings namespace as pseudo-settings; the
# module is stdlib-only, so importing it here is settings-load-safe.
from apps.eventbus.ingest_allowlist import (
    AllowlistConfigurationError as _IngestAllowlistConfigurationError,
)
from apps.eventbus.ingest_allowlist import (
    parse_event_allowlist as _parse_ingest_event_allowlist,
)
from apps.eventbus.ingest_allowlist import (
    parse_tenant_allowlist as _parse_ingest_tenant_allowlist,
)

# DRF-1061 multi-bot registry (MAX_BOT_REGISTRY block below). Same private-alias
# convention and the same settings-load-safety property: apps/channels/__init__.py
# is empty and bot_registry is stdlib-only (hmac, re, dataclasses).
from apps.channels.bot_registry import (
    BotRegistryConfigurationError as _BotRegistryConfigurationError,
)
from apps.channels.bot_registry import parse_registry as _parse_bot_registry
from apps.channels.bot_registry import with_legacy_fallback as _bot_registry_with_legacy

# DRF-1023 — strict parser for the CSRF_TRUSTED_ORIGINS wiring (see the
# web-security block below). Imported under private aliases so they don't
# leak into the settings namespace as pseudo-settings; the module is
# stdlib-only, so importing it here is settings-load-safe.
from config.security import (
    OriginConfigurationError as _OriginConfigurationError,
)
from config.security import (
    parse_trusted_origins as _parse_trusted_origins,
)

BASE_DIR = Path(__file__).resolve().parent.parent.parent

SECRET_KEY = os.environ.get(
    "DJANGO_SECRET_KEY",
    "django-insecure-sprint0-scaffold-only-replace-before-staging",
)

DEBUG = os.environ.get("DJANGO_DEBUG", "False").lower() == "true"

ALLOWED_HOSTS: list[str] = os.environ.get("DJANGO_ALLOWED_HOSTS", "").split(",")
ALLOWED_HOSTS = [h.strip() for h in ALLOWED_HOSTS if h.strip()]

# ---------------------------------------------------------------------------
# DRF-1023 — web security block (admin login fix + pilot HTTPS awareness).
#
# Until DRF-1023 the project declared NO security directives at all. The
# admin login at https://api-dev.gobeauty.site/admin/ answered «Ошибка
# проверки CSRF» to everyone: the contour terminates TLS at nginx, Django
# saw plain HTTP with an empty CSRF_TRUSTED_ORIGINS and rejected every
# POST. This block wires the Django-core security settings to the
# environment with unchanged-by-default values; HTTPS contours opt in
# explicitly via the environment (pilot: .env.staging).
#
# DJANGO_CSRF_TRUSTED_ORIGINS — CSV of origins allowed to POST over HTTPS
#   (admin login/session forms). Empty default = behaviour unchanged.
#   Strict parsing (config/security.py): a malformed value raises
#   ImproperlyConfigured at settings load — the same fail-safe as the
#   T-02 / DRF-1005 allowlists. A half-parsed origin list is a login that
#   "should work" and 403s, or worse one that trusts something unintended.
try:
    CSRF_TRUSTED_ORIGINS = _parse_trusted_origins(os.environ.get("DJANGO_CSRF_TRUSTED_ORIGINS", ""))
except _OriginConfigurationError as exc:
    raise ImproperlyConfigured(f"Invalid CSRF trusted-origins configuration: {exc}") from exc

# DJANGO_BEHIND_TLS_PROXY=true tells Django the contour terminates TLS at
# the front proxy (infra/nginx/ai-bot-platform-api.conf.template sets
# ``X-Forwarded-Proto $scheme``): request.is_secure() then reflects the
# client-facing scheme, which is what makes CSRF origin checking apply to
# admin POSTs and Secure cookies behave correctly. A boolean rather than a
# free-form tuple env: the header name is pinned to the one nginx actually
# sends, so there is no misconfiguration axis. Default off = behaviour
# unchanged (direct deployments, local dev).
if os.environ.get("DJANGO_BEHIND_TLS_PROXY", "false").lower() == "true":
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")

# Secure cookies: browsers then only send the session/CSRF cookies over
# HTTPS. Off by default (local dev has no TLS); HTTPS contours set both
# true. Only meaningful together with DJANGO_BEHIND_TLS_PROXY.
SESSION_COOKIE_SECURE = os.environ.get("DJANGO_SESSION_COOKIE_SECURE", "false").lower() == "true"
CSRF_COOKIE_SECURE = os.environ.get("DJANGO_CSRF_COOKIE_SECURE", "false").lower() == "true"

# SECURE_SSL_REDIRECT is deliberately NOT wired to the env: nginx already
# 301s 80 → 443, and a Django-level redirect would also 301 the
# container's own healthcheck (docker-compose.staging.yml curls
# http://localhost:8000/healthz/ with no X-Forwarded-Proto), flipping the
# container unhealthy. Enabling it requires teaching the probe about TLS
# first — out of DRF-1023 scope.
# ---------------------------------------------------------------------------

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
    "apps.marketplace",
    "apps.replay",
    "apps.promptreg",
    "apps.adminconsole",
    "apps.handoff",
    # Sprint 8 / T1 (DRF-705) — observability package owns OTel + Sentry + JSON logs.
    "apps.observability",
    # Phase 1 / B2 (DRF-838) — booking persistence (BookingRequest +
    # BookingReminder) bundled with the YClients admin-webhook port.
    # Skill / tool layer (LLM-callable booking ops) lands in B3 / DRF-839.
    "apps.booking",
    # Phase 1 / R1 (DRF-844) — reminder system: factory + periodic
    # dispatcher + cb:rem:* callback skill. Consumes apps.booking models;
    # owns the reminder lifecycle code paths.
    "apps.bookings",
    # Phase 1 / B6 (DRF-842) — promo codes + calc_price LLM tool.
    # Owns the Promotion model + promo-validation service; the
    # ``calc_price`` tool wiring lives in apps.skills.booking.
    "apps.promotions",
    # #427+#428 stub — Order + PaymentEvent tables retired in
    # migration 0002. App entry stays in INSTALLED_APPS so Django's
    # migration history applies cleanly on deploy; a future cleanup
    # PR removes this entry + the directory. Payment lifecycle now
    # lives in Ayla djangoproject per ADR-0009 §Domain ownership.
    # The bot-facing ``buy_certificate`` LLM tool talks to Ayla via
    # apps.integrations.ayla_payments.
    "apps.orders",
    # Customer Mini App Phase 0a — master schedule + slot resolver.
    "apps.scheduling",
    # Master Mini App M7 (Bundle B / item 3) — per-master notification
    # toggles + quiet-hours window. See
    # ``docs/design/handoffs/2026-05-18-master-mobile-handoff.md`` §M7.
    "apps.notifications",
    # Customer Mini App Phase 0b — HTTP API for the MAX Mini App webview.
    "apps.miniapp_api",
    # Master Mini App PR 1 (M0 onboarding) — claim-invite flow + BotUser
    # linkage + session-token issuance. See
    # ``docs/design/handoffs/2026-05-18-master-mobile-handoff.md`` §M0.
    "apps.master_api",
    # Admin REST API PR 2 — master roster CRUD (MM1 list + MM3 detail/edit).
    # See ``docs/design/handoffs/2026-05-18-master-management-handoff.md``.
    # Distinct from apps.adminconsole (which is reserved for Django admin
    # chrome). PR 2 owns the REST surface that the Ayla Pro web dashboard
    # + admin Mini App consume.
    "apps.admin_api",
    # 2026-05-19 — domain event bus (Postgres outbox per Q-EV-IMPL3).
    # Distinct from apps.events (analytics, snake_case, sync fanout):
    # apps.eventbus carries dot.notation domain events per
    # docs/design/policies/event-taxonomy.md §3 catalog. Two-bus
    # architecture by design — see memory two-bus-event-architecture.
    "apps.eventbus",
    # Loyalty (Volna 4) — points tracking. Phase 1.a ships the data
    # layer + LoyaltySubscriber listening to booking.completed.
    # Tiers / redemption flow / referrals / config UI deferred.
    # Subscriber activates by adding apps.loyalty.subscribers.LoyaltySubscriber
    # to DOMAIN_EVENT_SUBSCRIBERS env var.
    "apps.loyalty",
    # Master-Admin internal chat — PR 6 (production blocker for
    # earnings disputes, leave requests, review concerns, substitution,
    # offboarding). See ``docs/design/handoffs/2026-05-19-master-admin-internal-chat-handoff.md``.
    # Data layer + basic CRUD; SLA beat, PII scan, founder flow,
    # frontend Mini-App tabs all in follow-up PRs.
    "apps.internal_chat",
    # DRF-1285 - proactive nutrition layer: exactly two bot-initiated
    # messages (daily report, water reminder) plus the chat off-switch.
    # No models, so no migrations; per-user preferences live in
    # ``BotUser.context["nutrition_proactive"]``. Both beat tasks no-op
    # while ``NUTRITION_PROACTIVE_ENABLED`` is False (the default).
    "apps.nutrition_proactive",
]

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "rest_framework",
    # Celery beat scheduler tables — required when systemd beat runs
    # with `--scheduler django_celery_beat.schedulers:DatabaseScheduler`
    # (infra/systemd/ai-bot-platform-beat.service.template). Without
    # this entry the beat process crashes on import of SolarSchedule.
    "django_celery_beat",
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

# Tenancy retro B4 (2026-05-21) — STRICT_TENANT_REFUSE gates the
# ``TenantAwareTask`` ``requires_tenant`` enforcement.
#
#   False — log-only. Missing tenant on a requires_tenant=True handler
#           logs ERROR but proceeds with ``tenant_scope(None)``. Same
#           pre-B4 behaviour, but loud. Default during Phase 0 rollout
#           soak — same pattern as STRICT_TENANT_SCOPE shadow window.
#   True  — refuse-dispatch. Missing tenant raises
#           TenantRequiredButMissing → consumer.py treats it like any
#           handler exception (no XACK; entry stays in the PEL for
#           manual escalation). **No automatic DLQ retry is wired**
#           — XAUTOCLAIM reaper is a follow-up; PEL retention is the
#           current contract.
#
# Flip to True only after the dev-side soak shows zero
# ``worker.tenant_required_missing`` events in audit for a clean
# week (mirrors the Sprint 8 STRICT_TENANT_SCOPE flip cadence).
#
# **WORKER RESTART REQUIRED ON FLIP.** This value is read from
# ``os.environ`` exactly once, here, at module import time. After
# that, ``settings.STRICT_TENANT_REFUSE`` is a static attribute on
# the settings module. The runtime read in ``apps.workers.base``
# returns the import-frozen value, NOT the live env var. Operator
# flip sequence is documented in
# ``docs/runbooks/strict-tenant-refuse-flip.md``.
STRICT_TENANT_REFUSE = os.environ.get("STRICT_TENANT_REFUSE", "false").lower() == "true"

# Issue #500 (D-2 operator-side ceilings): max
# ``worker.tenant_required_missing`` audit emits per (handler, hour).
# Default 100 keeps audit-table growth bounded even when a misbehaving
# ingress pushes 1000+ entries/hour with empty ``resolved_tenant_id``.
# Set to 0 to disable the ceiling entirely (every emit fires — escape
# hatch for diagnostic windows).
WORKER_TENANT_MISSING_RATE_LIMIT = int(os.environ.get("WORKER_TENANT_MISSING_RATE_LIMIT", "100"))

# Tenancy retro B4 — post-flip monitor (mirrors STRICT_SCOPE_FLIP_AT
# pattern from Sprint 8 / F2). Operator sets this to the ISO 8601 flip
# timestamp at the same moment they roll STRICT_TENANT_REFUSE=true in
# /etc/ai-bot-platform/.env. A future observability task (NOT shipped
# in this PR) can read this and page on any
# ``worker.tenant_required_missing`` event in the 24h post-flip
# window. Runbook: docs/runbooks/strict-tenant-refuse-flip.md (TBD).
STRICT_TENANT_REFUSE_FLIP_AT = os.environ.get("STRICT_TENANT_REFUSE_FLIP_AT", "")

# Issue #499 — XAUTOCLAIM-based PEL reaper.
#
# Drains the Redis Streams Pending Entries List (PEL) for every
# registered ``ingress:*`` stream by claiming entries idle past
# PEL_REAPER_IDLE_SECONDS via XAUTOCLAIM, classifying them, and
# routing terminal entries to ``<stream>:dlq`` for operator triage.
# See ``apps/workers/reaper.py`` + ``docs/runbooks/strict-tenant-refuse-flip.md``.
#
# Opt-in. The Celery beat task ``apps.workers.tasks.reap_pel`` is
# scheduled below but no-ops while disabled — adding the schedule
# entry is safe before flip.
PEL_REAPER_ENABLED = os.environ.get("PEL_REAPER_ENABLED", "false").lower() == "true"

# Minimum idle time before an entry is eligible for reaping.
# Default 1h — long enough that a slow handler still in-flight on a
# real workload isn't reaped out from under itself, short enough that
# a strict-mode B4 refusal doesn't accumulate for a full day before
# being moved to DLQ. Tunable per-env via env var.
PEL_REAPER_IDLE_SECONDS = int(os.environ.get("PEL_REAPER_IDLE_SECONDS", "3600"))

# Max entries claimed per beat tick (caps work per fire). Real Redis
# handles much higher batches, but bounded here so a single tick can't
# DoS the audit pipeline if 100K entries are stuck.
PEL_REAPER_BATCH_SIZE = int(os.environ.get("PEL_REAPER_BATCH_SIZE", "100"))

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

# Phase 1 / PI1 (DRF-851) — AuditLog retention sweep mode.
#
#   "hard"  — DELETE rows past the cutoff. Original behaviour;
#             default to keep existing deployments backwards-compatible.
#   "soft"  — UPDATE rows to is_archived=True + archived_at=now.
#             Recommended for prod going forward; a future task can
#             then hard-delete archived rows past a longer cutoff
#             (out of scope for this PR — TODO in apps/audit/tasks.py).
#
# Trade-off: "soft" keeps a forensic trail of "what was retired and
# when" at the cost of disk + index size. "hard" reclaims the disk
# but loses the row entirely. For a single-tenant Phase 1 deployment
# either is fine; we recommend operators flip this to "soft" once
# disk usage is observed and bounded.
AUDIT_LOG_RETENTION_MODE = os.environ.get("AUDIT_LOG_RETENTION_MODE", "hard")

# Phase 1 / PI1 (DRF-851) — PaymentEvent retention.
# Webhook idempotency ledger entries. Hard-delete only — these are
# dedup tokens, not forensic data (Order carries the forensic trail).
PAYMENT_EVENT_RETENTION_DAYS = int(os.environ.get("PAYMENT_EVENT_RETENTION_DAYS", "90"))

# #443 — payment.failed consumer threshold. After N consecutive failures
# without an intervening capture, the consumer emits
# ``payment_failed_skill_triggered`` (separate PR for the skill itself).
# Env-driven so founder can dial without a code change. Bumping mid-flight
# is forward-compatible: counter is per-Conversation, the next failure
# event re-evaluates against the new threshold.
PAYMENT_FAILED_HANDOFF_THRESHOLD = int(os.environ.get("PAYMENT_FAILED_HANDOFF_THRESHOLD", "3"))

# Tier-A #4 (P1 PRE_PILOT, founder pilot_scope_discipline sequence #5).
#
# AI safety formalization: skills что compute а ``confidence`` score
# (FAQ on RAG retrieval, future LLM-driven flows) trigger handoff к
# human operator when confidence falls below a threshold. Pipeline
# step 10.5 enforces this as **defense-in-depth** — even if the skill
# forgot к set ``should_handoff=True``, the pipeline catches low
# confidence и transitions к handoff automatically.
#
# Global default (``AI_CONFIDENCE_HANDOFF_THRESHOLD``) applies к any
# skill that doesn't have a per-skill override. Per-skill dict
# (``SKILL_CONFIDENCE_HANDOFF_THRESHOLD``) lets ops tune individual
# skills без code change — analogous to ``SKILL_LLM_PROVIDER`` pattern.
# Set к ``None`` per skill (or omit) → disable enforcement for that
# skill (skill remains owning the decision via ``should_handoff``).
#
# Skill confidence semantics (locked in ``apps.skills.base.SkillResult``
# docstring 2026-05-27): scale ``[0.0, 1.0]``; ``None`` = skill didn't
# compute (Sprint 3 deterministic skills); ``1.0`` = full confidence
# (tool success); ``< threshold`` → pipeline auto-handoffs.
AI_CONFIDENCE_HANDOFF_THRESHOLD = float(os.environ.get("AI_CONFIDENCE_HANDOFF_THRESHOLD", "0.5"))
# Per-skill override dict. Key = skill ``name`` attribute (e.g. ``"faq"``,
# ``"booking"``). Value = threshold float OR ``None`` к disable. Skills
# не listed here fall back к ``AI_CONFIDENCE_HANDOFF_THRESHOLD``.
# Env-driven not supported для dict; set in deployment-specific
# settings module if per-skill tuning needed.
SKILL_CONFIDENCE_HANDOFF_THRESHOLD: dict[str, float | None] = {}

# #433 umbrella — HANDLER_EXCEPTION → DLQ threshold. A handler that
# raises gets retried by Ayla per §6.3; after this many failed
# attempts (counted per event_id + handler), bot-platform upserts a
# DLQ row with reason="handler_exception" so operator triage has a
# DB-level handle instead of digging through log aggregator. Below
# threshold = silent retries (current behaviour); at-or-above =
# operator-visible row. Env-driven so ops can tighten/loosen without
# a deploy. See ``apps/eventbus/models.py::HandlerFailureTracker``.
EVENTBUS_HANDLER_EXCEPTION_DLQ_THRESHOLD = int(
    os.environ.get("EVENTBUS_HANDLER_EXCEPTION_DLQ_THRESHOLD", "3")
)

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

# #842 PII tokenization at LLM exit boundary (Tier-A #1 P0 — 152-ФЗ §6).
# Defaults to ENABLED. The `PIITokenizingProvider` decorator in
# `apps/llm/pii_protected_provider.py` reads this; when False, it
# bypasses tokenization entirely (raw text → vendor). Production MUST
# leave this on; the gate exists to let CI tests run without a Redis
# instance + to let internal smoke flows (without user PII) opt out
# without monkey-patching the decorator.
#
# Test conftest sets this to False — explicit override per
# `feedback_pre_post_flip_rubric` (test isolation > production default).
PII_TOKENIZER_ENABLED = os.environ.get("PII_TOKENIZER_ENABLED", "1").lower() not in {
    "0",
    "false",
    "no",
}

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

# Phase 2.2 PR-B — `apps.eventbus` (domain bus) subscriber registry.
# Comma-separated dotted paths to ``apps.eventbus.dispatcher.Subscriber``
# implementations. Default ships ``NoopSubscriber`` so the dispatcher has
# something to call against until real subscribers (AuditSubscriber, etc.)
# land in follow-up PRs. Distinct from ``EVENT_FANOUTS`` (analytics bus).
DOMAIN_EVENT_SUBSCRIBERS: list[str] = [
    p.strip()
    for p in os.environ.get(
        "DOMAIN_EVENT_SUBSCRIBERS", "apps.eventbus.dispatcher.NoopSubscriber"
    ).split(",")
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

# DRF-1029 — handoff escalation notifications to MAX operator chats.
#
# HANDOFF_NOTIFY_MAX_CHAT_IDS: comma-separated MAX chat_ids that receive
# a best-effort push whenever an AdminTask is created (apps/handoff/
# notify.py). EMPTY (default) disables the mechanism completely — no
# network calls, no warning-level log noise. CI and local dev keep it
# empty; the pilot sets it in .env.staging at deploy time.
# HANDOFF_ADMIN_BASE_URL: public base URL of the Django admin used to
# build the direct task link in the notification text
# (<base>/admin/handoff/admintask/<uuid>/change/). Empty → no link line.
HANDOFF_NOTIFY_MAX_CHAT_IDS = [
    p.strip() for p in os.environ.get("HANDOFF_NOTIFY_MAX_CHAT_IDS", "").split(",") if p.strip()
]
HANDOFF_ADMIN_BASE_URL = os.environ.get("HANDOFF_ADMIN_BASE_URL", "")

# Phase 5 lazy-onboarding (apps/miniapp_api/views.py:require_init_data).
# Single-bot mode binds the bot's HMAC token to exactly one tenant; this
# slug picks which one. Multi-tenant ingress will replace this with the
# CHANNEL_TOKEN_TO_TENANT_SLUG map already wired for /api/v1/ingress/max/.
MAX_BOT_TENANT_SLUG = os.environ.get("MAX_BOT_TENANT_SLUG", "")

# Welcome-skill keyboard config (apps/skills/welcome). When the bot's
# Mini App username is set, the welcome buttons use MAX's native
# ``open_app`` button type so the Mini App opens INSIDE the MAX client.
# When unset, the fallback ``link`` button opens MAX_MINIAPP_URL in the
# user's external browser — degraded UX but always works.
#
# MAX_MINIAPP_URL is the Mini App **origin and nothing else** — scheme +
# host, no path (DRF-1326). The whole client path lives in
# ``apps/skills/welcome/skill.py::MINIAPP_ROUTES``; appending ``/customer``
# here would produce ``/customer/customer/wellness``. On the pilot the
# client screens are served from ``proapp``, not ``miniapp``.
#
# Both empty is a supported state, not a broken one: every Mini App button
# is then dropped and the welcome screen ships only bot-native callbacks.
# Filling either variable is what turns those buttons on, so the routes
# they carry have to be right *before* it happens — which is why
# ``apps/skills/welcome/tests/test_miniapp_routes.py`` checks each one
# against the Mini App route table rather than waiting for a live tap.
MAX_BOT_WEB_APP = os.environ.get("MAX_BOT_WEB_APP", "")
MAX_MINIAPP_URL = os.environ.get("MAX_MINIAPP_URL", "")

# Master Mini App session token (PR 1 / M0 onboarding).
#
# Issued by POST /api/v1/master/onboarding/accept. The Mini App stores it
# in ``WebApp.DeviceStorage.setItem('master_token', ...)`` and (later
# PRs) replays it on dashboard endpoints. PR 1 only ISSUES the token —
# consumption / middleware decode lands when the dashboard endpoints
# do (PR 7+).
#
# Format: signed JSON via ``django.core.signing.TimestampSigner`` (we do
# not have PyJWT in deps and the spec is intentionally compatible with
# any future JWT migration — the payload is the same shape). Signing key
# defaults to SECRET_KEY when MASTER_SESSION_SECRET is unset, matching
# the rest of the platform's session-data signing pattern.
MASTER_SESSION_SECRET = os.environ.get("MASTER_SESSION_SECRET", "")
MASTER_SESSION_TTL_DAYS = int(os.environ.get("MASTER_SESSION_TTL_DAYS", "30"))

# Master invite flow (PR 3 / MM2). The admin invite endpoint
# (`apps/admin_api/views_invite.py`) renders a web fallback URL that
# embeds the invite token, used by the owner's UI as a "copy invite
# link" option when the in-bot DM fails. The token is also encoded into
# a MAX deeplink `max://bot/<MASTER_BOT_USERNAME>?start=master_invite_<token>`.
# Defaults:
#   * SITE_DOMAIN — Vite dev default; production overrides via env.
#   * MASTER_BOT_USERNAME — falls back to `<tenant_slug>_bot` when empty
#     (the management command does the same fallback).
SITE_DOMAIN = os.environ.get("SITE_DOMAIN", "http://localhost:5173")
MASTER_BOT_USERNAME = os.environ.get("MASTER_BOT_USERNAME", "")

# M6 AI drafts auto-trigger (deferred follow-up from PR #535 / #540).
#
# When True, every inbound customer Message (``role=USER``) on a
# conversation that involves a master enqueues a Celery task that
# generates an :class:`apps.conversations.models.AiDraft` proactively —
# so the master sees «✨ Предложен ответ» on M5 list refresh without
# tapping «✨ Предложить ответ» first (spec §M6 line 660 «— помощник
# готовит ответ —»).
#
# Default False keeps the pilot launch ramp conservative. Operators
# flip per-environment via env var once cost / rate telemetry is
# stable. The Celery task is enqueued unconditionally from the hook;
# the flag is re-checked inside the worker as a cheap short-circuit
# so an LLM call NEVER happens with the flag off.
AI_DRAFTS_AUTO_TRIGGER_ENABLED = os.environ.get(
    "AI_DRAFTS_AUTO_TRIGGER_ENABLED", "false"
).lower() in ("true", "1")


# M6 auto-trigger idle-active-draft suppress window (issue #659).
# If an ACTIVE draft on a conversation is younger than this many seconds,
# skip auto-trigger regeneration — the master is probably still viewing
# the existing draft. Prevents the documented #659 collision race:
#
#   1. Customer message arrives → auto-trigger task starts LLM call
#      (1-3s under Conversation row lock).
#   2. Master taps «Отправить от себя» on the ACTIVE draft visible in UI.
#   3. send_draft_as_master returns 429 conversation_busy (PR #551 lock).
#   4. Frontend retries after Retry-After: 3 — by then auto-trigger has
#      REPLACED the visible draft with a fresh one.
#   5. send-as-me targets REPLACED draft → 400 draft_already_acted.
#
# Suppressing auto-trigger while the master likely still has the draft
# on-screen breaks the race at step 1. Setting this to 0 disables the
# suppress (regression escape hatch for ops).
#
# Issue #693 (follow-up from #659 review): wrap ``int()`` parsing in a
# try/except so a non-integer env value (operator typo, e.g.
# ``IDLE_ACTIVE_DRAFT_SUPPRESS_WINDOW_SECONDS=abc``) does NOT crash
# Django boot on every worker.  Fall back to the 60s default and log a
# WARNING so the misconfiguration is visible without taking the service
# down — module-load ValueErrors take out ALL workers simultaneously.
def _parse_idle_active_draft_suppress_window() -> int:
    raw = os.environ.get("IDLE_ACTIVE_DRAFT_SUPPRESS_WINDOW_SECONDS", "60")
    try:
        return int(raw)
    except ValueError:
        import logging

        logging.getLogger(__name__).warning(
            "Invalid IDLE_ACTIVE_DRAFT_SUPPRESS_WINDOW_SECONDS=%r — falling back to 60",
            raw,
        )
        return 60


IDLE_ACTIVE_DRAFT_SUPPRESS_WINDOW_SECONDS = _parse_idle_active_draft_suppress_window()

# Sprint 9 / I1 (DRF-825) — Ayla nutrition backend.
# Empty defaults make the lazy singleton fail loudly on first use rather
# than silently 500ing on a misconfigured prod box.
#
# ``AYLA_BASE_URL`` is **host-only** (``scheme://host[:port]``, no ``/api/v1``)
# — the :class:`apps.integrations.ayla.url_builder.AylaUrlBuilder` inserts the
# version prefix (#1049). A base with a path fails loudly at client start.
AYLA_BASE_URL = os.environ.get("AYLA_BASE_URL", "")

# ── s2s auth secrets (#1050 — auth unification) ─────────────────────────────
#
# Two named secrets serve the Ayla REST seam; each has ONE canonical role.
# The audit-era ``AYLA_SERVICE_TOKEN`` conflated both (used as a Bearer by
# recommendations/profile AND as ``X-Service-Token`` by nutrition), and the
# Bearer half never existed on Ayla's side — Ayla validates
# ``AYLA_INTERNAL_API_TOKEN``. S0-A declares the canonical pair below; the
# client-migration stream (S0-B, #978/#1048/#1050) rewires the clients onto it.
#
# 1. ``AYLA_INTERNAL_API_TOKEN`` — the single s2s Bearer. Shipped Ayla internal
#    endpoints authenticate with ``Authorization: Bearer {AYLA_INTERNAL_API_TOKEN}``
#    (reads → IsInternalBearer; writes → IsBotServiceWithVerifiedClient, which
#    also requires ``X-External-User-ID``). Used by payments + booking today;
#    recommendations + profile move onto it in S0-B.
AYLA_INTERNAL_API_TOKEN = os.environ.get("AYLA_INTERNAL_API_TOKEN", "")

# C7 client-payments: fallback ``return_url`` for the YooKassa confirmation
# flows (payment create / card setup) when the miniapp request doesn't carry
# one. W4's master-side flows send return_url explicitly (precedent); the
# customer-side FE predates that — until it catches up, staging/prod set
# this to the miniapp's public URL. Empty + no FE value → the views 400
# (better a local validation error than an upstream one).
AYLA_CLIENT_PAYMENTS_RETURN_URL = os.environ.get("AYLA_CLIENT_PAYMENTS_RETURN_URL", "")

# 2. ``NUTRITION_SERVICE_TOKEN`` — the nutrition ``X-Service-Token`` shared
#    secret, named to match what Ayla validates. Falls back to the legacy
#    ``AYLA_SERVICE_TOKEN`` env var ONLY when unset, so a mid-migration deploy
#    that sets only the old name keeps working (invariant: nutrition's header
#    secret == whatever Ayla's nutrition endpoint validates as
#    ``NUTRITION_SERVICE_TOKEN``). An explicit empty value is honoured as-is
#    (does NOT resurrect the deprecated token during a rotation blank-out).
#
# The old ``AYLA_SERVICE_TOKEN`` *settings attribute* is gone (#1050 / S0-B —
# all client code refs removed): it was a Bearer secret that never existed on
# Ayla's side, conflated with the nutrition ``X-Service-Token``. The env var of
# that name survives ONLY as the back-compat fallback source below, so
# deploys mid-rotation keep working.
_nutrition_service_token = os.environ.get("NUTRITION_SERVICE_TOKEN")
NUTRITION_SERVICE_TOKEN = (
    _nutrition_service_token
    if _nutrition_service_token is not None
    else os.environ.get("AYLA_SERVICE_TOKEN", "")
)

# Feature flag: route the booking skill through the Ayla canonical REST bridge
# instead of direct YClients calls. DEFAULT OFF — the flip (#1041) is gated on
# the ayla_service_id coverage report (#1016/#1034, command:
# link_ayla_service_ids) and is executed by the orchestrator. The flag-ON path
# (real HTTP client + RemoteBookingProxy mirror) is implemented and tested;
# production flips deliberately, never ad-hoc.
BOOKING_VIA_AYLA_REST = os.environ.get("BOOKING_VIA_AYLA_REST", "false").lower() == "true"

# DRF-1005 — Controlled Pilot: per-tenant kill-switch for the booking
# health-check gate.
#
# Under ``BOOKING_VIA_AYLA_REST`` the gate fails CLOSED unconditionally
# (#1034 / #1121) because the resolved (master×service)
# requires-health-check source does not exist yet — which makes automatic
# booking impossible for ANY tenant. Owner decision 2026-08-12 (variant 3):
# an explicit, empty-by-default allowlist of tenant UUIDs for which the
# gate is disabled, with an audit record on every gate-disabled
# evaluation.
#
# Empty/unset = gate closed for every tenant (behaviour unchanged).
# Parsing reuses the strict T-02 allowlist parser: malformed input raises
# ImproperlyConfigured at settings load — a process must not boot with a
# half-parsed allowlist whose operator believes a tenant is listed when
# it is not. Controlled Pilot ONLY; the canonical resolved
# (master×service) ``resolved_requires_health_check`` source replaces
# this setting.
try:
    BOOKING_HEALTH_CHECK_GATE_DISABLED_TENANTS = _parse_ingest_tenant_allowlist(
        os.environ.get("BOOKING_HEALTH_CHECK_GATE_DISABLED_TENANTS", ""),
        setting_name="BOOKING_HEALTH_CHECK_GATE_DISABLED_TENANTS",
    )
except _IngestAllowlistConfigurationError as exc:
    # Same fail-safe as the ingest allowlists below: refuse to boot.
    raise ImproperlyConfigured(
        f"Invalid booking health-check gate allowlist configuration: {exc}"
    ) from exc

# DRF-1007 — Controlled Pilot runs WITHOUT prepayment: per-tenant switch
# for the ``payment_required`` flag on bot-created bookings.
#
# Backend (Ayla) supports both schemes (AMD-002 / D6): ``payment_required``
# False creates the appointment directly CONFIRMED without a Payment row;
# True parks it in ``awaiting_payment`` — and reminders go out ONLY for
# CONFIRMED bookings, so a pilot client booked with the wrong default
# never gets a single reminder.
#
# Owner decision 2026-08-12: the pilot goes without prepayment, but the
# payment model will change — so this is a setting, not a hardcode, and
# it comes off as easily as it goes on. Empty/unset = behaviour unchanged
# (``payment_required=True`` everywhere, as before). An explicit
# ``payment_required`` in the confirm payload still wins over this
# setting — a deliberate caller choice beats a deployment default.
# Same strict parser as above: a malformed value refuses to boot rather
# than silently parsing to an empty allowlist.
try:
    BOOKING_NO_PREPAYMENT_TENANTS = _parse_ingest_tenant_allowlist(
        os.environ.get("BOOKING_NO_PREPAYMENT_TENANTS", ""),
        setting_name="BOOKING_NO_PREPAYMENT_TENANTS",
    )
except _IngestAllowlistConfigurationError as exc:
    raise ImproperlyConfigured(
        f"Invalid booking no-prepayment allowlist configuration: {exc}"
    ) from exc

# Wellness MVP scaled pilot (memory ``project_wellness_mvp_scaled_pilot``).
#
# Two-gate model per founder verdict 2026-06-02:
#
# 1. ``NUTRITION_ENABLED`` — master switch for the RU-side nutrition
#    surface: food log / diary / water / basic daily summary. Data stays
#    on the RU-side via Ayla. Default ``False`` globally so non-pilot
#    environments don't accidentally surface the feature; pilot-env
#    config overrides to ``True``. When False, the food_scanner skill
#    + miniapp_api food endpoints return a graceful «feature off»
#    reply BEFORE any Ayla call.
#
#    Scope of this flag:
#      * `apps.skills.food_scanner` — photo-result diary log + callbacks.
#      * `apps.skills.food_clarify` + `food_correction` — manual food
#        entry helpers.
#      * miniapp_api ``/customer/food/{log,diary,consent}`` endpoints.
#    Out of scope (NOT gated by this flag — they are post-pilot):
#      * Nutrition advice / weekly reports / nudges / recommendations.
#      * Tier-B FSM / anketa / cross-domain insight cards.
#
# 2. ``FOOD_PHOTO_SCAN_ENABLED`` — SEPARATE gate for the cross-border
#    path (photo → OpenAI vision provider via Ayla). Required because
#    photo content crosses the RU border. Default ``False`` until ALL
#    three conditions hold:
#      * Legal-green (#947 — cross-border legal review accepts the
#        scan pipeline).
#      * Cross-border consent storage shipped (server audit trail per
#        #956 for the F0 152-ФЗ acknowledgement).
#      * РКН notification submitted for the cross-border processor.
#    When False but ``NUTRITION_ENABLED=True``, photo turns are refused
#    with a manual-entry hint; food log / diary / water still flow.
#
# Both flags are read at call time via ``getattr(settings, ...)`` so
# runtime overrides in tests work. Pilot-env config sets the live
# values via env vars — the import-time read here is the boot-time
# snapshot used by the skill + endpoints.
NUTRITION_ENABLED = os.environ.get("NUTRITION_ENABLED", "false").lower() in ("true", "1")
FOOD_PHOTO_SCAN_ENABLED = os.environ.get("FOOD_PHOTO_SCAN_ENABLED", "false").lower() in (
    "true",
    "1",
)

# Stabilization sprint Block B / B2 — gift-certificate payment kill-switch.
#
# Founder verdict 2026-05-30 (memory ``project_certificate_payment_post_pilot``):
# certificate domain is DEFERRED post-pilot. The ``buy_certificate`` LLM
# tool and ``💳 Оплатить`` checkout flow stay in the codebase but must
# not be reachable from the customer surface until proper certificate
# implementation lands (Ayla side + bot side, ~4-5 weeks post-pilot).
#
# Reasons for the freeze (per founder memo):
#   * Scope discipline — pilot focuses on the booking flow.
#   * Prepayment legal risk under ФЗ-54 / ст. 487 ГК РФ / ФЗ-2300-1.
#   * Volume unknown — no business case for the cohort yet.
#   * Live mode currently broken — Ayla integration not certified.
#
# Default ``False`` keeps the pilot launch safe by default. Operators
# flip per-environment via env var once the post-pilot certificate
# ticket lands. Both the LLM tool advertisement and the direct
# ``buy_certificate()`` call honour the flag:
#
#   * When False, ``apps.skills.booking.tools.get_active_booking_tool_specs()``
#     filters ``BUY_CERTIFICATE_TOOL_SPEC`` out of the LLM tool list so
#     the model does not pitch a feature it cannot deliver.
#   * When False, a direct call to
#     ``apps.skills.booking.tools.buy_certificate`` short-circuits with
#     a graceful «функция готовится» reply (``error="certificate_disabled"``)
#     — defence-in-depth in case a keyword fallback or replay path
#     bypasses the tool-list filter.
#
# Customer-Mini-App / W2 Block B-2 reads
# ``settings.CERTIFICATE_PAYMENT_ENABLED`` directly to hide the
# certificate entry from the UI surface.
CERTIFICATE_PAYMENT_ENABLED = os.environ.get("CERTIFICATE_PAYMENT_ENABLED", "false").lower() in (
    "true",
    "1",
)

# Phase 1 / B1 (DRF-837) — YClients booking API.
# Single-tenant: env-based credentials. Per-tenant encrypted storage on
# Tenant is a follow-up (requires a migration). Empty defaults keep the
# integration dormant until configured; ``get_yclients_client()`` raises
# loudly on first use when env is missing. NOT enforced in production.py's
# _REQUIRED_ENV_VARS — non-YClients tenants must still boot clean.
YCLIENTS_PARTNER_TOKEN = os.environ.get("YCLIENTS_PARTNER_TOKEN", "")
YCLIENTS_USER_TOKEN = os.environ.get("YCLIENTS_USER_TOKEN", "")
YCLIENTS_COMPANY_ID = os.environ.get("YCLIENTS_COMPANY_ID", "")
YCLIENTS_BASE_URL = os.environ.get("YCLIENTS_BASE_URL", "https://api.yclients.com/api/v1")

# Phase 1 / B2 (DRF-838) — YClients admin webhook tenant resolution.
# YClients does NOT send our X-Tenant header (it's an external system).
# Single-tenant Phase 1 maps every incoming webhook to ONE configured
# tenant slug. Phase 2 will add a (yclients_company_id → tenant_slug)
# mapping table on Tenant; until then, deployments serving multiple
# tenants must run one webhook URL per tenant subdomain.
# Empty default keeps the receiver dormant: payloads get an audit row
# + 200 (still no retries from YClients) until ops configures the slug.
YCLIENTS_WEBHOOK_TENANT_SLUG = os.environ.get("YCLIENTS_WEBHOOK_TENANT_SLUG", "")

# #428 (Bucket 6) — YooKassa settings RETIRED. Per ADR-0009 §Domain
# ownership matrix, YooKassa payment lifecycle (create, capture,
# refund, webhook) lives in Ayla djangoproject only. The four settings
# previously defined here — ``YOOKASSA_SHOP_ID``, ``YOOKASSA_SECRET_KEY``,
# ``YOOKASSA_RETURN_URL``, ``YOOKASSA_TEST_MODE`` — are deleted to
# eliminate the dead-credential surface (no code reads them after this
# PR, but they would otherwise persist in .env files, secret manager,
# CI vaults and Sentry context). SRE: sunset these env vars from all
# deployment environments in the same window as this deploy. The
# matching Ayla-side settings live in ayla-djangoproject/config/settings.

# Sprint 2 / E2 — admin chat for breaker state-transition alerts.
# Empty (default) → telegram_alert is a no-op. Set to the operator's
# MAX chat id to receive 🚨 messages on breaker open/close.
ADMIN_MAX_CHAT_ID = os.environ.get("ADMIN_MAX_CHAT_ID", "")

# Issue #552 — Django CACHES backed by Redis (django-redis).
#
# WHY THIS EXISTS
# ---------------
# Several production code paths rely on the Django cache framework for
# *cross-worker* atomic semantics — namely:
#
#   * apps.master_api.services.ai_draft_limits — per-master rate limit
#     (cache.add SETNX + cache.incr) for the M6 AI drafts endpoint
#     (PR #540).
#   * apps.admin_api.tasks — per-(request_id, decision) SETNX lock that
#     prevents duplicate MAX DMs after a Celery broker hiccup (PR #539).
#   * apps.llm.providers.anthropic_provider — daily-token-counter INCR
#     used by the L5 cost-cap router fallback (DRF-585).
#   * apps.skills.faq.tools — invalidate_kb_search_cache uses
#     ``cache.delete_pattern`` (django-redis-specific API).
#
# Without an explicit CACHES configuration Django falls through to
# ``locmem``, which is *per-worker*. Under N gunicorn workers each
# worker maintains its own counters and SETNX locks; the rate limiter
# silently caps at 10/min × N and idempotency locks let through
# duplicate DMs whenever the second delivery happens to land on a
# different worker. The production boot assertion below
# (``_assert_production_cache_backend``) fails fast if CACHES ends up
# pointing at a non-Redis backend in production.
#
# KEY_PREFIX
# ----------
# Single Redis instance is shared across dev / staging / prod environments
# in some single-tenant deployments. The prefix scopes cache keys per
# environment + service so a staging rate-limiter never collides with a
# production rate-limiter on the same key. ``DJANGO_ENV`` (already used
# by ``DEPLOYMENT_ENVIRONMENT``) selects the suffix; defaults to ``local``.
#
# CONNECTION_POOL_KWARGS
# ----------------------
# ``max_connections=50`` per process is the django-redis recommended
# default and matches the gunicorn workers × Celery workers × eventloops
# arithmetic for the Phase 1 single-tenant deploy (4 web × 4 celery × ~3
# concurrent cache calls each = ~48 peak). Raise via env if multi-tenant
# Phase 2 fan-out increases the per-process concurrency.
#
# TIMEOUT
# -------
# 300s (5 min) default applies only to callers that pass no explicit
# ``timeout``. The rate limiter and SETNX callers above pass explicit
# TTLs (90 / 90_000 / 3600 / 86_400 seconds); they are unaffected by
# this default.
# Redis connection URL — exposed as a settings attribute (not just an
# inline env read) so the readyz probes (apps/orchestrator/views.py)
# hit the SAME url as the cache/celery layers instead of falling back
# to their getattr localhost default. CACHES below consumes this exact
# variable — one source of truth.
REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379/0")

CACHES = {
    "default": {
        "BACKEND": "django_redis.cache.RedisCache",
        "LOCATION": REDIS_URL,
        "KEY_PREFIX": f"ai_bot_platform:{os.environ.get('DJANGO_ENV', 'local')}",
        "TIMEOUT": 300,
        "OPTIONS": {
            "CLIENT_CLASS": "django_redis.client.DefaultClient",
            "CONNECTION_POOL_KWARGS": {
                "max_connections": 50,
                "retry_on_timeout": True,
            },
            # IGNORE_EXCEPTIONS=False (default) — surface Redis outages
            # as ConnectionError to the caller. The rate limiter would
            # otherwise silently allow every request through on a
            # transient Redis blip (worse than 503-ing the request).
        },
    },
}


def _assert_production_cache_backend(*, debug: bool, caches: dict) -> None:
    """Fail-fast guard wired into apps.master_api.apps.MasterApiConfig.ready().

    Verifies the configured ``default`` cache BACKEND is a Redis-backed
    backend whenever DEBUG=False. LocMem in production silently breaks
    rate limiters and idempotency locks (see CACHES docstring above);
    this assertion turns that into a loud :class:`ImproperlyConfigured`
    on Django boot rather than a subtle production correctness bug.

    Additionally guards two env-derived failure modes the BACKEND check
    alone cannot catch (adversarial review PRE_PILOT findings on PR #552):

    * **REDIS_URL silent localhost fallback.** ``CACHES['default']['LOCATION']``
      defaults to ``redis://localhost:6379/0`` when ``REDIS_URL`` is unset.
      In production this points at a Redis the host does not run; boot
      succeeds, the first cache write ``ConnectionError``s. Reject
      ``localhost`` / ``127.0.0.1`` LOCATION when ``DEBUG=False`` — unless
      ``ALLOW_LOCAL_REDIS=true`` is set, the deliberate opt-in for single-box
      deployments that intentionally run Redis on the loopback.
    * **DJANGO_ENV unset → keyspace collision.** ``KEY_PREFIX`` interpolates
      ``DJANGO_ENV`` (defaults to ``local``). On a shared Redis instance
      across dev/staging/prod, a prod boot with ``DJANGO_ENV`` unset
      collides with dev's ``local`` namespace → SETNX idempotency
      false-positives + rate-limit cross-contamination. Reject
      ``DJANGO_ENV`` unset/``local`` when ``DEBUG=False``.

    Local dev + tests run with ``DEBUG=True`` and may use locmem freely.

    Raises:
        django.core.exceptions.ImproperlyConfigured: when DEBUG=False and
          any of (BACKEND not Redis, LOCATION points at localhost, empty
          LOCATION, DJANGO_ENV unset/``local``).
    """

    from django.core.exceptions import ImproperlyConfigured

    if debug:
        return  # Local dev + tests tolerate locmem.

    cache_cfg = caches.get("default") or {}
    backend = cache_cfg.get("BACKEND", "")
    # Accept both django-redis (RedisCache class) and Django 4+ built-in
    # (django.core.cache.backends.redis.RedisCache) — both deliver atomic
    # cross-worker SETNX/INCR via the same Redis server. ``delete_pattern``
    # is django-redis-specific; faq/tools.py degrades gracefully when the
    # method is absent (falls back to ``cache.clear()``), so the built-in
    # backend is also acceptable for the assertion gate.
    if "RedisCache" not in backend:
        raise ImproperlyConfigured(
            f"Production cache backend must be Redis (got {backend!r}). "
            "Rate limiters (apps.master_api.services.ai_draft_limits) and "
            "idempotency locks (apps.admin_api.tasks) depend on cross-worker "
            "atomic cache operations (SETNX/INCR). LocMem is per-worker and "
            "silently bypasses these guards — each gunicorn worker holds its "
            "own counter, so the effective rate cap becomes N×configured. "
            "Set REDIS_URL and ensure CACHES['default']['BACKEND'] resolves "
            "to 'django_redis.cache.RedisCache' (or Django 4+ built-in "
            "'django.core.cache.backends.redis.RedisCache'). See "
            "config/settings/base.py CACHES docstring for the rationale."
        )

    # PRE_PILOT #1 — REDIS_URL silent localhost fallback.
    location = str(cache_cfg.get("LOCATION", ""))
    if not location:
        raise ImproperlyConfigured(
            "CACHES['default']['LOCATION'] is empty in production. Set "
            "REDIS_URL env to a non-empty Redis URL "
            "(e.g. redis://redis.internal:6379/0)."
        )
    if "localhost" in location or "127.0.0.1" in location:
        # Single-box deployments intentionally run Redis on the loopback
        # (host-local Redis with logical-DB isolation — see
        # docs/runbooks/server-deployment.md, "no new containers"). For those
        # the localhost LOCATION is correct, not the silent unset-fallback this
        # guard targets. Require a deliberate, explicit opt-in so the default
        # stays fail-closed for real multi-host prod — an accidental localhost
        # there (REDIS_URL forgotten) still aborts boot.
        allow_local = os.environ.get("ALLOW_LOCAL_REDIS", "false").lower() == "true"
        if not allow_local:
            raise ImproperlyConfigured(
                f"CACHES['default']['LOCATION']={location!r} points at localhost "
                "in production. This is the silent fallback that triggers when "
                "the REDIS_URL env var is unset (see CACHES config in "
                "config/settings/base.py). Set REDIS_URL to the real Redis URL "
                "(e.g. redis://redis.internal:6379/0). For an intentional "
                "single-box deployment where Redis runs on the loopback, set "
                "ALLOW_LOCAL_REDIS=true to assert this is deliberate. Boot would "
                "otherwise succeed but the first cache.add/incr call would "
                "ConnectionError at runtime."
            )

    # PRE_PILOT #2 — DJANGO_ENV unset → keyspace collision.
    # Two layers of defence: the KEY_PREFIX-suffix check catches whatever
    # the prefix interpolation resolved to, and the env-var check catches
    # the upstream cause directly. Either one alone leaves a gap (custom
    # KEY_PREFIX could bypass the suffix check; an explicit DJANGO_ENV
    # plus a hand-edited prefix could bypass the env check), so both run.
    key_prefix = str(cache_cfg.get("KEY_PREFIX", ""))
    if key_prefix.endswith(":local") or key_prefix.endswith(":"):
        raise ImproperlyConfigured(
            f"CACHES['default']['KEY_PREFIX']={key_prefix!r} suggests "
            "DJANGO_ENV is unset or set to 'local' in production. KEY_PREFIX "
            "uses DJANGO_ENV to namespace cache keys; a missing/'local' "
            "value collides with dev/CI environments on a shared Redis "
            "instance (SETNX idempotency false-positives + rate-limit "
            "cross-contamination). Set DJANGO_ENV=production "
            "(or staging/canary/etc) to differentiate the keyspace."
        )

    env = os.environ.get("DJANGO_ENV", "")
    if not env or env == "local":
        raise ImproperlyConfigured(
            "DJANGO_ENV env var must be set to a non-'local' value in "
            f"production (currently {env!r}). KEY_PREFIX uses DJANGO_ENV "
            "to namespace cache keys; missing/'local' value collides with "
            "dev environments on a shared Redis instance."
        )


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

# Modules whose tasks Celery autodiscover_tasks() misses because the
# package isn't a Django app in INSTALLED_APPS. Producer-side imports
# (e.g. apps.booking.services.create) register the task on the web
# process, but the worker only autoloads INSTALLED_APPS; without this
# list it would reject tasks with KeyError on dispatch.
# DRF-1054/1056: apps.llm holds no models and is deliberately not a
# Django app (see apps/llm/__init__.py), so its beat task needs the same
# explicit registration.
CELERY_IMPORTS = (
    "apps.integrations.yclients.tasks",
    "apps.llm.tasks",
)

# Live Shadow Activation (Stage 1 pre-flight) — dedicated queue for the
# observe-only shadow task. Shadow LLM jobs (up to the soft 2.5s budget /
# 30s hard ceiling) must never occupy worker slots of latency-sensitive
# production tasks (booking reminders/followups, eventbus dispatch) that
# share the default `celery` queue on a concurrency=2 pool. Routing is
# registered for THIS task only — every other task keeps the default queue.
# The shadow queue is drained by a dedicated worker
# (`infra/systemd/ai-bot-platform-shadow-worker.service.template`).
CELERY_TASK_ROUTES = {
    "orchestrator.shadow_turn": {"queue": "shadow"},
}

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
    # Phase 2.3 — booking.completed producer. Scans CONFIRMED bookings
    # whose visit time has passed and emits taxonomy §3.1 booking.completed
    # exactly once per booking. Unblocks LoyaltySubscriber (no-op without
    # a producer). Cadence 30 min: tight enough to credit loyalty points
    # within an hour of visit end, sparse enough to spare worker pool.
    "detect_completed_bookings": {
        "task": "bookings.detect_completed_bookings",
        "schedule": crontab(minute="*/30"),
    },
    # Phase 2.c (Loyalty) — daily inactivity hard-downgrade. Scans
    # LoyaltyAccount rows with no EARN_VISIT in ≥ 365 days, drops tier
    # to STARTER + stamps tier_reset_at. Soft 6-month notification
    # deferred (requires notification surface). Daily 05:00 UTC —
    # offset from the 04:00/04:30 cleanup sweeps to keep the worker pool
    # from being slammed by overlapping batch jobs.
    "loyalty_apply_inactivity_downgrades": {
        "task": "loyalty.apply_inactivity_downgrades",
        "schedule": crontab(hour="5", minute="0"),
    },
    "catalog_sync_every_15min": {
        # Fan-out catalog sync across every active tenant. Since S3B (#1044)
        # the task pulls Ayla's internal salon-services (mysite retired).
        # Cadence matched to apps.catalog.services.sync advisory-lock TTL
        # (1.5x). The task carries a 12-min soft time limit so an overrun
        # fires before the next beat fires a parallel run.
        "task": "apps.catalog.tasks.sync_catalog_for_all_tenants",
        "schedule": crontab(minute="*/15"),
    },
    "cleanup_expired_replay_traces": {
        "task": "apps.replay.tasks.cleanup_expired_traces",
        # Daily 04:00 UTC — offset from the 03:00 audit cleanup so the
        # worker pool isn't slammed by both sweeps simultaneously.
        "schedule": crontab(hour="4", minute="0"),
    },
    # #427+#428 — `cleanup_old_payment_events` beat entry RETIRED.
    # apps/orders/tasks.py was deleted; YooKassa webhook lifecycle
    # moved to Ayla djangoproject per ADR-0009 §Domain ownership.
    # The Ayla side runs its own equivalent retention sweep on its
    # PaymentEvent ledger.
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
    # Phase 1 / R1 (DRF-844) — reminder dispatcher. Every 15 min picks
    # PENDING reminders whose scheduled_at has passed, atomically flips
    # status (compare-and-set so concurrent beats can't double-fire),
    # and sends via the channel outbound adapter. 15-min cadence trades
    # ~7m worst-case delay for low load (Phase 1 ~50 bookings/day total).
    "bookings.send_due_reminders": {
        "task": "bookings.send_due_reminders",
        "schedule": crontab(minute="*/15"),
    },
    # Phase 1 / R2 (DRF-845) — escalate stale T-24h reminders to the
    # salon manager. Runs hourly on the hour: picks
    # DAY_BEFORE/SENT_NO_REPLY rows where the visit is < 12h away, flips
    # them to ESCALATED via compare-and-set, and posts a plain-text
    # alert (no buttons) to the tenant's manager_chat_id. The hourly
    # cadence matches the operational tempo — managers don't need to
    # see the alert in < 60 min for a phone call that's still within
    # the 12h window. T-2h reminders are deliberately NOT escalated:
    # they fire too late for the manager to phone before the visit.
    "bookings.escalate_stale_reminders": {
        "task": "bookings.escalate_stale_reminders",
        "schedule": crontab(minute="0"),
    },
    # Phase 1 / R3 (DRF-846) — post-visit follow-up nudge. Runs once
    # daily at 19:00 МСК (= 16:00 UTC) and sends a low-pressure
    # "как прошёл вчерашний визит?" message to every client whose
    # visit was yesterday (Moscow-local day window). Idempotent via
    # ``BotUser.context["last_followup_sent_at"]`` — the same beat
    # tick re-running mid-day, or a daily re-run after a transient
    # MAX outage that left status uncommitted, will not double-send.
    # Cancelled reminders are excluded (the client didn't actually
    # visit). Sentiment classification of the reply is deferred to
    # a follow-up ticket — this beat only sends the prompt.
    "bookings.send_post_visit_followups": {
        "task": "bookings.send_post_visit_followups",
        # 16:00 UTC = 19:00 МСК (UTC+3, Russia does not observe DST).
        "schedule": crontab(hour="16", minute="0"),
    },
    # Issue #499 — PEL reaper. No-ops while PEL_REAPER_ENABLED=False
    # (default). Operator flips the flag after the STRICT_TENANT_REFUSE
    # log-only soak completes; this beat entry is here in advance so
    # enabling the flag is a one-line config change with no further
    # deploy. Every 5 min — tight enough that strict-mode refusals
    # don't pile up past the PEL alert threshold (issue #500), sparse
    # enough that the audit table isn't hammered.
    "workers.reap_pel": {
        "task": "apps.workers.tasks.reap_pel",
        "schedule": crontab(minute="*/5"),
    },
    # PR #507 adversarial A8 — bound the cross-service event-ingest
    # tables' retention. DLQ persists envelope.data per §6.4 (PII
    # surface per ADR-0011 §3.4 + 152-ФЗ); dedupe per §5.3.
    "eventbus.cleanup_ingest_dlq": {
        "task": "apps.eventbus.cleanup_ingest_dlq",
        # Daily 04:45 UTC — slotted after the 04:30 payment-events
        # cleanup and before the 05:00 shadow-delta sweep, so no two
        # large-table sweeps fire simultaneously.
        "schedule": crontab(hour="4", minute="45"),
    },
    "eventbus.cleanup_ingest_dedupe": {
        "task": "apps.eventbus.cleanup_ingest_dedupe",
        # Daily 04:50 UTC — 5 min after the DLQ sweep, separate
        # transaction so a long dedupe sweep doesn't block the
        # DLQ task.
        "schedule": crontab(hour="4", minute="50"),
    },
    # #1056 — bound the consumer-side second-layer dedupe guards
    # (PaymentTerminalDedupe / ReviewProcessedDedupe /
    # NotificationDispatchDedupe) + the #433 HandlerFailureTracker.
    # All share the 120d window (§5.3); chunked delete so a backlog
    # doesn't hold a long lock. Daily 04:55 UTC — after the dedupe
    # sweep, own transaction.
    "eventbus.cleanup_ingest_secondary_ledgers": {
        "task": "apps.eventbus.cleanup_ingest_secondary_ledgers",
        "schedule": crontab(hour="4", minute="55"),
    },
    # PR #535 follow-up Blocker #5 Layer 2 — AI draft retention sweep.
    # Hard-deletes terminal AiDraft rows (SENT_AS_MASTER / RELEASED_TO_AI
    # / REPLACED / DISMISSED) older than 30 days. Layer 1 (immediate
    # content clear on status flip) lives in
    # apps/master_api/services/ai_drafts.py — that closes the at-rest
    # PII window. Layer 2 sweeps the metadata stubs after the finance
    # reconciliation window closes. Daily 03:15 UTC — slotted between
    # the 03:00 audit cleanup and the 03:30 profile recompute to keep
    # worker pool spikes staggered.
    "purge_old_ai_drafts": {
        "task": "apps.conversations.tasks.purge_old_ai_drafts",
        "schedule": crontab(hour="3", minute="15"),
    },
    # DRF-1054 (availability monitor) + DRF-1056 (connection warm-up) —
    # ONE cheap real completion down the production LLM path per tick.
    # Its success is the warm-up; its verdict drives the state machine
    # and the MAX alert. See apps/llm/health.py for why the two tickets
    # share one request instead of firing two.
    #
    # Cadence 5 min, bounded from both sides:
    #
    #  * UPPER — must sit well inside the proxy's idle-decay window. That
    #    window is UNKNOWN; all we measured on 13.08 is that after hours
    #    idle the first call costs 20.7 s while a follow-up costs
    #    0.8-1.4 s. Idle timeouts on HTTP CONNECT proxies and their
    #    upstream keepalives typically land in the 60 s - 15 min band, so
    #    5 min sits under the common floor of that band with margin. If
    #    probe latency in the logs keeps showing cold-start numbers, the
    #    window is tighter than assumed — shorten this, don't widen it.
    #  * LOWER — cost and load. 288 ticks/day x ~13 tokens is under
    #    0.001 USD/day on gpt-4o-mini, and one request per 5 min is
    #    noise next to pilot traffic. There is no reason to go below
    #    this, and going below it would trade real money for nothing.
    #
    # Detection latency that falls out of this: threshold(2) x 5 min +
    # one probe (<= 60 s) ~= 10.5 min worst case, against the several
    # hours the 13.08 outage actually ran undetected.
    "llm.probe_availability": {
        "task": "apps.llm.tasks.probe_llm_availability",
        "schedule": crontab(minute="*/5"),
    },
    # DRF-1285 - proactive nutrition layer. BOTH entries no-op while
    # NUTRITION_PROACTIVE_ENABLED is False (the default), and even once
    # enabled they only log while NUTRITION_PROACTIVE_DRY_RUN is True
    # (also the default). They are listed here in advance for the same
    # reason as workers.reap_pel above: enabling the feature is then an
    # env change, not a deploy. Nothing starts writing to people from a
    # deploy alone.
    #
    # Hourly on the hour. The task cannot know a recipient's chosen hour
    # without localising per row, so the cadence is the finest one the
    # chosen-hour setting can express, and the per-row local-hour match
    # discards the other 23 ticks. 14 candidate rows on the pilot makes
    # that cheap; the BATCH_LIMIT in selection.py bounds the future.
    "nutrition_proactive.send_daily_reports": {
        "task": "nutrition_proactive.send_daily_reports",
        "schedule": crontab(minute="5"),
    },
    # Every four hours at :20 UTC. Six ticks a day; for a Moscow-local
    # recipient they land at 03:20 / 07:20 / 11:20 / 15:20 / 19:20 / 23:20,
    # and the quiet-hours gate silences the first two and the last. Three
    # waking ticks remain, which is exactly MAX_WATER_REMINDERS_PER_DAY --
    # so the cadence and the quota agree instead of one silently shadowing
    # the other. A recipient in a different timezone gets a different split
    # of the same six ticks, and the quota is what bounds them there.
    # Offset from :00 and from the report's :05 so the two beats never
    # contend for the worker pool on the same second.
    "nutrition_proactive.send_water_reminders": {
        "task": "nutrition_proactive.send_water_reminders",
        "schedule": crontab(minute="20", hour="*/4"),
    },
}

# DRF-1285 - the two switches in front of every bot-initiated nutrition
# message. Both are closed by default and both must be opened, in order,
# before a single message reaches a real person.
#
# NUTRITION_PROACTIVE_ENABLED: master switch. False - both beat tasks
#   return immediately without touching the database or Ayla. This is
#   what makes the beat entries above safe to ship ahead of the decision
#   to run them.
# NUTRITION_PROACTIVE_DRY_RUN: the safety inside the switch. True - the
#   tasks run the full selection, the full Ayla read and the full
#   proportional-threshold arithmetic, log exactly whom they would have
#   written to and why, and send nothing. Auto-disable state (the
#   ignored-streak shutoff) is still persisted, because that is a
#   suppression, never a send.
#
# Sequencing is deliberate: ENABLED=True + DRY_RUN=True first, read the
# ``nutrition_proactive.*.dry_run`` log lines against the expected
# recipient list, and only then DRY_RUN=False. Flipping both at once
# skips the only step that can catch a selection bug before a stranger
# gets a message about their calorie intake.
NUTRITION_PROACTIVE_ENABLED = os.environ.get("NUTRITION_PROACTIVE_ENABLED", "false").lower() in (
    "true",
    "1",
)
NUTRITION_PROACTIVE_DRY_RUN = os.environ.get("NUTRITION_PROACTIVE_DRY_RUN", "true").lower() not in (
    "false",
    "0",
)

# DRF-1301 — the same two switches in front of the post-visit follow-up
# («как прошёл вчерашний визит?»), for the same reason and in the same
# order. See apps/bookings/followups.py for the consent gate they guard.
#
# One difference from the nutrition pair above, and it is the whole point
# of the ticket: those two tasks shipped dark, so their switches cost
# nothing. This beat was LIVE and sending — seven messages had already
# gone to two people on the pilot, neither of whom had consent_at set.
# Defaulting ENABLED to False therefore turns a RUNNING feature off on
# purpose. That is the right default for a proactive task found to be
# writing to people who never consented: the operator re-enables it after
# reading `manage.py post_visit_followup_dryrun` against the real
# recipient list, not before.
#
# Sequencing, as with nutrition: ENABLED=True + DRY_RUN=True first, read
# the `bookings.followup.dry_run` log lines, and only then DRY_RUN=False.
POST_VISIT_FOLLOWUP_ENABLED = os.environ.get("POST_VISIT_FOLLOWUP_ENABLED", "false").lower() in (
    "true",
    "1",
)
POST_VISIT_FOLLOWUP_DRY_RUN = os.environ.get("POST_VISIT_FOLLOWUP_DRY_RUN", "true").lower() not in (
    "false",
    "0",
)

# Sprint 7 / L7 (DRF-585) — Anthropic daily-token cost cap. Counter
# stored in Redis as `anthropic_tokens:<YYYY-MM-DD>` (TTL 24h, natural
# UTC-midnight rollover). On overrun the provider raises
# LLMProviderQuotaExceeded; the L5 router falls back to OpenAI.
ANTHROPIC_DAILY_TOKEN_CAP = int(os.environ.get("ANTHROPIC_DAILY_TOKEN_CAP", "1000000"))

# DRF-989 — per-request timeout for OpenAI / Anthropic SDK calls.
# Default 30s replaces the SDK default of 600s, preventing one
# stalled upstream request from blocking the single-threaded worker
# for minutes. Read by both providers in _get_client().
LLM_REQUEST_TIMEOUT_S = float(os.environ.get("LLM_REQUEST_TIMEOUT_S", "30.0"))

# Phase 1 / PI7 (DRF-858) — exponential-backoff retry for OpenAI 429
# and 5xx errors, applied uniformly to both OpenAI and Anthropic
# providers via ``apps.llm.retry.run_with_retry``. Single set of
# settings shared across both providers — per-provider tuning is
# a future ticket if and when it becomes necessary.
#
# DRF-989 update: max_attempts lowered from 3 to 2 so the interactive
# worst-case budget stays under ~90s: timeout(30s) × 2 attempts +
# one 1s backoff ≈ 61s (even with +25% jitter). The SDK's own retries
# are disabled via max_retries=0 in each provider; this layer owns the
# only retry budget.
LLM_RETRY_MAX_ATTEMPTS = int(os.environ.get("LLM_RETRY_MAX_ATTEMPTS", "2"))
LLM_RETRY_BASE_DELAY_S = float(os.environ.get("LLM_RETRY_BASE_DELAY_S", "1.0"))
LLM_RETRY_MAX_DELAY_S = float(os.environ.get("LLM_RETRY_MAX_DELAY_S", "30.0"))

# DRF-1054 (LLM availability monitor) + DRF-1056 (connection warm-up).
# Beat entry: CELERY_BEAT_SCHEDULE["llm.probe_availability"], every 5
# min. Logic + rationale: apps/llm/health.py.
#
# Note what is NOT here: no separate probe timeout knob. The probe uses
# LLM_REQUEST_TIMEOUT_S above, unchanged, because a probe that measures
# something other than the user path is not a monitor. On the 13.08
# numbers that timeout (30 s) already covers the 20.7 s cold start with
# ~9 s to spare, so the cold start does NOT eat the retry budget — see
# the LLM_REQUEST_TIMEOUT_S comment above and DRF-1056's rationale.
#
# LLM_HEALTH_PROBE_ENABLED: master switch. On by default; with an empty
#   HANDOFF_NOTIFY_MAX_CHAT_IDS (the CI / local default) it can still
#   only log, never send.
# LLM_HEALTH_PROBE_MODEL: empty → the provider's default completion
#   model (gpt-4o-mini). Override only to probe a specific deployment.
# LLM_HEALTH_PROBE_TIMEOUT_S: OUTER ceiling on one probe, not the SDK
#   timeout. httpx applies its scalar timeout per phase (connect/read/
#   write/pool), so a single request can outlive any one phase budget;
#   this is the hard stop that keeps a beat tick bounded. 60 s = 2x the
#   SDK timeout.
# LLM_HEALTH_FAILURE_THRESHOLD: consecutive failed probes before the
#   state flips to DOWN and MAX is told. 2 = one blip never pages
#   anyone. Recovery is deliberately NOT debounced — first success
#   clears immediately (slow to alarm, fast to clear).
# LLM_HEALTH_STATE_TTL_S: how long the Redis state keys live. A week —
#   comfortably longer than any plausible gap between ticks. Losing the
#   state costs at most one duplicate alert on the next transition.
LLM_HEALTH_PROBE_ENABLED = os.environ.get("LLM_HEALTH_PROBE_ENABLED", "1") not in {
    "0",
    "false",
    "False",
}
LLM_HEALTH_PROBE_MODEL = os.environ.get("LLM_HEALTH_PROBE_MODEL", "")
LLM_HEALTH_PROBE_TIMEOUT_S = float(os.environ.get("LLM_HEALTH_PROBE_TIMEOUT_S", "60.0"))
LLM_HEALTH_FAILURE_THRESHOLD = int(os.environ.get("LLM_HEALTH_FAILURE_THRESHOLD", "2"))
LLM_HEALTH_STATE_TTL_S = int(os.environ.get("LLM_HEALTH_STATE_TTL_S", str(7 * 24 * 3600)))


# Sprint 1 / C1 channel token map. Format env CHANNEL_TOKEN_TO_TENANT_SLUG:
# ``"token1=tenant-a,token2=tenant-b"``. Sprint 4 replaces with
# encrypted-on-tenant lookup (ADR-0006).
CHANNEL_TOKEN_TO_TENANT_SLUG = os.environ.get("CHANNEL_TOKEN_TO_TENANT_SLUG", "")

# #1019 / EPIC #1014 — global (nationwide) bot tokens. Comma-separated set of
# channel tokens (the same X-Max-Bot-Api-Secret values) that belong to the ONE
# nationwide Ayla bot rather than a single salon. Webhooks bearing one of these
# tokens are routed to the tenant-less global ingress path (discovery at
# ``current_tenant()=None``); a tenant is selected only at booking. Tokens NOT
# in this set keep the legacy per-tenant routing via
# ``CHANNEL_TOKEN_TO_TENANT_SLUG``. Empty when unset (no global bot configured).
#
# Deployment note: a MAX webhook is first gated on the single shared
# ``MAX_WEBHOOK_SECRET`` (``apps/ingress/views.py::max_webhook``), and that same
# header value is then matched against this set. So to route the nationwide bot
# globally, ``GLOBAL_BOT_TOKENS`` must CONTAIN the ``MAX_WEBHOOK_SECRET`` value.
# (Per-tenant encrypted multi-token support is the Sprint 4 / ADR-0006 work; the
# current single-secret gate is unchanged by #1019.)
GLOBAL_BOT_TOKENS = os.environ.get("GLOBAL_BOT_TOKENS", "")

# DRF-1061 — the registry of MAX bots this deployment serves.
#
# Supersedes the four single-bot assumptions documented in
# ``apps/channels/bot_registry`` (webhook gate, outbound token, initData HMAC
# key, tenant binding) with one enumerable structure. Declaring a bot:
#
#   MAX_BOTS=client,salon
#   MAX_BOT_CLIENT_WEBHOOK_SECRET=...   MAX_BOT_SALON_WEBHOOK_SECRET=...
#   MAX_BOT_CLIENT_API_TOKEN=...        MAX_BOT_SALON_API_TOKEN=...
#   MAX_BOT_CLIENT_TENANT_SLUG=...      MAX_BOT_SALON_TENANT_SLUG=formula-tela
#   MAX_BOT_CLIENT_STREAM=max_global    MAX_BOT_SALON_STREAM=max_salon
#   MAX_BOT_CLIENT_MINIAPP_URL=...      MAX_BOT_SALON_MINIAPP_URL=...
#
# ADDITIVE BY CONSTRUCTION: with MAX_BOTS unset, the fallback synthesizes the
# single legacy bot from MAX_WEBHOOK_SECRET / MAX_BOT_TOKEN / MAX_BOT_TENANT_SLUG
# and reproduces today's ingress routing (``max_global`` iff the secret is in
# GLOBAL_BOT_TOKENS, else ``max``). Existing deployments and the ~900 tests that
# set those settings directly are unaffected — do not remove the legacy names as
# part of this change.
#
# NOTE for readers of the GLOBAL_BOT_TOKENS comment above: the "single shared
# secret" gate it describes is what DRF-1061 replaces. Once a deployment declares
# MAX_BOTS, the gate matches against every registered secret and the matching
# entry — not the GLOBAL_BOT_TOKENS membership test — decides the stream.
try:
    MAX_BOT_REGISTRY = _bot_registry_with_legacy(
        _parse_bot_registry(os.environ),
        webhook_secret=MAX_WEBHOOK_SECRET,
        api_token=MAX_BOT_TOKEN,
        # Deliberately NOT MAX_BOT_TENANT_SLUG. Ingress and the Mini App
        # resolve tenancy from different sources today: ingress uses the
        # CHANNEL_TOKEN_TO_TENANT_SLUG map, while the Mini App auth layer
        # reads MAX_BOT_TENANT_SLUG. Feeding the Mini App's slug into the
        # registry would silently change ingress behaviour for every existing
        # deployment — on the pilot, from "tenant-less" to "formula-tela".
        # A legacy bot therefore declares no tenant, and ingress keeps
        # falling back to the token map exactly as before. Unifying the two
        # sources is a real decision, not a side effect of this refactor.
        tenant_slug="",
        global_bot_tokens=GLOBAL_BOT_TOKENS,
        miniapp_url=MAX_MINIAPP_URL,
        web_app=MAX_BOT_WEB_APP,
    )
except _BotRegistryConfigurationError as exc:
    # Refuse to boot rather than serve a half-configured bot: an ambiguous
    # registry means webhooks authenticate against the wrong secret, replies go
    # out as the wrong bot, or a Mini App silently 401s on every screen. All
    # three are far more expensive to diagnose in production than at startup.
    raise ImproperlyConfigured(f"Invalid MAX bot registry configuration: {exc}") from exc

# #1046 — welcome + 152-ФЗ consent capture on the tenant-less global marketplace
# path (`_handle_global_max_event_inner`). Default OFF so enabling is an explicit,
# reviewed rollout. Variant A «soft gate»: onboarding greets + captures consent
# but does NOT block discovery / one-off booking — only long-term memory +
# proactive messaging are gated (that gate lives in the memory writer, S1.7, not
# here). The A→B move (consent required BEFORE any foreign-LLM send, pending the
# #947 lawyer verdict) is a one-line change behind this same flag.
GLOBAL_BOT_ONBOARDING = os.environ.get("GLOBAL_BOT_ONBOARDING", "false").lower() == "true"

# W5 (pilot 2026-08-15) — Concierge Mode rollback switch for the concierge
# memory surface (runbook §7). Default ON (the feature ships enabled); set
# "false" to roll back WITHOUT a deploy: the ai-core memory block is not
# injected into the concierge prompt and the memory-ask flow
# (ask-eligibility → question → PATCH) is bypassed entirely — the W3 gated
# services are not even called. The concierge dialog itself keeps working.
CONCIERGE_MEMORY_ENABLED = os.environ.get("CONCIERGE_MEMORY_ENABLED", "true").lower() == "true"

# DRF-1266 (slice 1, multi-pass concierge) — cap on LLM passes per concierge
# turn. Pass 1 is the primary call; each further pass feeds the executed
# tool's result back as a plain user message (NO tool protocol — the
# Anthropic adapter in ayla-ai-core does not assemble role="tool" blocks,
# so a classic tool loop would silently break when an operator flips
# SKILL_LLM_PROVIDER). Default 2 = primary call + one tool-data pass.
# Set "1" to restore the pre-DRF-1266 single-pass behaviour exactly
# (rollback without a deploy). Values < 1 clamp to 1.
CONCIERGE_MAX_LLM_PASSES = int(os.environ.get("CONCIERGE_MAX_LLM_PASSES", "2"))

# DRF-1284 — switch for the concierge weekly-nutrition context
# (apps.orchestrator.nutrition_context). Default OFF: with it off the Ayla
# deficits endpoint is not called at all and the concierge prompt is
# byte-identical to the pre-DRF-1284 one.
#
# OFF by default because the pilot measurement (2026-08-23) showed the block
# reaching the model — llm_tokens_input +~200/turn, payload verifiably in the
# rendered prompt — WITHOUT changing the reply: the concierge prompt redirects
# anything that is not about booking a master, and its medical boundary makes
# the model answer «я не врач» to a nutrition signal. Turning this on before
# the concierge's scope is widened would buy ~200 input tokens per consented
# turn and nothing else. Flip it together with that prompt decision.
#
# The flag is NOT the privacy control. Nutrition is health data (152-ФЗ ст. 10
# special category), so the real gate is consent — PERSONAL_DATA *and* HEALTH,
# both fail-closed — checked before any Ayla call regardless of this flag. Nor
# is this surface gated by NUTRITION_ENABLED: that flag's documented scope is
# the food-log / diary / water surface and explicitly excludes nutrition
# advice and weekly reports.
CONCIERGE_NUTRITION_CONTEXT_ENABLED = (
    os.environ.get("CONCIERGE_NUTRITION_CONTEXT_ENABLED", "false").lower() == "true"
)

# DRF-963 (Wave 1, variant A) — rollback switch for the pilot conversational
# UX. Default ON (the feature ships enabled); set "false" to roll back WITHOUT
# a deploy. OFF restores the pre-DRF-963 behaviour exactly:
#   * MenuSkill stands down, so unrecognised text falls through to the echo
#     skill again and the widened U-1 service matcher never runs;
#   * HelpSkill stands down, so «что ты умеешь?» goes back to the FAQ skill;
#   * the welcome keyboard reverts to Mini-App salon buttons and drops «Помощь».
# Worth having because the change claims 100% of non-empty text turns on BOTH
# channels: a matcher false positive on an unfamiliar tenant catalog routes
# small talk into booking's LLM flow, and before DRF-963 that cost one echo.
PILOT_CONVERSATIONAL_UX = os.environ.get("PILOT_CONVERSATIONAL_UX", "true").lower() == "true"

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
#
# Both host and token are stripped at import time so probes and the
# actual client see a single normalized value; whitespace-only input
# is treated as intentionally unset (embedded mode / no auth).
CHROMA_HTTP_HOST = os.environ.get("CHROMA_HTTP_HOST", "").strip()
CHROMA_HTTP_PORT = int(os.environ.get("CHROMA_HTTP_PORT", "8001"))
CHROMA_AUTH_TOKEN = os.environ.get("CHROMA_AUTH_TOKEN", "").strip()

# S3/minio endpoint — exposed as a settings attribute so the readyz
# minio probe (apps/orchestrator/views.py) checks the configured
# endpoint instead of its getattr localhost default. Replay/S3 writers
# read env directly today; this is the single attribute probes rely on.
S3_ENDPOINT_URL = os.environ.get("S3_ENDPOINT_URL", "http://localhost:9000")

# Catalog sync (Ayla internal catalog → CatalogService mirror). S3B (#1044):
# the sync service pulls `salon-services` from Ayla's internal Bearer catalog
# (AYLA_BASE_URL + AYLA_INTERNAL_API_TOKEN, above) and upserts the
# CatalogService mirror keyed on the Ayla stable-id. mysite is retired
# (ADR-0009 strangler-fig — MYSITE_CATALOG_* removed). Per-tenant Redis
# advisory lock guards against concurrent runs; the TTL is intentionally
# ≥ 1.5× the 15-minute beat cadence so a slow run can't race itself.
CATALOG_SYNC_LOCK_TTL_SECONDS = int(os.environ.get("CATALOG_SYNC_LOCK_TTL_SECONDS", str(25 * 60)))
CATALOG_SYNC_HTTP_TIMEOUT = int(os.environ.get("CATALOG_SYNC_HTTP_TIMEOUT", "30"))
CATALOG_SYNC_HTTP_RETRIES = int(os.environ.get("CATALOG_SYNC_HTTP_RETRIES", "3"))

# KB-RAG Sub-4b (GH #128) — Google Docs read-only client takes NO
# credentials. It fetches source docs via the public Markdown export
# endpoint and relies on per-doc link-sharing. See
# ``docs/operations/google-docs-public-link.md`` for the per-doc setup
# steps (one-time toggle in the Google Docs share dialog).

# Sprint 10 / O2 (DRF-863) — Alerting (Telegram-only, no PagerDuty).
#
# After the Sprint 10 day-15 decision to skip PagerDuty (cost / RF
# friction of credit-card-backed SaaS signup), the alerting library
# pages exclusively to a dedicated Telegram channel + Sentry for
# critical events. The trade-offs vs PagerDuty are documented in
# `docs/runbooks/on-call.md`.
#
# Setup:
# 1. Create a private Telegram channel `🚨 ai-bot-platform alerts`.
# 2. Add your bot (TELEGRAM_BOT_TOKEN) as a channel admin.
# 3. Find the channel's chat_id (negative integer for channels) via:
#       curl "https://api.telegram.org/bot<TOKEN>/getUpdates"
#    after sending any message to the channel from the bot.
# 4. Set the env vars below.
#
# Empty token / chat_id → page() logs at INFO and skips (local dev
# never pages anyone accidentally). Sentry capture still fires when
# SENTRY_DSN is set.
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
ALERTS_TELEGRAM_CHAT_ID = os.environ.get("ALERTS_TELEGRAM_CHAT_ID", "")

# Phase 1 / CH1 (DRF-848) — Telegram outbound proxy mandate.
#
# api.telegram.org is blocked from Russian IPs (Roskomnadzor). Every
# outbound call from the Telegram channel adapter (and the alerting
# module above) MUST be routed through a proxy. The Telegram adapter's
# ``apps.channels.telegram.proxy.get_proxies()`` helper reads
# ``TELEGRAM_PROXY`` first, then falls back to ``OPENAI_PROXY`` — that
# way a single shared proxy can serve both blocked endpoints. Missing
# both in production → 100% silent delivery failure.
#
# Per-tenant Telegram BOT credentials (BotFather token + webhook
# secret) live on the Tenant row, NOT as global settings — the platform
# is multi-tenant and each salon registers its own bot. The
# ``TELEGRAM_BOT_TOKEN`` setting above is for the global ALERTING
# channel (operator pages) only, NOT for customer-facing channel
# traffic.
TELEGRAM_PROXY = os.environ.get("TELEGRAM_PROXY", "")
OPENAI_PROXY = os.environ.get("OPENAI_PROXY", "")

# OpenAI auth — read by ``apps.orchestrator.llm.openai_provider.OpenAIProvider``
# via ``getattr(settings, "OPENAI_API_KEY", "")``. Without this line the
# provider silently constructs with an empty key, every embedding / chat
# call returns 401, and the ingester's broad ``except`` swallows the
# error as ``failed: N`` with no stack trace. Found by KB-RAG Sub-6 seed
# run (PR #136) — embed_pending_kb_documents reported failures until the
# key was patched in manually. The .env file at the repo root is now
# auto-loaded (PR #135) so a local-dev `.env` line is enough; staging /
# prod inject via their secret stores.
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")

# Phase 5 / KB-SYNC — shared HMAC-SHA256 secret for inbound webhooks from
# the colleague's ``Shiro-Py/salon-knowledge`` service. The webhook view at
# ``/api/v1/salon-knowledge/webhook/approved/`` rejects every request with
# 500 (not 200) when this is empty — a silent-200 on misconfigured prod
# would let approved-knowledge events vanish without raising any alarm.
# Coordinate value with the colleague's ``WebhookEndpoint.secret`` row
# pointing at our URL.
SALON_KNOWLEDGE_WEBHOOK_SECRET = os.environ.get("SALON_KNOWLEDGE_WEBHOOK_SECRET", "")

# Dedup window: identical (severity, dedup_key) pairs within this window
# collapse to a single page. Defaults to 5 minutes — same as PD's default
# dedup behaviour. Lower in tests via override_settings.
ALERTS_DEDUP_TTL_SECONDS = int(os.environ.get("ALERTS_DEDUP_TTL_SECONDS", "300"))

# Sprint 10 / C3 (DRF-879) — mysite catalog webhook HMAC secret.
# The 15-min pull keeps state eventually consistent; this push gives
# salons sub-second feedback when they edit prices/masters. Empty
# default fails closed in production (production.py raises) and
# rejects everything in dev/CI — never silently accepts unsigned
# deliveries.
MYSITE_WEBHOOK_HMAC_SECRET = os.environ.get("MYSITE_WEBHOOK_HMAC_SECRET", "")

# Eventbus cross-service ingest (event-contract.md §6.2, ADR-0009) — shared
# HMAC-SHA256 secret for inbound event envelopes from Ayla. Ayla's outbox
# publisher signs with its ``AYLA_OUTBOUND_HMAC_SECRET``; the two values MUST
# match. Empty default fails closed: ``verify_signature`` rejects everything
# with 401 ``no_secret`` (never silently accepts unsigned deliveries). Was
# getattr-only until the pilot staging smoke (O1/S4) — now env-wired like the
# sibling webhook secrets above.
EVENT_INGEST_HMAC_SECRET = os.environ.get("EVENT_INGEST_HMAC_SECRET", "")

# Tenant-verify bridge for the ingest path (Round-2 AS8, pre-#246). When
# False (default), ``assert_envelope_tenant_authorized`` FAILS CLOSED on any
# envelope whose tenant_id it cannot verify against TenantUserRelationship
# (which lands with #246). staging.py opts into True — the documented
# pre-#246 transition bridge; production.py stays fail-closed until #246.
# Was getattr-only (staging round-trip finding №2) — env never reached it.
EVENT_INGEST_TENANT_VERIFY_FAIL_OPEN = (
    os.environ.get("EVENT_INGEST_TENANT_VERIFY_FAIL_OPEN", "false").lower() == "true"
)

# T-02 / OD-T02-1 — pilot-scoped ingest allowlists.
#
# ``TenantUserRelationship`` (the canonical ADR-0009 §Hard-rule-#6 check)
# lives in Ayla, not here, so ``assert_envelope_tenant_authorized`` fails
# CLOSED on every tenant-scoped envelope and the Wave-1 pilot cannot ingest
# anything. The old workaround was the global FAIL_OPEN flag above —
# unbounded blast radius, and staging was silently running with it. The new
# answer is these two enumerated allowlists: an envelope passes the pilot
# branch only when its tenant AND its event name are both explicitly listed
# AND the tenant exists in the bot DB.
#
# BOTH default to empty = DENY ALL. An empty (or unset) value must never be
# read as "no restriction". Parsing is strict — malformed input raises
# ImproperlyConfigured at import time rather than silently widening access;
# see apps/eventbus/ingest_allowlist.py for the full contract.
#
# Controlled Pilot ONLY. This bounds *scope*; it does not prove the
# user↔tenant relationship. Public MVP MUST replace it with the real
# relationship contract. Rollback = clear both vars (→ fail-closed).
#
# The import is settings-load-safe: ingest_allowlist pulls in nothing from
# Django or the app registry (stdlib ``re`` only).
try:
    EVENT_INGEST_ALLOWED_TENANTS = _parse_ingest_tenant_allowlist(
        os.environ.get("EVENT_INGEST_ALLOWED_TENANTS", "")
    )
    EVENT_INGEST_ALLOWED_EVENTS = _parse_ingest_event_allowlist(
        os.environ.get("EVENT_INGEST_ALLOWED_EVENTS", "")
    )
except _IngestAllowlistConfigurationError as exc:
    # Fail-safe = refuse to boot. A process that starts with a half-parsed
    # allowlist is a process whose operator believes a tenant is onboarded
    # when it is not (or vice versa). Startup failure is the T-02-preferred
    # behaviour for production-like environments.
    raise ImproperlyConfigured(f"Invalid event-ingest allowlist configuration: {exc}") from exc

# Ingest rate-limit IP resolution (apps/eventbus/ingest_ip.py, Round-2 AS2):
# how many leading XFF hops to discard before taking the client IP. 0 = trust
# no XFF (direct deployment). staging nginx adds exactly one trusted hop →
# staging.py sets 1. Env-wired for the same getattr-only reason as above.
EVENT_INGEST_TRUSTED_PROXY_DEPTH = int(os.environ.get("EVENT_INGEST_TRUSTED_PROXY_DEPTH", "0"))

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
#   - When POSTGRES_HOST or DB_HOST is set → Postgres.
#   - Otherwise SQLite for fast local boot without docker.
#
# Two env naming schemes are accepted side-by-side:
#   - POSTGRES_HOST / POSTGRES_DB / ...  — docker-compose convention,
#     used by the Phase 0 platform stack and CI.
#   - DB_HOST / DB_NAME / ...            — the Phase 0 dev-server
#     env template (infra/env/dev.env.example) used these. Kept as
#     aliases so existing /etc/ai-bot-platform/*.env files keep working
#     after the merge that introduced the POSTGRES_* scheme.
#
# Full DATABASE_URL parsing lands in Sprint 9 (production hardening).
def _pg_env(*keys: str, default: str | None = None) -> str | None:
    """Return the first non-empty environment value across ``keys``."""
    for key in keys:
        value = os.environ.get(key)
        if value:
            return value
    return default


_PG_HOST = _pg_env("POSTGRES_HOST", "DB_HOST")
if _PG_HOST:
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.postgresql",
            "NAME": _pg_env("POSTGRES_DB", "DB_NAME", default="ai_bot_platform"),
            "USER": _pg_env("POSTGRES_USER", "DB_USER", default="platform"),
            "PASSWORD": _pg_env("POSTGRES_PASSWORD", "DB_PASSWORD", default="platform"),
            "HOST": _PG_HOST,
            "PORT": _pg_env("POSTGRES_PORT", "DB_PORT", default="5432"),
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
# Phase 0 deploy (DRF-891 / server-deployment.md §2.5) — collectstatic
# target. Required for prod/staging gunicorn deploys; local dev with
# runserver works without it (Django serves static directly via
# staticfiles.views in DEBUG). Sub-directory of BASE_DIR keeps
# everything inside the deploy checkout; nginx serves from
# ``{DEPLOY_PATH}/staticfiles/`` per the api vhost template.
STATIC_ROOT = BASE_DIR / "staticfiles"
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# Phase 1 / PI8 (DRF-859) — PII-redacting log filter wired into every
# persistent-destination handler (console / file / journald). This is
# defence-in-depth on top of Sprint 8 / E1's Sentry ``before_send``
# scrubber (apps.observability.sentry.scrub_event), which only handles
# Sentry-bound events — JSON / stdout / file logs would otherwise reach
# disk with raw phone / email / card numbers in them.
#
# The Sentry handler intentionally does NOT get this filter: Sentry's
# own scrubber runs at ``before_send`` already, and layering two
# scrubbers risks corrupting already-placeholdered text.
#
# ``disable_existing_loggers=False`` preserves Django + Celery + library
# loggers; this config only ADDS the filter on top of stdlib defaults.
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "filters": {
        "pii_redactor": {
            "()": "apps.observability.pii_filter.PIIRedactingFilter",
        },
        "context": {
            "()": "apps.observability.logging.ContextFilter",
        },
    },
    "formatters": {
        "json": {
            "()": "apps.observability.logging.JsonFormatter",
        },
        "simple": {
            "format": "%(asctime)s %(levelname)s %(name)s %(message)s",
        },
    },
    "handlers": {
        # Persistent destination (stdout → journald in prod). PII filter
        # runs BEFORE the formatter so the JSON line lands clean on disk.
        "console": {
            "class": "logging.StreamHandler",
            "level": "INFO",
            "filters": ["pii_redactor", "context"],
            "formatter": "json",
        },
    },
    "root": {
        "level": "INFO",
        "handlers": ["console"],
    },
}

# OR-SHADOW (Bot Runtime Shadow Integration) — side-effect-free shadow
# execution of the new-brain compute subset next to the orchestration
# seam. Default OFF: when disabled the seam performs zero shadow work
# (no enqueue, no latency, no log spam). The legacy brain stays
# authoritative either way; the shadow runs async on the
# ingress:shadow_turn stream and can never change the user-visible reply.
ORCHESTRATOR_SHADOW_ENABLED = (
    os.environ.get("ORCHESTRATOR_SHADOW_ENABLED", "false").lower() == "true"
)
# Soft per-turn budget for the shadow compute (intent classify dominates).
# Exceeding it marks the shadow result TIMEOUT; the legacy turn is never
# affected.
ORCHESTRATOR_SHADOW_TIMEOUT_MS = int(os.environ.get("ORCHESTRATOR_SHADOW_TIMEOUT_MS", "2500"))

# Live Shadow Activation Gate controls (§5-§8).
# SAMPLE_RATE: deterministic fraction of eligible turns dispatched to
# shadow (0.0-1.0). Default 0.0 — enabling the flag alone never floods
# the broker; activation requires an explicit rate (rollout ladder
# 0.01 -> 0.10 -> 0.25 -> 0.50 -> 1.00).
ORCHESTRATOR_SHADOW_SAMPLE_RATE = float(os.environ.get("ORCHESTRATOR_SHADOW_SAMPLE_RATE", "0.0"))
# SURFACES: rollout targeting — seam surfaces allowed to dispatch.
# Default "global" (tenant-less pilot ONLY). per-tenant MAX / Telegram
# stay excluded unless ops explicitly widens the list.
ORCHESTRATOR_SHADOW_SURFACES = os.environ.get("ORCHESTRATOR_SHADOW_SURFACES", "global")
# MAX_BACKLOG: broker admission limit — when the celery queue depth is at
# or above this, new shadow jobs are dropped (logged) and legacy turns
# continue untouched.
ORCHESTRATOR_SHADOW_MAX_BACKLOG = int(os.environ.get("ORCHESTRATOR_SHADOW_MAX_BACKLOG", "500"))
