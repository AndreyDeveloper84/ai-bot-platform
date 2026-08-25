"""Identity Celery tasks (DRF-533 / Sprint 6 / P7).

`recompute_profiles_daily` — beats every day at 03:30 UTC (offset from
the 03:00 audit cleanup and ahead of the 04:00 replay cleanup so the
worker pool isn't slammed simultaneously). For every "active" bot_user
(last_seen within the rolling 90-day window), pulls a visit history
from the configured visit source (Phase 0 = empty stub; Phase 1 binds
the real Booking queryset adapter) and writes a fresh ClientProfile
snapshot.

### Why batches of 500 + per-batch audit

Sprint 6 ships against a single tenant with hundreds of bot_users —
1000s in Phase 1. A single transaction over thousands of users would
hold the connection for minutes and lock the events.append + audit.write
paths. Batches of 500 keep one batch <60s and let the audit row record
how many we touched per chunk for forensic reconstruction.

### Phase 0 vs Phase 1 visit source

The visit source is wired via :func:`_get_visit_source` which dispatches
on ``settings.IDENTITY_VISIT_SOURCE`` (dotted path to a callable). Phase 0
default returns an empty iterable — so the task runs end-to-end (audit
rows + ClientProfile writes happen) but every profile ends up segment=new,
monetary=0. That's correct: we have no Booking facts in Phase 0.

Phase 1 binds a real `apps.booking.queries.visits_for_bot_user` callable
without touching this module.
"""

from __future__ import annotations

import logging
from datetime import timedelta
from importlib import import_module
from typing import Callable, Iterable

from celery import shared_task  # type: ignore[import-untyped]
from django.conf import settings
from django.utils import timezone

from apps.audit.services import write_audit
from apps.identity.models import BotUser
from apps.identity.services.recompute import recompute_profile
from apps.tenancy.context import tenant_scope

logger = logging.getLogger(__name__)


_BATCH_SIZE = 500
_ACTIVE_WINDOW_DAYS = 90


def _empty_visit_source(_bot_user: BotUser) -> list:
    """Phase 0 default — no Booking facts yet, return empty iterable."""

    return []


def _get_visit_source() -> Callable[[BotUser], Iterable]:
    """Resolve the configured visit source callable.

    settings.IDENTITY_VISIT_SOURCE is a dotted import path. Defaults to
    the empty stub. Phase 1 sets this to a real Booking queryset adapter.
    """

    path = getattr(settings, "IDENTITY_VISIT_SOURCE", None)
    if not path:
        return _empty_visit_source

    try:
        module_path, attr = path.rsplit(".", 1)
        module = import_module(module_path)
        callable_ = getattr(module, attr)
        return callable_
    except (ImportError, AttributeError, ValueError) as exc:
        logger.exception(
            "identity.tasks.bad_visit_source path=%s err=%s — falling back to empty",
            path,
            exc,
        )
        return _empty_visit_source


@shared_task(name="apps.identity.tasks.recompute_profiles_daily")
def recompute_profiles_daily() -> dict:
    """Refresh ClientProfile for every active bot_user.

    Returns:
      Summary dict {"batches": int, "users_processed": int, "errors": int}.
      Audit rows track per-batch outcomes for forensic recovery.
    """

    visit_source = _get_visit_source()
    active_cutoff = timezone.now() - timedelta(days=_ACTIVE_WINDOW_DAYS)

    # all_tenants — daily sweep across the whole platform.
    qs = (
        BotUser.all_tenants.filter(last_seen__gte=active_cutoff)
        .select_related("tenant")
        .order_by("id")
    )

    batches = 0
    processed = 0
    errors = 0
    buffer: list[BotUser] = []

    for bot_user in qs.iterator(chunk_size=_BATCH_SIZE):
        buffer.append(bot_user)
        if len(buffer) >= _BATCH_SIZE:
            ok, fail = _process_batch(buffer, visit_source)
            processed += ok
            errors += fail
            batches += 1
            buffer.clear()

    if buffer:
        ok, fail = _process_batch(buffer, visit_source)
        processed += ok
        errors += fail
        batches += 1

    summary = {"batches": batches, "users_processed": processed, "errors": errors}
    logger.info("identity.recompute_profiles_daily summary=%s", summary)
    return summary


def _process_batch(
    users: list[BotUser],
    visit_source: Callable[[BotUser], Iterable],
) -> tuple[int, int]:
    """Recompute a single batch of bot_users. Audit-row per batch.

    Returns (success_count, error_count). Per-user errors are caught
    and counted — one bad user shouldn't break the rest of the batch.
    """

    ok = 0
    fail = 0
    for bot_user in users:
        try:
            with tenant_scope(bot_user.tenant):
                visits = visit_source(bot_user)
                recompute_profile(bot_user, visits)
            ok += 1
        except Exception:  # noqa: BLE001 — batch must continue past one bad user
            logger.exception(
                "identity.recompute_profiles_daily.user_failed bot_user=%s",
                bot_user.id,
            )
            fail += 1

    # Per-batch audit row (system-level, no tenant scope — sweeps span tenants).
    write_audit(
        "identity.recompute_profiles.batch",
        payload={"batch_size": len(users), "ok": ok, "fail": fail},
    )

    return ok, fail


@shared_task(name="apps.identity.tasks.forget_all_sweep")
def forget_all_sweep() -> dict:
    """Execute the pending «забудь всё» erasures (DRF-1370).

    The job three docstrings named and nobody wrote — see
    :mod:`apps.identity.services.forget_all_sweep` for what it erases, what
    it deliberately leaves alone, and why it re-runs rather than stamping a
    marker.

    Beat-scheduled HOURLY, not daily like the sweep above it. The read gate
    already silences memory the instant the person asks, so the cadence does
    not govern what they see — it governs how long the rows stay physically
    resurrectable by a read path that forgets the gate. An hour of that
    exposure is a different risk from a day of it, and the query costs a
    count over a table that holds one row per user who ever asked.

    Cross-tenant with no ``tenant_scope``: memory is keyed on the canonical
    Ayla user id and ``UserPersonalContext`` has no tenant FK by design.

    Returns:
      Summary dict {"candidates", "users_swept", "entries_deleted", "errors"}.
    """

    from apps.identity.services.forget_all_sweep import sweep_pending_forget_all

    return sweep_pending_forget_all()
