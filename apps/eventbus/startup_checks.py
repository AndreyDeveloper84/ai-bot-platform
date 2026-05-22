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
