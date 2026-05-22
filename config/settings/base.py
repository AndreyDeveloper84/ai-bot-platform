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
    # Phase 1 / B7 (DRF-843) — orders (certificates today, extensible).
    # Owns the Order + PaymentEvent models. The YooKassa client +
    # webhook live in apps.integrations.yookassa; the buy_certificate
    # LLM tool wiring lives in apps.skills.booking.
    "apps.orders",
    # Customer Mini App Phase 0a — master schedule + slot resolver.
    "apps.scheduling",
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

# Sprint 9 / I1 (DRF-825) — Ayla nutrition backend.
# Empty defaults make the lazy singleton fail loudly on first use rather
# than silently 500ing on a misconfigured prod box.
AYLA_BASE_URL = os.environ.get("AYLA_BASE_URL", "")
AYLA_SERVICE_TOKEN = os.environ.get("AYLA_SERVICE_TOKEN", "")

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

# Phase 1 / B7 (DRF-843) — YooKassa hosted-checkout integration.
# Powers the ``buy_certificate`` LLM tool. Empty defaults keep the
# integration dormant until ops configures it; ``YOOKASSA_TEST_MODE``
# defaults to True so any accidental call returns a stubbed checkout
# URL and never hits api.yookassa.ru. Production deployments MUST set
# ``YOOKASSA_TEST_MODE=false`` AND populate the shop id + secret key
# AND override ``YOOKASSA_RETURN_URL`` to the public post-checkout
# landing page.
#
# SECURITY: ``YOOKASSA_SECRET_KEY`` is a payments credential — never
# log it, never include it in audit / event payloads, never include
# it in any breaker-alert / observability path.
YOOKASSA_SHOP_ID = os.environ.get("YOOKASSA_SHOP_ID", "")
YOOKASSA_SECRET_KEY = os.environ.get("YOOKASSA_SECRET_KEY", "")
YOOKASSA_RETURN_URL = os.environ.get(
    "YOOKASSA_RETURN_URL",
    "https://example.com/payment/return",
)
YOOKASSA_TEST_MODE = os.environ.get("YOOKASSA_TEST_MODE", "true").lower() in (
    "true",
    "1",
    "yes",
    "on",
)

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

# Modules whose tasks Celery autodiscover_tasks() misses because the
# package isn't a Django app in INSTALLED_APPS. Producer-side imports
# (e.g. apps.booking.services.create) register the task on the web
# process, but the worker only autoloads INSTALLED_APPS; without this
# list it would reject tasks with KeyError on dispatch.
CELERY_IMPORTS = ("apps.integrations.yclients.tasks",)

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
    # Phase 1 / PI1 (DRF-851) — PaymentEvent dedup-ledger retention.
    # Daily 04:30 UTC — slotted between the 04:00 replay sweep and the
    # 05:00 shadow-delta compute so no two sweeps fire simultaneously
    # against the same worker pool. PaymentEvent is a small table in
    # Phase 1 (1 tenant, low webhook volume) but accumulates fast
    # under YooKassa redelivery — daily hard-delete keeps it bounded.
    "cleanup_old_payment_events": {
        "task": "apps.orders.tasks.cleanup_old_payment_events",
        "schedule": crontab(hour="4", minute="30"),
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
}

# Sprint 7 / L7 (DRF-585) — Anthropic daily-token cost cap. Counter
# stored in Redis as `anthropic_tokens:<YYYY-MM-DD>` (TTL 24h, natural
# UTC-midnight rollover). On overrun the provider raises
# LLMProviderQuotaExceeded; the L5 router falls back to OpenAI.
ANTHROPIC_DAILY_TOKEN_CAP = int(os.environ.get("ANTHROPIC_DAILY_TOKEN_CAP", "1000000"))

# Phase 1 / PI7 (DRF-858) — exponential-backoff retry for OpenAI 429
# and 5xx errors, applied uniformly to both OpenAI and Anthropic
# providers via ``apps.llm.retry.run_with_retry``. Single set of
# settings shared across both providers — per-provider tuning is
# a future ticket if and when it becomes necessary.
#
# Defaults: 3 attempts (initial + 2 retries), 1s base delay with
# exponential doubling (1s, 2s, 4s, …), 30s cap, ±25% jitter. With
# these defaults the worst-case before exhaustion is ~3s of waiting
# — well under the 4000ms p95 turn budget for the (~95%) of
# transients that retry-successfully on the first or second retry.
LLM_RETRY_MAX_ATTEMPTS = int(os.environ.get("LLM_RETRY_MAX_ATTEMPTS", "3"))
LLM_RETRY_BASE_DELAY_S = float(os.environ.get("LLM_RETRY_BASE_DELAY_S", "1.0"))
LLM_RETRY_MAX_DELAY_S = float(os.environ.get("LLM_RETRY_MAX_DELAY_S", "30.0"))


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
