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
            "eventbus.ingest.tenant_verify_fail_open_enabled "
            "EVENT_INGEST_TENANT_VERIFY_FAIL_OPEN=True. Tenant "
            "verification stub will FALL THROUGH for tenant_id-set "
            "envelopes when the canonical TenantUserRelationship "
            "(Sprint 1 #246) is unavailable. Flip this OFF once "
            "#246 ships in your environment. See PR #524 round-3 NEW-5."
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
