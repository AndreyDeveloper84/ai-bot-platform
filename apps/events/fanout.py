"""Event fanout adapters (DRF-462 / Sprint 3 / B4).

Adapter pattern for shipping :class:`EventEnvelope` instances to
external destinations after the DB insert.

### Phase 0 vs Phase 1

Sprint 3 ships the **interface only**. Default registry
(`settings.EVENT_FANOUTS = ["apps.events.fanout.NoopFanout"]`) means
no external calls — events land in the DB and stop there.

Phase 1 fills the real adapters:
- :class:`MixpanelFanout` — product analytics.
- :class:`GA4Fanout` — Google Analytics 4 measurement protocol.
- :class:`WarehouseFanout` — BigQuery / Clickhouse for cohort SQL.

Sprint 3 ships the skeletons so call-site code (B3 ``emit()``)
doesn't refactor when integrations arrive.

### Why a Protocol, not an ABC

- Adapters live across modules (Phase 1 may add a community adapter
  in third-party code). Duck-typing via Protocol avoids inheritance
  coupling.
- runtime_checkable lets ``isinstance(adapter, EventFanout)`` work in
  ``fanout_all()`` for safety.

### Error handling

``fanout_all`` catches **per-adapter** exceptions and logs them
without re-raising. Rationale: one failing fanout (e.g. Mixpanel
returning 503) must not break the DB insert path or other fanouts.
This mirrors the ``emit()`` "telemetry never breaks the request"
contract from Sprint 1 E0.
"""

from __future__ import annotations

import logging
from importlib import import_module
from typing import Protocol, runtime_checkable

from django.conf import settings

from apps.events.schema import EventEnvelope

logger = logging.getLogger(__name__)


@runtime_checkable
class EventFanout(Protocol):
    """Sink for canonical event envelopes.

    Implementations must implement a single ``send`` method. The method
    is invoked AFTER the DB insert in ``emit()``; failure is logged
    and swallowed by ``fanout_all``.
    """

    def send(self, envelope: EventEnvelope) -> None:  # pragma: no cover - protocol
        ...


class NoopFanout:
    """Default Phase-0 adapter — never makes a network call.

    Exists so the registry has a real instance to iterate over and so
    test environments don't accidentally hit external services.
    """

    def send(self, envelope: EventEnvelope) -> None:
        # Intentionally empty — Phase 0 keeps events in DB only.
        return None


class MixpanelFanout:
    """Skeleton — Phase 1 fills this in.

    Will call the Mixpanel ingest endpoint with the canonical envelope
    flattened to `{event, distinct_id, properties}`.
    """

    def send(self, envelope: EventEnvelope) -> None:  # pragma: no cover - Phase 1
        logger.debug(
            "fanout.mixpanel.skipped event=%s (Phase 1 will implement)",
            envelope.event_name,
        )


class GA4Fanout:
    """Skeleton — Phase 1 fills this in.

    Will use the GA4 Measurement Protocol (server-side hit) once a
    measurement_id + api_secret pair is provisioned.
    """

    def send(self, envelope: EventEnvelope) -> None:  # pragma: no cover - Phase 1
        logger.debug(
            "fanout.ga4.skipped event=%s (Phase 1 will implement)",
            envelope.event_name,
        )


class WarehouseFanout:
    """Skeleton — Phase 1 fills this in.

    Will batch-stream envelopes to BigQuery or Clickhouse for cohort
    SQL. Probable implementation: an asynchronous buffer flushed by
    a periodic Celery task to avoid per-event network round-trips.
    """

    def send(self, envelope: EventEnvelope) -> None:  # pragma: no cover - Phase 1
        logger.debug(
            "fanout.warehouse.skipped event=%s (Phase 1 will implement)",
            envelope.event_name,
        )


_REGISTRY_CACHE: list[EventFanout] | None = None


def _resolve(path: str) -> EventFanout:
    """Resolve a dotted path like ``apps.events.fanout.NoopFanout`` to an instance."""

    module_path, _, class_name = path.rpartition(".")
    module = import_module(module_path)
    cls = getattr(module, class_name)
    return cls()


def _registry() -> list[EventFanout]:
    """Return the configured fanout adapters as instances.

    Cached after first resolution. Tests that want to swap the
    registry should call :func:`reset_registry_cache` (settings change
    is the canonical lever).
    """

    global _REGISTRY_CACHE
    if _REGISTRY_CACHE is None:
        paths: list[str] = list(
            getattr(settings, "EVENT_FANOUTS", ["apps.events.fanout.NoopFanout"])
        )
        _REGISTRY_CACHE = [_resolve(p) for p in paths]
    return _REGISTRY_CACHE


def reset_registry_cache() -> None:
    """Drop the cached registry. Tests + Django ``setting_changed`` use this."""

    global _REGISTRY_CACHE
    _REGISTRY_CACHE = None


def fanout_all(envelope: EventEnvelope) -> None:
    """Send `envelope` through every registered adapter.

    Per-adapter failures are logged and swallowed; the loop continues.
    Returns None.
    """

    for adapter in _registry():
        try:
            adapter.send(envelope)
        except Exception:  # noqa: BLE001 — one fanout never breaks others
            logger.exception(
                "fanout.send_failed adapter=%s event=%s",
                type(adapter).__name__,
                envelope.event_name,
            )
