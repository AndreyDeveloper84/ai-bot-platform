"""Catalog event consumer (#444).

Per ``docs/architecture/event-contract.md`` §3.10. One handler:

* ``service.updated`` — bump ``CatalogService.cache_version`` for the
  mirror row keyed by ``data.service_id`` (the Ayla canonical UUID).
  If ``"is_active" ∈ data.changed_fields``, infer the new boolean
  value from ``previous_values["is_active"]`` (negation) and apply.
  No REST refetch — contract §3.10 step 3 forbids proactive
  re-fetching; the next consumer query lazy-loads via REST.

### Hard rules

* **ADR-0009 Rule #1 + #5** — bot-platform mirror is a read-replica
  of Ayla canonical state. This handler ONLY mutates mirror-local
  fields (``cache_version``, ``is_active``). It does NOT write
  canonical fields (``name``, ``price``, ``duration``) — those are
  Ayla's source of truth, fetched lazily via REST when needed.

* **PII rule §7** — no PII in event payload (only IDs + boolean
  values), no PII risk here. The free-text ``description`` field is
  excluded from event payloads by design per §3.10.

* **Canonical handler shape** (locked through PR #623): tenant
  guard first, idempotency via dispatcher's IngestDedupe table,
  side-effects under dispatcher's ``transaction.atomic``.

### Handler registration

Module-level :func:`register_catalog_handlers` — called from
``apps/eventbus/apps.py::EventBusConfig.ready`` so the registry
shape is deterministic at every Django start.
"""

from __future__ import annotations

import logging
from typing import Any
from uuid import UUID

from django.db.models import F

from apps.catalog.models import CatalogService
from apps.eventbus.ingest_dispatcher import register
from apps.eventbus.ingest_envelope import IngestEnvelope
from apps.eventbus.ingest_tenancy import assert_envelope_tenant_authorized


logger = logging.getLogger(__name__)


# Closed enum of changed_fields per event-contract.md §3.10. The
# ``description`` field is intentionally EXCLUDED (PII §7 — free-text
# wellness phrasing). Unknown field names get logged as a contract-
# drift warning but do not block processing (fail-closed-with-signal
# pattern, mirror of _map_yookassa_failure_code in payment.py).
_CONTRACT_CHANGED_FIELDS: frozenset[str] = frozenset(
    {
        "name",
        "price",
        "duration",
        "is_active",
        "category_id",
        "requires_consultation",
    }
)


# ─── helpers ───────────────────────────────────────────────────────────────


def _infer_new_is_active(previous_values: dict[str, Any]) -> bool | None:
    """Return the new value of ``is_active`` inferred from previous.

    Boolean fields are a special case: the contract never carries the
    NEW value (§3.10 «New values are NOT in the envelope»), but for a
    binary flag we can deterministically negate the previous one.

    Returns ``None`` if ``previous_values`` doesn't contain the
    expected boolean shape — fail safe, no inference.
    """
    if "is_active" not in previous_values:
        return None
    prev = previous_values["is_active"]
    if not isinstance(prev, bool):
        # Defensive: contract encodes booleans as JSON true/false, not
        # strings. If a future contract drift sends "true"/"false"
        # strings, we'd guess wrong — refuse to guess.
        logger.warning(
            "eventbus.consumer.catalog.service_updated.non_bool_is_active "
            "previous_value=%r (skipping inference)",
            prev,
        )
        return None
    return not prev


# ─── handlers ──────────────────────────────────────────────────────────────


def handle_service_updated(envelope: IngestEnvelope) -> None:
    """``service.updated`` — event-contract.md §3.10.

    Steps:
      1. Verify tenant authorization (A3 mandate).
      2. Look up the CatalogService mirror row by Ayla UUID.
      3. If the row exists: bump ``cache_version`` atomically via
         ``F("cache_version") + 1``. If ``is_active`` is in
         ``changed_fields``, also flip it (using ``previous_values``
         to infer the new boolean value).
      4. If the row does NOT exist: log + no-op. The mirror sync
         worker (Celery beat) or a later upsert path will create the
         row; subsequent service.updated events will then find it.
         We deliberately do NOT create a stub here because we don't
         have the canonical fields (name, price, ...) in the event
         payload and a partial row would mislead downstream readers.
    """
    assert_envelope_tenant_authorized(envelope)

    data = envelope.data
    try:
        service_id = UUID(data["service_id"])
    except (KeyError, ValueError, TypeError) as exc:
        logger.warning(
            "eventbus.consumer.catalog.service_updated.bad_service_id event_id=%s data=%r exc=%s",
            envelope.event_id,
            data,
            exc,
        )
        return

    # Round-1 F2 fix: payload shape defence. ``changed_fields`` MUST
    # be a list (substring-match ``"is_active" in "is_active_xyz"``
    # would return True on a string — a publisher contract drift that
    # would silently flip is_active). ``previous_values`` MUST be a
    # dict (``"is_active" in 42`` raises TypeError → DLQ loop).
    changed_fields_raw = data.get("changed_fields", [])
    if not isinstance(changed_fields_raw, list):
        logger.warning(
            "eventbus.consumer.catalog.service_updated.bad_changed_fields "
            "event_id=%s type=%s value=%r",
            envelope.event_id,
            type(changed_fields_raw).__name__,
            changed_fields_raw,
        )
        changed_fields_raw = []
    # Only string entries count — also defends against publishers
    # putting nested objects or ints inside the list.
    changed_fields: list[str] = [field for field in changed_fields_raw if isinstance(field, str)]

    previous_values_raw = data.get("previous_values", {})
    if not isinstance(previous_values_raw, dict):
        logger.warning(
            "eventbus.consumer.catalog.service_updated.bad_previous_values event_id=%s type=%s",
            envelope.event_id,
            type(previous_values_raw).__name__,
        )
        previous_values_raw = {}
    previous_values: dict[str, Any] = previous_values_raw

    # Round-1 S2 fix: log contract-drift warnings for unknown field
    # names. Per §3.10 the closed enum is name|price|duration|
    # is_active|category_id|requires_consultation. Unknowns fail-open
    # (still bump cache_version) but get observability signal. We log
    # ``description`` separately because §3.10 explicitly excludes it
    # (PII §7) — its presence is a contract violation.
    for field in changed_fields:
        if field == "description":
            logger.warning(
                "eventbus.consumer.catalog.service_updated.description_in_payload "
                "event_id=%s — §3.10 forbids description in changed_fields (PII §7)",
                envelope.event_id,
            )
        elif field not in _CONTRACT_CHANGED_FIELDS:
            logger.info(
                "eventbus.consumer.catalog.service_updated.unknown_changed_field "
                "event_id=%s field=%s — possible contract drift",
                envelope.event_id,
                field,
            )

    # Build the per-event UPDATE in one statement — atomic, idempotent
    # at the SQL level. cache_version always bumps. is_active flips
    # ONLY if it's listed in changed_fields AND the inferred new value
    # is not None.
    update_kwargs: dict[str, Any] = {
        "cache_version": F("cache_version") + 1,
    }
    if "is_active" in changed_fields:
        new_is_active = _infer_new_is_active(previous_values)
        if new_is_active is not None:
            update_kwargs["is_active"] = new_is_active

    # Round-1 F1 fix: tenant_id MUST be in the filter. Without it, a
    # cross-tenant collision on ``ayla_service_id`` (test-data leak,
    # future shared-catalog feature, malicious publisher) would let
    # tenant B's event mutate tenant A's mirror row. ``ayla_service_id``
    # has db_index=True but NO unique constraint at the schema level
    # — runtime tenant scoping is the only guarantee.
    tenant_id_str = envelope.tenant_id
    if not tenant_id_str:
        # service.updated is not a tenant-nullable event per
        # ingest_envelope; this branch is defence-in-depth (envelope
        # auth would have raised already).
        logger.warning(
            "eventbus.consumer.catalog.service_updated.null_tenant event_id=%s",
            envelope.event_id,
        )
        return

    updated = CatalogService.all_tenants.filter(
        tenant_id=tenant_id_str,
        ayla_service_id=service_id,
    ).update(**update_kwargs)

    if updated == 0:
        # Mirror row not yet provisioned for this Ayla service. Log
        # for observability; the catalog sync path (Celery beat or a
        # future service.created consumer) will create the row, and
        # subsequent service.updated events will land cleanly.
        logger.info(
            "eventbus.consumer.catalog.service_updated.no_mirror_row "
            "ayla_service_id=%s tenant_id=%s event_id=%s",
            service_id,
            envelope.tenant_id,
            envelope.event_id,
        )
        return

    logger.info(
        "eventbus.consumer.catalog.service_updated.applied "
        "ayla_service_id=%s tenant_id=%s rows=%d changed_fields=%s "
        "event_id=%s",
        service_id,
        envelope.tenant_id,
        updated,
        changed_fields,
        envelope.event_id,
    )

    # §3.10 step 2 — if duration changed, the contract says to also
    # invalidate slot-resolution cache for any master who offers this
    # service. apps/scheduling has no active cache layer today, so
    # this is a no-op + FOLLOW_UP. The cache_version bump above
    # already covers the catalog-layer invalidation; the slot layer
    # joins once it lands a real cache.
    if "duration" in changed_fields:
        logger.info(
            "eventbus.consumer.catalog.service_updated.duration_changed "
            "ayla_service_id=%s tenant_id=%s — slot cache invalidation "
            "deferred (FOLLOW_UP: no active scheduling cache layer)",
            service_id,
            envelope.tenant_id,
        )


# ─── registration ──────────────────────────────────────────────────────────


def register_catalog_handlers() -> None:
    """Register catalog handlers with the ingest dispatcher.

    Called from :func:`apps.eventbus.apps.EventBusConfig.ready`.
    Wrapped in try/except so re-imports during autoreload don't
    crash startup.
    """
    try:
        register(event_name="service.updated", event_version=1, handler=handle_service_updated)
    except ValueError:
        # Duplicate registration — silently OK on autoreload.
        pass
