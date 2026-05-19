"""Outbox dispatcher — Q-EV-IMPL3 Celery beat MVP.

Periodically claims pending DomainEvent rows and hands the envelopes
to registered subscribers. Phase 2 wires real subscribers; Phase 1
ships with a Noop subscriber so the polling + claim mechanics are
exercised end-to-end.

### Concurrency model

``SELECT FOR UPDATE SKIP LOCKED`` so multiple workers can run the
beat task concurrently without lock contention. Each worker grabs a
disjoint batch.

### Failure model

Subscriber raises → ``dispatch_attempts++`` + ``last_error`` set, row
left ``is_dispatched=False`` for retry on next tick. Dead-letter
threshold per taxonomy §5 (3 attempts) is checked here and surfaces
the row by leaving it forever-pending; engineering must triage. A
proper dead-letter queue lands when subscribers are real.
"""

from __future__ import annotations

import logging
from typing import Protocol, runtime_checkable

from celery import shared_task
from django.db import transaction
from django.utils import timezone

from apps.eventbus.envelope import Envelope
from apps.eventbus.models import DomainEvent

logger = logging.getLogger(__name__)

MAX_ATTEMPTS = 3  # taxonomy §5
BATCH_SIZE = 100


@runtime_checkable
class Subscriber(Protocol):
    """Sink for outbound domain envelopes."""

    def handle(self, envelope: Envelope) -> None:  # pragma: no cover - protocol
        ...


class NoopSubscriber:
    """Phase 1 default — never makes a network call.

    Exists so the dispatcher has something to call against in tests
    and dev, and so settings can override with real subscribers once
    they exist.
    """

    def handle(self, envelope: Envelope) -> None:
        logger.debug("eventbus.noop_subscriber.handled event=%s", envelope.event_name)


def _subscribers() -> list[Subscriber]:
    """Return configured subscribers. Phase 1 hard-codes Noop.

    Phase 2: read DOMAIN_EVENT_SUBSCRIBERS from settings (dotted paths)
    similarly to apps.events.fanout. Kept inline here so this PR ships
    without adding more settings surface.
    """

    return [NoopSubscriber()]


@shared_task(name="apps.eventbus.dispatch_pending_events")
def dispatch_pending_events(batch_size: int = BATCH_SIZE) -> dict[str, int]:
    """Claim and dispatch the oldest pending domain events.

    Returns counters: {claimed, dispatched, failed, dead_letter}.

    Idempotency: each row is dispatched at-least-once. Subscribers must
    de-duplicate by ``event_id`` (taxonomy §4 — subscriber contract
    requires idempotency).
    """

    counters = {"claimed": 0, "dispatched": 0, "failed": 0, "dead_letter": 0}
    subs = _subscribers()

    with transaction.atomic():
        pending = list(
            DomainEvent.objects.select_for_update(skip_locked=True)
            .filter(is_dispatched=False, dispatch_attempts__lt=MAX_ATTEMPTS)
            .order_by("event_id")[:batch_size]
        )
        counters["claimed"] = len(pending)

        for row in pending:
            envelope = Envelope.from_row(row)
            ok = True
            error_msg = ""
            for sub in subs:
                try:
                    sub.handle(envelope)
                except Exception as exc:  # noqa: BLE001 — one sub never breaks others
                    ok = False
                    error_msg = f"{type(sub).__name__}: {exc}"[:500]
                    logger.exception(
                        "eventbus.dispatch.subscriber_failed event=%s sub=%s",
                        row.event_id,
                        type(sub).__name__,
                    )

            if ok:
                row.is_dispatched = True
                row.dispatched_at = timezone.now()
                row.save(update_fields=["is_dispatched", "dispatched_at"])
                counters["dispatched"] += 1
            else:
                row.dispatch_attempts += 1
                row.last_error = error_msg
                row.save(update_fields=["dispatch_attempts", "last_error"])
                counters["failed"] += 1
                if row.dispatch_attempts >= MAX_ATTEMPTS:
                    counters["dead_letter"] += 1
                    logger.error(
                        "eventbus.dispatch.dead_letter event=%s attempts=%s",
                        row.event_id,
                        row.dispatch_attempts,
                    )

    return counters
