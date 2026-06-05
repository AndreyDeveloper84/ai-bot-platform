"""Schedule event consumer (#445 — master.schedule.updated).

Per event-contract.md §3.11. One handler:

* ``master.schedule.updated`` — when a master's working hours,
  exceptions, or vacation change, bump
  ``CatalogMaster.cache_version`` so downstream slot-resolution
  cache layers transparently invalidate stale entries.

### Hard rules

* **ADR-0009 Rule #5** — bot-platform never writes canonical
  schedule state. This handler mutates ONLY the local mirror's
  ``cache_version`` signal field. Canonical schedule lives in Ayla.
* **Contract §3.11 step 2** — handler MUST NOT cancel existing
  bookings. Ayla emits ``booking.cancelled`` separately if the
  schedule change invalidated any bookings.
* **No active cache layer today** — ``apps/scheduling`` has no
  Redis/in-memory cache for slots. ``cache_version`` is a
  forward-compatible signal; when the cache lands, it includes this
  field in its key and increments transparently invalidate.

### Granularity

Contract §3.11 distinguishes ``affected_all_dates=true`` (entire
schedule) from per-date ``affected_dates`` list. This handler treats
both as a single ``cache_version`` bump — per-tech-lead Q3 decision
(global counter, no per-date granularity until an active cache reader
exists that could use it).

### Handler registration

Module-level :func:`register_schedule_handlers` — called from
``apps/eventbus/apps.py::EventBusConfig.ready``.
"""

from __future__ import annotations

import logging
from uuid import UUID

from django.db.models import F

from apps.catalog.models import CatalogMaster
from apps.eventbus.ingest_dispatcher import register
from apps.eventbus.ingest_envelope import IngestEnvelope
from apps.eventbus.ingest_tenancy import assert_envelope_tenant_authorized


logger = logging.getLogger(__name__)


# ─── handlers ──────────────────────────────────────────────────────────────


def handle_master_schedule_updated(envelope: IngestEnvelope) -> None:
    """``master.schedule.updated`` — event-contract.md §3.11.

    Steps:
      1. Verify envelope tenant authorization (A3).
      2. Parse ``master_id`` (UUID) + optional ``affected_dates`` list
         for observability (NOT used for per-date invalidation today).
      3. Atomic ``F("cache_version") + 1`` on the CatalogMaster
         mirror row scoped by ``(tenant_id, ayla_user_id=master_id)``.
      4. Log change_type + per-date count for forensic trace.

    Idempotency: cache_version bumps are additive — 3 replays = +3
    versions, still a valid invalidation signal. IngestDedupe at the
    dispatcher layer is the primary guard against same-event_id
    replays; for distinct-event_id-same-master replays the cache key
    just gets bumped 2-3× harmlessly (forward-compatible signal).
    """
    assert_envelope_tenant_authorized(envelope)

    data = envelope.data
    try:
        master_id = UUID(data["master_id"])
    except (KeyError, ValueError, TypeError) as exc:
        logger.warning(
            "eventbus.consumer.schedule.master_schedule_updated.bad_master_id "
            "event_id=%s data=%r exc=%s",
            envelope.event_id,
            data,
            exc,
        )
        return

    # Defence-in-depth payload parsing. Contract §3.11 says
    # affected_dates is a list (≤365 entries) and change_type is an
    # enum; we only log them, but malformed shapes shouldn't crash.
    change_type = data.get("change_type", "")
    if not isinstance(change_type, str):
        change_type = ""

    affected_all_dates = bool(data.get("affected_all_dates"))
    affected_dates_raw = data.get("affected_dates", [])
    affected_dates_count = len(affected_dates_raw) if isinstance(affected_dates_raw, list) else 0

    tenant_id_str = envelope.tenant_id
    if not tenant_id_str:
        logger.warning(
            "eventbus.consumer.schedule.master_schedule_updated.null_tenant event_id=%s",
            envelope.event_id,
        )
        return

    updated = CatalogMaster.all_tenants.filter(
        tenant_id=tenant_id_str,
        ayla_user_id=master_id,
    ).update(cache_version=F("cache_version") + 1)

    if updated == 0:
        # Mirror row not yet provisioned for this Ayla master. Log
        # for observability; the catalog sync path will create the
        # row, and subsequent master.schedule.updated events will
        # land cleanly. Symmetric with #444 catalog consumer.
        logger.info(
            "eventbus.consumer.schedule.master_schedule_updated.no_mirror_row "
            "ayla_master_id=%s tenant_id=%s event_id=%s",
            master_id,
            envelope.tenant_id,
            envelope.event_id,
        )
        return

    logger.info(
        "eventbus.consumer.schedule.master_schedule_updated.applied "
        "ayla_master_id=%s tenant_id=%s change_type=%s "
        "affected_all_dates=%s affected_dates_count=%d event_id=%s",
        master_id,
        envelope.tenant_id,
        change_type,
        affected_all_dates,
        affected_dates_count,
        envelope.event_id,
    )


# ─── registration ──────────────────────────────────────────────────────────


def register_schedule_handlers() -> None:
    """Register schedule handlers with the ingest dispatcher.

    Called from :func:`apps.eventbus.apps.EventBusConfig.ready`.
    """
    try:
        register(
            event_name="master.schedule.updated",
            event_version=1,
            handler=handle_master_schedule_updated,
        )
    except ValueError:
        # Duplicate registration — silently OK on autoreload.
        pass
