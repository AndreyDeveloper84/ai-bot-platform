"""Cross-service event consumers (#442–#446).

Each module in this package registers one or more
``(event_name, event_version)`` handlers via
:func:`apps.eventbus.ingest_dispatcher.register`. Registration happens
at Django ``ready()`` time via :mod:`apps.eventbus.apps` importing
this package — see ``apps/eventbus/apps.py``.

### Canonical handler shape (locked through PR #524 hardening cycle)

Every handler MUST:

1. Call :func:`apps.eventbus.ingest_tenancy.assert_envelope_tenant_authorized`
   BEFORE any side-effect (A3 mandate).
2. Set outbound HTTP timeout ≤5s on any Ayla REST calls (A12
   defense-in-depth — orphan thread must exit within the 8s budget).
3. Write side-effects inside the dispatcher's ``transaction.atomic``
   (the dispatcher opens it; the handler runs inside it). On
   exception, dedupe row rolls back so retry re-processes (§5.1).
4. Be idempotent on ``envelope.event_id`` — already guaranteed by the
   ``IngestDedupe`` table at the dispatcher layer; handlers MAY add
   secondary checks (upsert-shaped writes) but are not required to.
5. Optionally emit an internal ``apps/events/`` analytics event
   via :func:`apps.events.services.emit` for fan-out to internal
   subscribers.
"""

from __future__ import annotations
