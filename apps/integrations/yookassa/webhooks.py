"""YooKassa webhook receiver (DRF-843 / Phase 1 / B7).

POST ``/api/v1/yookassa/webhook/``. Receives payment-status
notifications from YooKassa's notification subsystem.

### Security pipeline

Every request flows through three independent gates BEFORE any
side-effect is allowed to fire:

1. **IP allowlist** (:func:`apps.integrations.yookassa.ip_allowlist.is_yookassa_ip`)
   — rejects anything not coming from a YooKassa-published subnet
   with HTTP 403. NO audit row is written here, by design: we don't
   want bots discovering valid IPs by probing the audit log.
2. **HMAC-SHA256 signature** (:func:`apps.integrations.yookassa.signing.verify_webhook_signature`)
   — verifies the body wasn't tampered with. A failed signature
   returns 200 (don't leak verifier behaviour to a prober) but DOES
   write an audit row + a log line.
3. **Idempotency ledger** (:class:`apps.orders.models.PaymentEvent`)
   — a UNIQUE constraint on ``external_event_id`` catches re-delivery.
   We use ``get_or_create`` and check ``created`` — on the second
   delivery we short-circuit before any side effect.

### Always 200 (except IP)

YooKassa retries any non-2xx response for hours. A transient bug
during fulfilment becomes a stream of duplicate deliveries that mask
the original problem. The rule is: every code path inside the
post-IP-gate body returns 200. Operators investigate via the audit
log, not the HTTP status. The IP gate IS allowed to 403 because a
forbidden caller isn't an "intended retry" — they shouldn't be
talking to us at all.

### Side effects

* ``payment.succeeded``: flips Order status to PAID, sets ``paid_at``,
  enqueues the :func:`apps.orders.tasks.fulfill_paid_certificate`
  Celery task (which sends the bot confirmation message).
* ``payment.canceled``: flips Order status to CANCELLED, enqueues the
  :func:`apps.orders.tasks.notify_cancelled_certificate` task.
* Anything else: audit + 200, no state change.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from django.conf import settings
from django.db import IntegrityError, transaction
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from apps.audit.services import write_audit
from apps.integrations.yookassa.ip_allowlist import is_yookassa_ip
from apps.integrations.yookassa.signing import verify_webhook_signature
from apps.orders.models import Order, PaymentEvent


logger = logging.getLogger(__name__)


# Audit slugs — single source of truth for action names so test
# assertions and grep-based forensics stay aligned.
AUDIT_BAD_SIGNATURE = "yookassa.bad_signature"
AUDIT_MALFORMED = "yookassa.webhook.malformed"
AUDIT_UNKNOWN_EVENT = "yookassa.webhook.unknown_event"
AUDIT_DUPLICATE = "yookassa.webhook.duplicate"
AUDIT_PROCESSED = "yookassa.webhook.processed"
AUDIT_NO_ORDER = "yookassa.webhook.no_order"
AUDIT_HANDLER_EXCEPTION = "yookassa.webhook.handler_exception"

# Event slugs we know how to act on.
EVENT_PAYMENT_SUCCEEDED = "payment.succeeded"
EVENT_PAYMENT_CANCELED = "payment.canceled"

# YooKassa publishes their signature header as ``Content-HMAC`` per
# their v3 docs. Django mangles request headers as ``HTTP_<UPPER_SNAKE>``
# in ``request.META``. We accept the canonical form first, falling
# through to an alias commonly used in test fixtures.
_SIG_HEADER_META = "HTTP_CONTENT_HMAC"
_SIG_HEADER_ALIAS = "HTTP_X_YOOKASSA_SIGNATURE"


def _signature_header(request: HttpRequest) -> str:
    """Pull the signature header out of ``request.META``.

    Falls through ``Content-HMAC`` (the canonical YooKassa name) →
    ``X-YooKassa-Signature`` (test fixture alias). Returns ``""`` when
    neither is present.
    """
    return str(request.META.get(_SIG_HEADER_META) or request.META.get(_SIG_HEADER_ALIAS) or "")


def _remote_ip(request: HttpRequest) -> str:
    """Resolve the source IP for allowlist matching.

    Reads ``REMOTE_ADDR``. We deliberately DO NOT honour
    ``X-Forwarded-For`` here — that header is set by clients and
    spoofable. Deployments that sit behind a reverse proxy must
    configure the proxy to override REMOTE_ADDR with the true
    client IP at the proxy layer (e.g. nginx ``real_ip``).
    """
    return str(request.META.get("REMOTE_ADDR") or "")


@csrf_exempt
@require_POST
def yookassa_webhook(request: HttpRequest) -> HttpResponse:
    """POST ``/api/v1/yookassa/webhook/`` — YooKassa notification receiver.

    Flow:

    1. **IP gate** — non-YooKassa source → 403, NO audit. (See module
       docstring for the silent-403 rationale.)
    2. **Signature gate** — missing / bad → 200 + audit + log.
    3. **Body parse** — non-JSON / wrong shape → 200 + audit.
    4. **Idempotency** — ``PaymentEvent.get_or_create`` on
       ``external_event_id``. Duplicate → 200 + audit, no side effect.
    5. **Event handler** — dispatches on ``event`` slug. Unknown event
       slugs → 200 + audit, no side effect. Known slugs flip Order
       status + enqueue fulfilment.
    """
    # ── 1. IP gate ─────────────────────────────────────────────────────
    remote_ip = _remote_ip(request)
    if not is_yookassa_ip(remote_ip):
        logger.info(
            "yookassa_webhook.ip_rejected remote=%s",
            remote_ip,
        )
        return HttpResponse(status=403)

    # Everything below MUST return 200 unless an unhandled exception
    # bubbles all the way up — and even then we catch + 200 here so
    # YooKassa stops retrying.
    try:
        return _process(request)
    except Exception as exc:  # noqa: BLE001 — always 200 rule
        logger.exception("yookassa_webhook.unhandled exc=%s", exc)
        write_audit(
            action=AUDIT_HANDLER_EXCEPTION,
            target="yookassa.webhook",
            payload={"exception_type": type(exc).__name__},
        )
        return JsonResponse({"status": "error", "reason": "handler_exception"}, status=200)


def _process(request: HttpRequest) -> JsonResponse:
    body_bytes = request.body or b""

    # ── 2. Signature gate ─────────────────────────────────────────────
    secret = getattr(settings, "YOOKASSA_SECRET_KEY", "") or ""
    header_value = _signature_header(request)
    try:
        signature_ok = verify_webhook_signature(body_bytes, secret, header_value)
    except ValueError:
        # Empty secret — misconfigured deploy. Treat as bad signature
        # so we don't silently accept anything. Audit + 200.
        signature_ok = False
        logger.warning("yookassa_webhook.empty_secret")

    if not signature_ok:
        # NEVER include the secret in the audit payload.
        write_audit(
            action=AUDIT_BAD_SIGNATURE,
            target="yookassa.webhook",
            payload={
                "signature_present": bool(header_value),
                "body_bytes": len(body_bytes),
            },
        )
        # Return 200 — don't leak verifier behaviour to a prober.
        return JsonResponse({"status": "ok"}, status=200)

    # ── 3. Body parse ─────────────────────────────────────────────────
    try:
        payload = json.loads(body_bytes or b"{}")
    except (json.JSONDecodeError, UnicodeDecodeError):
        write_audit(
            action=AUDIT_MALFORMED,
            target="yookassa.webhook",
            payload={"reason": "json_decode"},
        )
        return JsonResponse({"status": "ok"}, status=200)
    if not isinstance(payload, dict):
        write_audit(
            action=AUDIT_MALFORMED,
            target="yookassa.webhook",
            payload={"reason": "not_object"},
        )
        return JsonResponse({"status": "ok"}, status=200)

    event_type = str(payload.get("event") or "")
    obj = payload.get("object") or {}
    if not isinstance(obj, dict):
        write_audit(
            action=AUDIT_MALFORMED,
            target="yookassa.webhook",
            payload={"reason": "object_not_dict", "event": event_type},
        )
        return JsonResponse({"status": "ok"}, status=200)

    # YooKassa's event_id is at the top level. The payment object has
    # its own id (the payment_id). Both matter:
    #
    # * event_id        — unique-per-delivery, used for idempotency.
    # * object.id       — the payment id, used to find the Order.
    external_event_id = str(payload.get("event_id") or payload.get("id") or "")
    payment_id = str(obj.get("id") or "")
    if not external_event_id:
        write_audit(
            action=AUDIT_MALFORMED,
            target="yookassa.webhook",
            payload={"reason": "missing_event_id", "event": event_type},
        )
        return JsonResponse({"status": "ok"}, status=200)

    # ── 4. Find order BEFORE idempotency insert ────────────────────────
    # We look up by payment_id so the PaymentEvent FK is populated. A
    # missing order is fine — we still record the event for forensics.
    order: Order | None = None
    if payment_id:
        order = Order.all_tenants.filter(external_payment_id=payment_id).first()

    # ── 5. Idempotency ledger ─────────────────────────────────────────
    # get_or_create on a UNIQUE field is the atomic primitive: if the
    # row already exists, Django returns it with ``created=False`` and
    # we skip the side effect. The IntegrityError fallback handles the
    # race where two concurrent deliveries reach this line at the same
    # instant — the loser hits the constraint and we treat it as a
    # duplicate.
    event_row: PaymentEvent | None
    try:
        event_row, created = PaymentEvent.objects.get_or_create(
            external_event_id=external_event_id,
            defaults={
                "event_type": event_type,
                "order": order,
                "raw_payload": payload,
                "processed": False,
            },
        )
    except IntegrityError:
        # Race — another worker won the insert. Treat as duplicate.
        created = False
        event_row = PaymentEvent.objects.filter(external_event_id=external_event_id).first()

    if not created or event_row is None:
        write_audit(
            action=AUDIT_DUPLICATE,
            target="yookassa.webhook",
            payload={
                "event_id": external_event_id,
                "event_type": event_type,
            },
        )
        logger.info(
            "yookassa_webhook.duplicate event_id=%s event_type=%s",
            external_event_id,
            event_type,
        )
        return JsonResponse({"status": "ok", "duplicate": True}, status=200)

    # ── 6. Dispatch on event_type ─────────────────────────────────────
    if event_type == EVENT_PAYMENT_SUCCEEDED:
        _handle_payment_succeeded(event_row, order, payload)
    elif event_type == EVENT_PAYMENT_CANCELED:
        _handle_payment_canceled(event_row, order, payload)
    else:
        write_audit(
            action=AUDIT_UNKNOWN_EVENT,
            target="yookassa.webhook",
            payload={"event": event_type, "event_id": external_event_id},
        )
        logger.info(
            "yookassa_webhook.unknown_event event_type=%s event_id=%s",
            event_type,
            external_event_id,
        )
        # Mark as processed — we acknowledge the delivery; the unknown
        # event is just not actionable.
        PaymentEvent.objects.filter(pk=event_row.pk).update(processed=True)

    return JsonResponse({"status": "ok"}, status=200)


def _handle_payment_succeeded(
    event_row: PaymentEvent,
    order: Order | None,
    payload: dict[str, Any],
) -> None:
    """Flip Order to PAID + enqueue fulfilment task.

    Idempotent at three layers:

    * The :class:`PaymentEvent` UNIQUE constraint prevents this
      function being called twice for the same delivery.
    * The Order status flip is a compare-and-set scoped to
      ``status=AWAITING_PAYMENT`` so an already-paid order doesn't
      double-process.
    * The Celery fulfilment task itself rechecks status before its
      side effect, as belt-and-braces.
    """
    if order is None:
        write_audit(
            action=AUDIT_NO_ORDER,
            target="yookassa.webhook",
            payload={
                "event_id": event_row.external_event_id,
                "event_type": EVENT_PAYMENT_SUCCEEDED,
            },
        )
        PaymentEvent.objects.filter(pk=event_row.pk).update(processed=True)
        return

    # CAS on status — only flip from AWAITING_PAYMENT. PENDING (created
    # but never got a checkout URL) is also acceptable for resilience
    # against the rare ordering where the webhook beats the response
    # parsing. PAID / CANCELLED / FAILED are terminal — leave them.
    from django.utils import timezone

    rows_updated = Order.all_tenants.filter(
        pk=order.pk,
        status__in=[Order.Status.AWAITING_PAYMENT, Order.Status.PENDING],
    ).update(
        status=Order.Status.PAID,
        paid_at=timezone.now(),
    )

    PaymentEvent.objects.filter(pk=event_row.pk).update(processed=True)

    write_audit(
        action=AUDIT_PROCESSED,
        target="yookassa.webhook",
        target_id=order.id,
        payload={
            "event_id": event_row.external_event_id,
            "event_type": EVENT_PAYMENT_SUCCEEDED,
            "order_id": str(order.id),
            "flipped": rows_updated > 0,
            "amount_rub": str(order.amount_rub),
        },
    )

    if rows_updated == 0:
        logger.info(
            "yookassa_webhook.succeeded.no_flip order_id=%s status=%s",
            order.id,
            order.status,
        )
        # Don't enqueue if we didn't flip — order was already in a
        # terminal state, fulfilment already ran (or doesn't apply).
        return

    # Enqueue fulfilment AFTER the commit so a rollback (no path here
    # rolls back, but defensive) doesn't dispatch a message for a
    # paid order that didn't persist.
    from apps.orders.tasks import fulfill_paid_certificate

    transaction.on_commit(lambda: fulfill_paid_certificate.delay(str(order.id)))


def _handle_payment_canceled(
    event_row: PaymentEvent,
    order: Order | None,
    payload: dict[str, Any],
) -> None:
    """Flip Order to CANCELLED + enqueue notification task."""
    if order is None:
        write_audit(
            action=AUDIT_NO_ORDER,
            target="yookassa.webhook",
            payload={
                "event_id": event_row.external_event_id,
                "event_type": EVENT_PAYMENT_CANCELED,
            },
        )
        PaymentEvent.objects.filter(pk=event_row.pk).update(processed=True)
        return

    rows_updated = Order.all_tenants.filter(
        pk=order.pk,
        status__in=[Order.Status.AWAITING_PAYMENT, Order.Status.PENDING],
    ).update(status=Order.Status.CANCELLED)

    PaymentEvent.objects.filter(pk=event_row.pk).update(processed=True)

    write_audit(
        action=AUDIT_PROCESSED,
        target="yookassa.webhook",
        target_id=order.id,
        payload={
            "event_id": event_row.external_event_id,
            "event_type": EVENT_PAYMENT_CANCELED,
            "order_id": str(order.id),
            "flipped": rows_updated > 0,
        },
    )

    if rows_updated == 0:
        return

    from apps.orders.tasks import notify_cancelled_certificate

    transaction.on_commit(lambda: notify_cancelled_certificate.delay(str(order.id)))
