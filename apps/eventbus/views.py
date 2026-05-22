"""HTTP views for the cross-service events ingest channel.

`docs/architecture/event-contract.md` §6 + §8 — entry point for
domain events published by Ayla djangoproject. Orchestrates:

  1. HMAC + timestamp verification (:mod:`apps.eventbus.ingest_security`).
  2. Envelope parsing + validation (:mod:`apps.eventbus.ingest_envelope`).
  3. Handler dispatch with dedupe + DLQ (:mod:`apps.eventbus.ingest_dispatcher`).

Each layer is decoupled so unit tests can exercise it in isolation;
this module's responsibility is the HTTP-shaped boundary — mapping
each layer's outcome to the §8 status taxonomy.
"""

from __future__ import annotations

import logging

from django.conf import settings
from django.http import HttpRequest, JsonResponse
from django.utils.decorators import method_decorator
from django.views import View
from django.views.decorators.csrf import csrf_exempt

from apps.audit.services import write_audit
from apps.eventbus.ingest_dispatcher import (
    DispatchOutcome,
    DispatchResult,
)
from apps.eventbus.ingest_envelope import (
    IngestEnvelope,
    IngestEnvelopeError,
    parse_envelope,
)
from apps.eventbus.ingest_security import (
    signature_header_from,
    timestamp_header_from,
    verify_signature,
)
from apps.eventbus.ingest_timeout import dispatch_with_timeout


logger = logging.getLogger(__name__)


# Audit slugs — single source of truth so test assertions and forensic
# grep stay aligned across the failure taxonomy.
AUDIT_SIGNATURE_FAILED = "eventbus.ingest.signature_failed"
AUDIT_MALFORMED = "eventbus.ingest.malformed"
AUDIT_UNKNOWN_EVENT_NAME = "eventbus.ingest.unknown_event_name"
AUDIT_UNKNOWN_EVENT_VERSION = "eventbus.ingest.unknown_event_version"
AUDIT_HANDLER_EXCEPTION = "eventbus.ingest.handler_exception"
AUDIT_DUPLICATE = "eventbus.ingest.duplicate"
AUDIT_PROCESSED = "eventbus.ingest.processed"


@method_decorator(csrf_exempt, name="dispatch")
class InternalEventsIngestView(View):
    """``POST /api/v1/internal/events/ingest`` — Ayla → bot-platform ingress.

    Status code mapping per `event-contract.md` §8:

    | Outcome                          | Status | Retry by Ayla? | DLQ?  |
    |----------------------------------|--------|----------------|-------|
    | Signature / timestamp fail       | 401    | No (§6.3)      | No    |
    | Malformed JSON / missing field   | 400    | No             | No    |
    | Unknown event_name               | 422    | No             | Yes   |
    | Unknown event_version            | 422    | No             | Yes   |
    | Handler raised                   | 500    | Yes (§6.3)     | No*   |
    | Duplicate delivery (dedupe hit)  | 200    | n/a (§8.7)     | No    |
    | OK                               | 200    | n/a            | No    |

    *  Handler-exception DLQ happens at the publisher (Ayla) after
       §6.3 retry budget exhaustion, not in this view. Each failed
       attempt here surfaces as 500; Ayla counts the attempts.
    """

    http_method_names = ["post"]

    def post(self, request: HttpRequest) -> JsonResponse:
        body = request.body or b""

        # ── 1. HMAC + timestamp ────────────────────────────────────────
        sig_result = verify_signature(
            body=body,
            signature_header=signature_header_from(request),
            timestamp_header=timestamp_header_from(request),
            secret=getattr(settings, "EVENT_INGEST_HMAC_SECRET", "") or "",
        )
        if not sig_result.ok:
            # Body deliberately NOT logged (§8.3 — partial valid data
            # could leak via the prober's error stream).
            logger.warning(
                "eventbus.ingest.signature_failed reason=%s body_bytes=%d",
                sig_result.reason,
                len(body),
            )
            write_audit(
                action=AUDIT_SIGNATURE_FAILED,
                target="eventbus.ingest",
                payload={"reason": sig_result.reason, "body_bytes": len(body)},
            )
            return JsonResponse(
                {"status": "unauthorized", "reason": sig_result.reason},
                status=401,
            )

        # ── 2. Envelope parsing + validation ───────────────────────────
        try:
            envelope = parse_envelope(body)
        except IngestEnvelopeError as exc:
            # §8.4/§8.5 — unknown event_name or unknown event_version
            # shape-wise (e.g. version is a string) are 400 not 422 in
            # this layer: the dispatcher decides 422 on KNOWN-name +
            # UNREGISTERED-version. Here we only know that the JSON
            # shape itself violated the envelope.
            logger.info(
                "eventbus.ingest.malformed reason=%s detail=%s",
                exc.reason,
                exc.detail[:200],
            )
            write_audit(
                action=AUDIT_MALFORMED,
                target="eventbus.ingest",
                payload={"reason": exc.reason, "detail": exc.detail[:200]},
            )
            return JsonResponse(
                {"status": "bad_request", "reason": exc.reason},
                status=400,
            )

        # ── 3. Dispatch (with §8.10 per-handler 8s budget) ─────────────
        # PR #507 adversarial A12 — Ayla's 10s outer HTTP timeout
        # leaves 8s for handler work + 2s for return-trip transit. A
        # slow handler (e.g. hung Ayla REST call from #442+) would
        # otherwise pin a worker thread and block ALL ingestion.
        # dispatch_with_timeout returns HANDLER_EXCEPTION + TimeoutError
        # on budget exceed; the orphan thread continues independently
        # but this request returns 500 promptly.
        result = dispatch_with_timeout(envelope)
        return self._map_outcome(result, envelope=envelope, request=request)

    def _map_outcome(
        self,
        result: DispatchResult,
        *,
        envelope: IngestEnvelope,
        request: HttpRequest,
    ) -> JsonResponse:
        outcome = result.outcome

        if outcome is DispatchOutcome.OK:
            write_audit(
                action=AUDIT_PROCESSED,
                target="eventbus.ingest",
                payload={
                    "event_id": envelope.event_id,
                    "event_name": envelope.event_name,
                    "event_version": envelope.event_version,
                },
            )
            return JsonResponse({"status": "ok"}, status=200)

        if outcome is DispatchOutcome.DUPLICATE:
            # §8.7 — expected and silent. NO alert; the audit is a
            # forensic trail only, sampled by ops as needed.
            write_audit(
                action=AUDIT_DUPLICATE,
                target="eventbus.ingest",
                payload={
                    "event_id": envelope.event_id,
                    "event_name": envelope.event_name,
                },
            )
            return JsonResponse({"status": "ok", "duplicate": True}, status=200)

        if outcome is DispatchOutcome.UNKNOWN_EVENT_NAME:
            write_audit(
                action=AUDIT_UNKNOWN_EVENT_NAME,
                target="eventbus.ingest",
                payload={
                    "event_id": envelope.event_id,
                    "event_name": envelope.event_name,
                },
            )
            return JsonResponse(
                {"status": "unprocessable", "reason": "unknown_event_name"},
                status=422,
            )

        if outcome is DispatchOutcome.UNKNOWN_EVENT_VERSION:
            write_audit(
                action=AUDIT_UNKNOWN_EVENT_VERSION,
                target="eventbus.ingest",
                payload={
                    "event_id": envelope.event_id,
                    "event_name": envelope.event_name,
                    "event_version": envelope.event_version,
                },
            )
            return JsonResponse(
                {"status": "unprocessable", "reason": "unknown_event_version"},
                status=422,
            )

        # HANDLER_EXCEPTION
        exc_type = type(result.exception).__name__ if result.exception is not None else "Unknown"
        write_audit(
            action=AUDIT_HANDLER_EXCEPTION,
            target="eventbus.ingest",
            payload={
                "event_id": envelope.event_id,
                "event_name": envelope.event_name,
                "event_version": envelope.event_version,
                # PII rule: log exception TYPE only, not message.
                "exception_type": exc_type,
            },
        )
        return JsonResponse(
            {"status": "internal_error", "reason": "handler_exception"},
            status=500,
        )
