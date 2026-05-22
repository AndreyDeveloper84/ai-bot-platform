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

from django_ratelimit.decorators import ratelimit  # type: ignore[import-untyped]

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
from apps.eventbus.ingest_ip import get_remote_ip
from apps.eventbus.ingest_rate_audit_sampler import should_audit_rate_limited
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
AUDIT_RATE_LIMITED = "eventbus.ingest.rate_limited"
AUDIT_SATURATED = "eventbus.ingest.saturated"


# PR #507 adversarial A2 — rate limit on the ingest endpoint as
# defense-in-depth against captured-tuple replay flood. The 5-min
# anti-replay window (§6.2) means an attacker who captured ONE
# valid (body, sig, ts) triple could fire it at line speed for up
# to 5 minutes; each call burns HMAC verify CPU + DB lookup BEFORE
# the dedupe short-circuit fires. The rate limit caps that
# amplification at the source-IP level.
#
# Default 100/min per source IP — well above expected steady-state
# traffic (Ayla's outbox dispatcher emits per-event, not in bursts).
# Overridable via settings.EVENT_INGEST_RATE_LIMIT for tenants with
# legitimately higher publish rates.
#
# Long-term defense is WAF/CDN rate limiting at the network edge;
# this code-side limit is the defense-in-depth layer for the time
# until that's deployed.
_RATE_LIMIT_DEFAULT = "100/m"


def _rate_limit_key(group, request) -> str:
    """Proxy-aware rate-limit key — Round-2 AS1.

    django-ratelimit's built-in ``key='ip'`` reads ``REMOTE_ADDR``,
    which behind any reverse proxy is the proxy's IP — collapsing all
    Ayla traffic into a single bucket. :func:`get_remote_ip` walks
    ``X-Real-IP`` → trusted ``X-Forwarded-For`` chain → ``REMOTE_ADDR``,
    using the proxy-trust depth from
    ``settings.EVENT_INGEST_TRUSTED_PROXY_DEPTH``.

    Empty string ⇒ no source identifiable ⇒ single bucket for the
    «unknown» surface — bounded but loud (deliberately conservative
    until ops sets up proxy headers).
    """
    return get_remote_ip(request) or "_unknown_"


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

    @method_decorator(
        ratelimit(
            # AS1 — proxy-aware key. See :func:`_rate_limit_key`.
            key=_rate_limit_key,
            # Rate is read from settings at request time via the lambda
            # form so deploys can tune without a code change.
            rate=lambda group, request: getattr(
                settings, "EVENT_INGEST_RATE_LIMIT", _RATE_LIMIT_DEFAULT
            ),
            method="POST",
            block=False,
        )
    )
    def post(self, request: HttpRequest) -> JsonResponse:
        body = request.body or b""

        # ── 0. Rate limit gate (§A2 + Round-2 AS1/AS3) ─────────────────
        # django_ratelimit sets request.limited=True when the per-IP
        # bucket is exhausted (`block=False` form). We return 429 with
        # a JSON body. Audit writes are SAMPLED on this path (Round-2
        # AS3) — without sampling a scanner without HMAC would
        # generate unbounded AuditLog rows = unauthenticated DoS
        # amplifier on the audit table.
        if getattr(request, "limited", False):
            remote_ip = get_remote_ip(request) or "_unknown_"
            logger.warning(
                "eventbus.ingest.rate_limited remote=%s body_bytes=%d",
                remote_ip,
                len(body),
            )
            # AS3 — 1 audit row per IP per 60s window. Suppressed
            # rows still count via Prometheus / ratelimit counters;
            # audit captures the forensic «first hit» only.
            if should_audit_rate_limited(remote_ip):
                write_audit(
                    action=AUDIT_RATE_LIMITED,
                    target="eventbus.ingest",
                    payload={
                        "remote_ip": remote_ip,
                        "body_bytes": len(body),
                    },
                )
            return JsonResponse(
                {"status": "rate_limited", "reason": "rate_limit_exceeded"},
                status=429,
            )

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

        if outcome is DispatchOutcome.SATURATED:
            # Round-2 AS5/AS6 — in-flight executor exhausted. Return
            # 503 + Retry-After so Ayla's outbox backs off per §6.3
            # instead of amplifying the slow-Ayla cascade. Audit
            # sampled-via-cache to avoid amplifier (same rationale as
            # AS3 audit sampling on 429).
            from apps.eventbus.ingest_rate_audit_sampler import (
                should_audit_rate_limited as _should_audit_saturated,
            )

            remote_ip = get_remote_ip(request) or "_unknown_"
            if _should_audit_saturated(remote_ip):
                write_audit(
                    action=AUDIT_SATURATED,
                    target="eventbus.ingest",
                    payload={
                        "event_id": envelope.event_id,
                        "event_name": envelope.event_name,
                        "event_version": envelope.event_version,
                        "remote_ip": remote_ip,
                    },
                )
            response = JsonResponse(
                {"status": "saturated", "reason": "executor_in_flight_exhausted"},
                status=503,
            )
            # Per §6.3 — exponential backoff starts at 1s; Retry-After
            # at 5s gives Ayla a generous window without bouncing the
            # event off the retry budget too fast.
            response["Retry-After"] = "5"
            return response

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
