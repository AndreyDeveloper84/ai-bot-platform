"""Startup-time defensive checks for the cross-service event ingest.

PR #507 adversarial pass A5. Surfaces ops-visible warnings when
Django settings combinations would silently degrade the ingest
machinery's failure-mode guarantees.

Why a warning vs an exception:

The Django ORM is single-tenant for these checks; we can't
arbitrarily refuse to boot a deploy that has chosen `ATOMIC_REQUESTS=True`
for legitimate reasons in other apps. Logging a high-severity warning
at startup gets it into the ops dashboard / log aggregator at first
deploy without breaking the runtime. If the warning is ignored AND a
handler exception fires under that combination, the existing audit
trail will simply be empty — diagnosable at incident-time.
"""

from __future__ import annotations

import logging
import os

from django.conf import settings


logger = logging.getLogger(__name__)


def warn_if_atomic_requests_true() -> None:
    """Log a warning if ``ATOMIC_REQUESTS=True`` on the default DB.

    Per `event-contract.md` §8.1 + §8.7 the ingest endpoint MUST
    persist audit + DLQ rows even when the handler raises. Today's
    view code catches dispatcher exceptions internally and returns
    500 cleanly, so a 500 status does NOT trigger Django's
    transaction rollback under ATOMIC_REQUESTS=True. The warning
    here is forward-defense — if a future refactor lets an
    exception escape the view, ATOMIC_REQUESTS=True would silently
    eat the audit row.
    """
    databases = getattr(settings, "DATABASES", {}) or {}
    default_db = databases.get("default") or {}
    atomic_requests = bool(default_db.get("ATOMIC_REQUESTS"))
    if atomic_requests:
        logger.warning(
            "eventbus.ingest.atomic_requests_true_warning "
            "DATABASES['default']['ATOMIC_REQUESTS']=True detected. "
            "Audit + DLQ persistence on handler-exception requires the "
            "ingest view to catch exceptions internally (current code "
            "does). If a future change lets an exception escape, the "
            "outer transaction will roll back the audit row. See "
            "PR #507 adversarial-pass A5."
        )


def warn_if_tenant_verify_fail_open() -> None:
    """Round-3 NEW-5 — surface the tenant-verify fail-open exposure window.

    When ops sets ``EVENT_INGEST_TENANT_VERIFY_FAIL_OPEN=True``
    (the documented pre-#246 bridge), the helper falls through
    silently per-request. Without a startup signal, an
    accidentally-True production deploy ships invisibly. Log a
    high-severity WARNING at app ready time so the misconfig hits
    the ops dashboard at first deploy.
    """
    fail_open = bool(getattr(settings, "EVENT_INGEST_TENANT_VERIFY_FAIL_OPEN", False))
    if fail_open:
        logger.warning(
            "eventbus.ingest.tenant_verify_fail_open_enabled security=critical "
            "environment=%s EVENT_INGEST_TENANT_VERIFY_FAIL_OPEN=True. "
            "TENANT RELATIONSHIP VERIFICATION IS DISABLED: every "
            "tenant_id-set envelope the T-02 pilot allowlist refuses is "
            "admitted anyway, for ANY tenant and ANY event. Emergency "
            "escape hatch only — not part of any recommended configuration, "
            "and the pilot runbook does not use it. Prefer "
            "EVENT_INGEST_ALLOWED_TENANTS / EVENT_INGEST_ALLOWED_EVENTS. "
            "See PR #524 round-3 NEW-5 + T-02.",
            _environment_label(),
        )


def _environment_label() -> str:
    """Best-effort environment name for the security warnings.

    Purely for log legibility — never load-bearing.
    """
    return os.environ.get("DJANGO_SETTINGS_MODULE", "unknown")


def check_event_ingest_allowlists() -> None:
    """T-02 — surface every unsafe or ineffective pilot-allowlist combination.

    The allowlists are the pilot's entire ingest authorization story, and
    their failure modes are silent: an empty one denies everything (the
    ingest looks "up" but drops every delivery), and a half-configured pair
    denies everything while *looking* configured. Both deserve a boot-time
    line in the ops dashboard.

    This check never mutates state and never refuses to boot — malformed
    values already fail hard at settings load (``config/settings/base.py``
    raises ``ImproperlyConfigured``). What remains here are the parseable-
    but-ineffective combinations, plus a defensive re-parse that catches a
    value injected after settings load.
    """
    from apps.eventbus.ingest_allowlist import (
        AllowlistConfigurationError,
        resolve_allowed_events,
        resolve_allowed_tenants,
    )

    try:
        tenants = resolve_allowed_tenants(
            getattr(settings, "EVENT_INGEST_ALLOWED_TENANTS", frozenset())
        )
        events = resolve_allowed_events(
            getattr(settings, "EVENT_INGEST_ALLOWED_EVENTS", frozenset())
        )
    except AllowlistConfigurationError as exc:
        # Deny-all with an explicit error, per the T-02 fail-safe rule.
        logger.error(
            "eventbus.ingest.allowlist_malformed security=high "
            "The event-ingest allowlist settings could not be parsed: %s. "
            "Tenant events are DENIED (deny-all), never allowed. Fix the "
            "EVENT_INGEST_ALLOWED_TENANTS / EVENT_INGEST_ALLOWED_EVENTS "
            "values and restart.",
            exc,
        )
        return

    if not tenants and not events:
        # The safe resting state. Worth one line so an operator debugging
        # "why did my booking.created 500?" finds the answer at boot.
        logger.warning(
            "eventbus.ingest.allowlist_empty "
            "EVENT_INGEST_ALLOWED_TENANTS and EVENT_INGEST_ALLOWED_EVENTS "
            "are both empty. TenantUserRelationship is unavailable in "
            "bot-platform, so every tenant-scoped event will be rejected "
            "(fail-closed). This is SAFE and is the correct default — "
            "populate both to onboard a pilot tenant."
        )
    elif not events:
        logger.error(
            "eventbus.ingest.allowlist_half_configured security=medium "
            "EVENT_INGEST_ALLOWED_TENANTS has %d entr(ies) but "
            "EVENT_INGEST_ALLOWED_EVENTS is EMPTY — the effective policy is "
            "DENY ALL. The tenant allowlist alone admits nothing.",
            len(tenants),
        )
    elif not tenants:
        logger.error(
            "eventbus.ingest.allowlist_half_configured security=medium "
            "EVENT_INGEST_ALLOWED_EVENTS has %d entr(ies) but "
            "EVENT_INGEST_ALLOWED_TENANTS is EMPTY — the effective policy is "
            "DENY ALL. The event allowlist alone admits nothing.",
            len(events),
        )
    else:
        logger.info(
            "eventbus.ingest.allowlist_active verification_mode=pilot_allowlist "
            "tenants=%d events=%s — Controlled Pilot scope. This bounds WHICH "
            "tenants and events may be ingested; it does NOT prove the "
            "user-tenant relationship (Public MVP requirement).",
            len(tenants),
            ",".join(sorted(events)),
        )

    # Vocabulary sanity: an event name no consumer can ever dispatch is a
    # typo, and a typo in an allowlist reads as "configured" while silently
    # denying the real event.
    if events:
        try:
            from apps.eventbus.ingest_dispatcher import _KNOWN_NAMES

            unknown = sorted(events - _KNOWN_NAMES)
        except Exception:  # noqa: BLE001 — introspection only, never load-bearing
            unknown = []
        if unknown:
            logger.error(
                "eventbus.ingest.allowlist_unknown_event security=medium "
                "EVENT_INGEST_ALLOWED_EVENTS contains name(s) absent from the "
                "event-contract vocabulary: %s. These can never match a real "
                "delivery — check for a typo.",
                ", ".join(unknown),
            )


def warn_if_event_ingest_hmac_missing() -> None:
    """O1/S4 — surface an empty ``EVENT_INGEST_HMAC_SECRET`` at boot.

    Without the secret the ingest endpoint rejects EVERY envelope with
    401 ``no_secret`` — a staging deploy that forgets the var looks
    "up" but is dead to Ayla's outbox publisher. Log a high-severity
    WARNING at app ready time so the misconfig hits the ops dashboard
    at first deploy (fail-closed by design, but loud about it).
    """
    if not getattr(settings, "EVENT_INGEST_HMAC_SECRET", ""):
        logger.warning(
            "eventbus.ingest.hmac_secret_missing "
            "EVENT_INGEST_HMAC_SECRET is empty — every ingest request "
            "will be rejected with 401 no_secret. Set it to the shared "
            "value of Ayla's AYLA_OUTBOUND_HMAC_SECRET."
        )
