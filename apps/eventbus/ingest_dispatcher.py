"""Cross-service event dispatcher — `docs/architecture/event-contract.md` §3 + §5.

Owns the handler registry keyed by ``(event_name, event_version)`` and
the dedupe-aware dispatch entry point used by the ingest view. The
registry is process-local and populated at module import time by
consumer modules (#442–#446) calling :func:`register`.

### Why a registry instead of explicit ``if event_name == "...":``

§4.2 says consumers register handlers per exact ``(event_name,
event_version)`` pair so two versions of the same event can run side-
by-side during a deprecation window. A registry — `dict[tuple, Handler]`
— is the trivial data structure that supports that. An if/elif tree
would lock the dispatcher to one version per name and force a
redeploy on every consumer change.

### Why dispatch lives here, not in the view

The view (:mod:`apps.eventbus.views`) is the HTTP-shaped layer: it
parses, verifies signatures, and maps outcomes to status codes. Pure
business logic (look up the handler, run inside a DB transaction,
write the dedupe row) belongs in this module so tests can exercise
it without spinning up Django's request cycle.

### Tenant-verification mandate (PR #507 adversarial pass A3)

HMAC verification (§6.2 of `event-contract.md`) proves only that
*some Ayla service holding the shared secret signed this body*. It
does NOT prove that the envelope's ``tenant_id`` falls within the
publisher's legitimate authority. A compromised Ayla worker, a
debug script with the secret, or a misconfigured tenant-isolation
boundary on the publisher side could mint an HMAC-valid envelope
carrying ``tenant_id=<victim_tenant>`` + arbitrary ``data`` — and
bot-platform would happily attribute writes to the victim tenant.

**Therefore every registered handler MUST verify that the
envelope's ``tenant_id`` is authorized for ``envelope.user_id``
BEFORE any side-effect**, per ADR-0009 §Hard rule #6 (the
``TenantUserRelationship`` check) and per ADR-0011 §9.1 (red-zone
event handling).

The canonical helper :func:`apps.eventbus.ingest_tenancy.assert_envelope_tenant_authorized`
is the one place to call. It raises :class:`TenantAuthorizationError`
on mismatch; the dispatcher catches and surfaces as
``HANDLER_EXCEPTION`` per §8.1 (Ayla's retry won't help — the
mismatch is permanent — but at least audit + DLQ capture the
attempt). Handlers MUST NOT silently no-op on mismatch.

The lint test :mod:`tests.contracts.test_consumer_tenant_verification_mandate`
asserts every registered handler's source contains a call to this
helper. The lint is permissive today (no handlers registered yet)
and tightens to a fail-on-missing assertion when the
:class:`TenantUserRelationship` model lands via Sprint 1 #246.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum
from typing import Final

from django.conf import settings
from django.db import IntegrityError, transaction
from django.db.models import F
from django.utils import timezone

from apps.eventbus.ingest_envelope import IngestEnvelope
from apps.eventbus.ingest_redaction import redact_data_for_dlq
from apps.eventbus.models import HandlerFailureTracker, IngestDedupe, IngestDLQ


logger = logging.getLogger(__name__)


# Type alias for a registered handler. The handler receives the
# validated envelope and runs its side-effect within the dedupe
# transaction. Returning a value is allowed but ignored — observable
# behaviour is via DB writes / further bus emissions per §5.2.
EventHandler = Callable[[IngestEnvelope], None]


class DispatchOutcome(str, Enum):
    """Possible outcomes of a single dispatch attempt.

    The view layer maps each outcome to an HTTP status per
    `event-contract.md` §8. Keeping the enum here keeps the
    HTTP/business boundary clean: the dispatcher knows nothing about
    HTTP codes, the view knows nothing about how dedupe works.
    """

    OK = "ok"  # Handler ran; dedupe row written. 200.
    DUPLICATE = "duplicate"  # Dedupe hit; handler did NOT run. 200 (§8.7).
    UNKNOWN_EVENT_NAME = "unknown_event_name"  # §8.5 — 422 + DLQ.
    UNKNOWN_EVENT_VERSION = "unknown_event_version"  # §8.4 — 422 + DLQ.
    HANDLER_EXCEPTION = "handler_exception"  # §8.1 — 500, NO dedupe.
    SATURATED = "saturated"  # Round-2 AS5/AS6 — 503 + Retry-After.


@dataclass(frozen=True)
class DispatchResult:
    """Outcome of :func:`dispatch_envelope`.

    The ``exception`` field is populated only on ``HANDLER_EXCEPTION``;
    the view layer logs its type (NOT its ``str()``) per §6.4 PII
    rules and increments the Prometheus failure counter.
    """

    outcome: DispatchOutcome
    exception: BaseException | None = None


# Module-level registry. Populated by consumer modules at import time
# via :func:`register`. Production code MUST NOT mutate at request
# time — the registry is read-mostly and concurrent mutation racing
# the dispatcher is undefined behaviour.
_REGISTRY: dict[tuple[str, int], EventHandler] = {}


def register(event_name: str, event_version: int, handler: EventHandler) -> None:
    """Register a handler for ``(event_name, event_version)``.

    Re-registering the same pair is a programmer error — raise loudly
    rather than silently shadow. Consumer modules import-time call
    this from their `apps.py.ready()` (or a module-level call) so
    every Django start-up has a deterministic registry shape.
    """
    key = (event_name, event_version)
    if key in _REGISTRY:
        raise ValueError(
            f"Handler already registered for {event_name}@v{event_version}; "
            "re-registration is a programmer error."
        )
    _REGISTRY[key] = handler
    logger.info(
        "eventbus.ingest.handler_registered name=%s version=%d",
        event_name,
        event_version,
    )


def unregister(event_name: str, event_version: int) -> None:
    """Remove a registered handler. Used by tests to keep the
    registry hygienic between modules; production code rarely
    unregisters."""
    _REGISTRY.pop((event_name, event_version), None)


def registered_handlers() -> dict[tuple[str, int], EventHandler]:
    """Return a copy of the registry — for tests and introspection."""
    return dict(_REGISTRY)


# `event-contract.md` §3 closed-set event names. Used to distinguish
# UNKNOWN_EVENT_NAME (422) from UNKNOWN_EVENT_VERSION (also 422 but for a
# different operator response). MUST stay in sync with
# ``ingest_envelope.ALLOWED_EVENT_NAMES`` (parse-level allowlist) — a name
# present in one but not the other is a latent 400/422 mismatch.
# ``booking.confirmed`` is a v1 contract extension beyond the original 12
# (B1 consumer; contract addition tracked in issue #946).
_KNOWN_NAMES: Final[frozenset[str]] = frozenset(
    {
        "booking.created",
        "booking.confirmed",
        "booking.cancelled",
        "booking.rescheduled",
        "booking.completed",
        "payment.authorized",
        "payment.captured",
        "payment.failed",
        "payment.refunded",
        "review.created",
        "service.updated",
        "master.schedule.updated",
        "user.profile.updated",
    }
)


def known_event_names() -> frozenset[str]:
    """Return the §3 closed-set of dispatchable event names.

    Public seam over ``_KNOWN_NAMES`` for tests and introspection
    (mirrors :func:`registered_handlers`). The set is frozen, so the
    object can be returned directly without a defensive copy. Callers
    that need to assert the parse↔dispatch allowlist sync should use
    this rather than reaching through the private name.
    """
    return _KNOWN_NAMES


def dispatch_envelope(envelope: IngestEnvelope) -> DispatchResult:
    """Run the registered handler for ``envelope`` with dedupe + DLQ.

    Steps (`event-contract.md` §5.1 + §8.4/§8.5):

    1. If ``event_name`` not in the closed §3 set → write DLQ row
       (reason ``unknown_event_name``) → return ``UNKNOWN_EVENT_NAME``.
    2. If no handler registered for ``(name, version)`` → write DLQ
       (reason ``unknown_event_version``) → return ``UNKNOWN_EVENT_VERSION``.
    3. Open a DB transaction.
    4. Try inserting a dedupe row (PK collision means duplicate
       delivery — short-circuit with ``DUPLICATE``).
    5. Run the handler.
    6. Mark ``processed_at = now()`` on the dedupe row.
    7. Commit. Both side-effect AND dedupe row land together; a crash
       between handler and commit rolls back BOTH (§5.1).

    On handler exception: rollback (no dedupe row), return
    ``HANDLER_EXCEPTION`` so the view can return 500 and Ayla can
    retry per §6.3.
    """
    if envelope.event_name not in _KNOWN_NAMES:
        _write_dlq(envelope, reason="unknown_event_name")
        return DispatchResult(outcome=DispatchOutcome.UNKNOWN_EVENT_NAME)

    key = (envelope.event_name, envelope.event_version)
    handler = _REGISTRY.get(key)
    if handler is None:
        _write_dlq(envelope, reason="unknown_event_version")
        return DispatchResult(outcome=DispatchOutcome.UNKNOWN_EVENT_VERSION)

    try:
        with transaction.atomic():
            # Try inserting dedupe row first — PK collision means
            # duplicate delivery. The standard §5.1 pattern is
            # process-then-record, but the dedupe table's PK uniqueness
            # is the cheaper primitive for short-circuiting duplicates
            # BEFORE running the handler. Writing the row + handler in
            # the same transaction still satisfies the §5.1 atomicity
            # invariant: on handler exception the dedupe insert rolls
            # back too.
            try:
                dedupe_row = IngestDedupe.objects.create(
                    event_id=envelope.event_id,
                    event_name=envelope.event_name,
                    event_version=envelope.event_version,
                    processed_at=timezone.now(),
                )
            except IntegrityError:
                # Duplicate delivery — already processed end-to-end
                # OR processing in flight on another worker. Either way,
                # we MUST NOT re-run the handler.
                return DispatchResult(outcome=DispatchOutcome.DUPLICATE)

            handler(envelope)
            # Update processed_at to AFTER-handler timestamp so the
            # row reflects the moment the side-effect committed.
            dedupe_row.processed_at = timezone.now()
            dedupe_row.save(update_fields=["processed_at"])

    except Exception as exc:  # noqa: BLE001 — we deliberately catch all
        logger.exception(
            "eventbus.ingest.handler_exception event_id=%s name=%s version=%d",
            envelope.event_id,
            envelope.event_name,
            envelope.event_version,
        )
        # #433 umbrella — track failure attempts in a SEPARATE
        # transaction (the outer atomic just rolled back, so a
        # tracker row in there would also roll back). On threshold
        # crossing, upsert a DLQ row so operator triage has a
        # DB-level handle instead of digging through Sentry.
        # Tracker insert is best-effort — a failure here MUST NOT
        # escape past the original handler exception.
        try:
            _track_handler_failure(envelope, exc)
        except Exception:  # noqa: BLE001 — tracker is observability, not load-bearing
            logger.exception(
                "eventbus.ingest.failure_tracking_error event_id=%s",
                envelope.event_id,
            )
        return DispatchResult(outcome=DispatchOutcome.HANDLER_EXCEPTION, exception=exc)

    return DispatchResult(outcome=DispatchOutcome.OK)


def _build_dlq_raw_body(envelope: IngestEnvelope) -> dict:
    """Build the redacted envelope dict for IngestDLQ.raw_body.

    Round-2 AS4 — redact envelope.data BEFORE persisting. DLQ
    retention is 90d (§6.4); without redaction, a publisher bug or
    v2 event with new fields = unredacted PII for 90 days in a
    surface ops triages via Sentry/log-aggregator. See
    apps/eventbus/ingest_redaction.py.
    """
    return {
        "event_id": envelope.event_id,
        "event_name": envelope.event_name,
        "event_version": envelope.event_version,
        "occurred_at": envelope.occurred_at.isoformat(),
        "tenant_id": envelope.tenant_id,
        "user_id": envelope.user_id,
        "actor": envelope.actor,
        "correlation_id": envelope.correlation_id,
        "causation_id": envelope.causation_id,
        "data": redact_data_for_dlq(envelope.data),
    }


def _write_dlq(envelope: IngestEnvelope, *, reason: str) -> None:
    """Persist a DLQ row for an event that cannot be processed.

    Upsert-shaped (#433 umbrella): if a row already exists for
    ``(event_id, reason)`` it's updated, not duplicated. UniqueConstraint
    on the table guarantees idempotency.

    Called from:
    * The dispatcher for ``UNKNOWN_EVENT_NAME`` / ``UNKNOWN_EVENT_VERSION``.
    * :func:`_track_handler_failure` once the retry-counter crosses
      ``settings.EVENTBUS_HANDLER_EXCEPTION_DLQ_THRESHOLD``.
    * The view layer for early-validation rejects (HMAC/timestamp).
    """
    try:
        IngestDLQ.objects.update_or_create(
            event_id=envelope.event_id,
            reason=reason,
            defaults={
                "event_name": envelope.event_name,
                "event_version": envelope.event_version,
                "raw_body": _build_dlq_raw_body(envelope),
            },
        )
    except Exception:  # noqa: BLE001 — DLQ write MUST NEVER block the response
        logger.exception(
            "eventbus.ingest.dlq_write_failed event_id=%s reason=%s",
            envelope.event_id,
            reason,
        )


def _track_handler_failure(envelope: IngestEnvelope, exc: BaseException) -> None:
    """Increment the per-(event_id, handler) attempt counter and
    upsert a DLQ row once it crosses the threshold.

    Called from the dispatcher's ``except Exception`` arm AFTER the
    outer ``transaction.atomic`` has rolled back. We open our own
    atomic so the tracker write commits independently of the
    handler's rollback (the whole point — otherwise the counter
    would never persist).

    #433 umbrella: closes the observability gap where HANDLER_EXCEPTION
    outcomes left no DB-level record for operator triage.
    """
    handler_name = f"{envelope.event_name}@v{envelope.event_version}"
    outcome = "handler_exception"
    error_msg = f"{type(exc).__name__}: {exc}"[:1024]

    with transaction.atomic():
        tracker, created = HandlerFailureTracker.objects.get_or_create(
            event_id=envelope.event_id,
            handler_name=handler_name,
            outcome=outcome,
            defaults={"attempt_count": 1, "last_error": error_msg},
        )
        if not created:
            # Atomic increment + error refresh. F() avoids the
            # read-modify-write race when two concurrent retries
            # land in parallel.
            HandlerFailureTracker.objects.filter(pk=tracker.pk).update(
                attempt_count=F("attempt_count") + 1,
                last_error=error_msg,
            )
            tracker.refresh_from_db(fields=["attempt_count"])

        threshold = getattr(settings, "EVENTBUS_HANDLER_EXCEPTION_DLQ_THRESHOLD", 3)
        if tracker.attempt_count >= threshold:
            # Upsert — second/third attempts past threshold refresh
            # the row but don't create new ones.
            _write_dlq(envelope, reason=outcome)
            logger.warning(
                "eventbus.ingest.handler_exception_threshold "
                "event_id=%s handler=%s attempts=%d threshold=%d",
                envelope.event_id,
                handler_name,
                tracker.attempt_count,
                threshold,
            )
