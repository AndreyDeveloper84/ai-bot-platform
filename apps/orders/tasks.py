"""Celery tasks for certificate fulfilment (DRF-843 / Phase 1 / B7).

Two tasks, both invoked by the YooKassa webhook receiver via
``transaction.on_commit(lambda: task.delay(...))`` so a rollback in
the webhook handler doesn't dispatch a message for a payment that
didn't persist:

* :func:`fulfill_paid_certificate` — runs on ``payment.succeeded``.
  Best-effort sends the buyer a MAX message confirming payment +
  including the certificate redemption link.
* :func:`notify_cancelled_certificate` — runs on ``payment.canceled``.
  Sends the buyer a short "payment was cancelled" message.

### Idempotency

Both tasks re-check Order.status before doing anything. The webhook
handler has already done the CAS on status; this is belt-and-braces
against the (impossible but not strictly so) case of a stale task
firing twice. A task observing the wrong status logs + returns —
NO retry, NO exception, NO side effect.

### MAX send-failure isolation

MAX outbound failures are NOT raised — the payment already happened
and we won't reverse it. We log + write audit + return. Operations
will see the audit row and re-notify the buyer manually if needed.
The order remains paid.

### Email delivery — stub for now

If the buyer supplied a ``recipient_email``, we log a TODO marker
indicating an email *would* be sent. Actual SMTP / SES wiring is out
of scope for B7 (FT-13 / future PI track). The marker is a
grep-target so when email lands we can find every emission point.
"""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any
from uuid import UUID

from celery import shared_task  # type: ignore[import-untyped]
from django.conf import settings
from django.db import transaction
from django.utils import timezone

from apps.audit.services import write_audit
from apps.channels.max.outbound import MaxAPIError, send_message
from apps.orders.models import Order


logger = logging.getLogger(__name__)


# Audit slugs.
AUDIT_CERT_FULFILLED = "orders.certificate.fulfilled"
AUDIT_CERT_FULFILL_SKIPPED = "orders.certificate.fulfill_skipped"
AUDIT_CERT_FULFILL_SEND_FAILED = "orders.certificate.send_failed"
AUDIT_CERT_CANCELLED_NOTIFY = "orders.certificate.cancelled_notify"
AUDIT_PAYMENT_EVENT_CLEANUP = "orders.payment_event.cleanup"

# Email-delivery TODO marker — single source of truth for grep when
# the actual SMTP path lands. NEVER include this string in operator-
# facing UI; it's an internal landmark only.
EMAIL_DELIVERY_STUB_LOG = "certificate.email_delivery_stub"


def _coerce_order_id(value: UUID | str) -> UUID | None:
    """Celery serialises UUIDs as strings on the wire. Convert back.

    A malformed value (manual ``.delay()`` call with garbage) returns
    None so the caller can return cleanly without raising — Celery's
    retry-on-exception default could otherwise create an infinite
    loop of broken task deliveries.
    """
    if isinstance(value, UUID):
        return value
    try:
        return UUID(str(value))
    except (TypeError, ValueError, AttributeError):
        return None


def _format_amount(amount_rub: Any) -> str:
    """Render Decimal/float/int amount as ``"1 500"`` — no kopeks.

    Russian thousands separator is a thin space; we use a regular
    space for compatibility with channels that strip exotic
    whitespace. Matches :func:`apps.promotions.formatting.format_rub`
    output style without taking on the dependency.
    """
    try:
        whole = int(amount_rub)
    except (TypeError, ValueError):
        return str(amount_rub)
    return f"{whole:,}".replace(",", " ")


@shared_task(name="orders.fulfill_paid_certificate")
def fulfill_paid_certificate(order_id: UUID | str) -> dict[str, Any]:
    """Notify the buyer that the certificate is paid + link to redeem.

    Args:
      order_id: ``Order.id`` UUID (string form when invoked from
                ``.delay()`` — Celery serialises UUIDs as strings).

    Returns:
      A small dict for observability: ``{"status": ..., "order_id": ...}``.
      No side-effect signals are encoded in the return value (audit
      log carries those); the dict is so Celery's task-result store
      has SOMETHING meaningful on inspection.
    """
    parsed_id = _coerce_order_id(order_id)
    if parsed_id is None:
        logger.warning("orders.fulfill.bad_id raw=%r", order_id)
        return {"status": "skipped", "reason": "bad_id"}

    # Atomic re-check + transition the paid_at stamp inside a single
    # SELECT FOR UPDATE wouldn't add anything: the webhook already
    # flipped status; we just check it. A direct fetch is cheaper.
    order = Order.all_tenants.filter(pk=parsed_id).select_related("bot_user", "tenant").first()
    if order is None:
        logger.warning("orders.fulfill.no_order order_id=%s", parsed_id)
        return {"status": "skipped", "reason": "no_order", "order_id": str(parsed_id)}

    if order.status != Order.Status.PAID:
        # Webhook didn't flip → don't send. Idempotency belt-and-braces.
        write_audit(
            action=AUDIT_CERT_FULFILL_SKIPPED,
            target="orders.tasks",
            target_id=order.id,
            payload={
                "order_id": str(order.id),
                "actual_status": order.status,
                "reason": "not_paid",
            },
        )
        logger.info(
            "orders.fulfill.skipped order_id=%s status=%s",
            order.id,
            order.status,
        )
        return {
            "status": "skipped",
            "reason": "not_paid",
            "order_id": str(order.id),
        }

    # Stamp paid_at if the webhook didn't (defensive — webhook does it,
    # but a missed stamp would surface as null in admin and is cheap
    # to fix here).
    if order.paid_at is None:
        with transaction.atomic():
            Order.all_tenants.filter(pk=order.pk, paid_at__isnull=True).update(
                paid_at=timezone.now()
            )

    # ── Best-effort: send MAX confirmation ────────────────────────────
    chat_id = ""
    if order.bot_user is not None:
        chat_id = order.bot_user.chat_id or ""
    text = (
        f"Сертификат на {_format_amount(order.amount_rub)} ₽ оплачен ✅\n"
        f"Ссылка для получателя: {order.checkout_url or '—'}"
    )

    send_failed = False
    if chat_id:
        try:
            send_message(chat_id=chat_id, text=text)
        except MaxAPIError as exc:
            send_failed = True
            logger.warning(
                "orders.fulfill.send_failed order_id=%s status=%s body=%s",
                order.id,
                exc.status_code,
                exc.body[:200],
            )
            write_audit(
                action=AUDIT_CERT_FULFILL_SEND_FAILED,
                target="orders.tasks",
                target_id=order.id,
                payload={
                    "order_id": str(order.id),
                    "max_status": exc.status_code,
                },
            )
        except Exception as exc:  # noqa: BLE001
            # Defence-in-depth: a non-MaxAPIError exception (e.g. a
            # network error not classified upstream) must NOT bubble
            # up. The payment already happened; we won't unwind it.
            send_failed = True
            logger.exception(
                "orders.fulfill.send_unexpected order_id=%s exc=%s",
                order.id,
                exc,
            )
            write_audit(
                action=AUDIT_CERT_FULFILL_SEND_FAILED,
                target="orders.tasks",
                target_id=order.id,
                payload={
                    "order_id": str(order.id),
                    "exception_type": type(exc).__name__,
                },
            )

    # ── Email delivery: TODO stub ─────────────────────────────────────
    if order.recipient_email:
        # Future ticket wires SMTP / SES here. Keep this log marker so
        # we can grep every emission site once the email path lands.
        logger.info(
            "%s email=%s amount=%s order_id=%s",
            EMAIL_DELIVERY_STUB_LOG,
            order.recipient_email,
            order.amount_rub,
            order.id,
        )

    write_audit(
        action=AUDIT_CERT_FULFILLED,
        target="orders.tasks",
        target_id=order.id,
        payload={
            "order_id": str(order.id),
            "tenant_id": str(order.tenant_id),
            "amount_rub": str(order.amount_rub),
            "max_sent": (chat_id != "" and not send_failed),
            "email_recipient_present": bool(order.recipient_email),
        },
    )
    return {
        "status": "fulfilled",
        "order_id": str(order.id),
        "max_sent": (chat_id != "" and not send_failed),
    }


@shared_task(name="orders.notify_cancelled_certificate")
def notify_cancelled_certificate(order_id: UUID | str) -> dict[str, Any]:
    """Notify the buyer that the payment was cancelled.

    Triggered by the YooKassa ``payment.canceled`` webhook event.
    Sends a short MAX message — no email — and audits the outcome.
    """
    parsed_id = _coerce_order_id(order_id)
    if parsed_id is None:
        logger.warning("orders.cancel.bad_id raw=%r", order_id)
        return {"status": "skipped", "reason": "bad_id"}

    order = Order.all_tenants.filter(pk=parsed_id).select_related("bot_user", "tenant").first()
    if order is None:
        return {"status": "skipped", "reason": "no_order", "order_id": str(parsed_id)}

    if order.status != Order.Status.CANCELLED:
        return {
            "status": "skipped",
            "reason": "not_cancelled",
            "actual_status": order.status,
            "order_id": str(order.id),
        }

    chat_id = order.bot_user.chat_id if order.bot_user is not None else ""
    text = (
        f"Оплата сертификата на {_format_amount(order.amount_rub)} ₽ отменена. "
        "Если это ошибка — попробуйте ещё раз или напишите менеджеру."
    )

    sent = False
    if chat_id:
        try:
            send_message(chat_id=chat_id, text=text)
            sent = True
        except MaxAPIError as exc:
            logger.warning(
                "orders.cancel.send_failed order_id=%s status=%s",
                order.id,
                exc.status_code,
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception("orders.cancel.send_unexpected order_id=%s exc=%s", order.id, exc)

    write_audit(
        action=AUDIT_CERT_CANCELLED_NOTIFY,
        target="orders.tasks",
        target_id=order.id,
        payload={
            "order_id": str(order.id),
            "tenant_id": str(order.tenant_id),
            "max_sent": sent,
        },
    )
    return {"status": "notified", "order_id": str(order.id), "max_sent": sent}


@shared_task(name="apps.orders.tasks.cleanup_old_payment_events")
def cleanup_old_payment_events() -> int:
    """Hard-delete PaymentEvent rows older than the retention cutoff.

    Added DRF-851 / Phase 1 / PI1.

    Returns:
      Number of rows deleted.

    Reads:
      settings.PAYMENT_EVENT_RETENTION_DAYS (default 90).

    Rationale:
      :class:`apps.orders.models.PaymentEvent` is a dedup ledger of
      inbound webhook deliveries — it accumulates fast under load.
      Forensic / audit data for payments lives on :class:`Order`
      (which has its own retention contract via being a billing
      artefact); the event rows are throwaway after the dedup window
      closes. No soft-delete here — keeps the ledger trim.

    Side effects:
      Writes ``orders.payment_event.cleanup`` audit row with deleted
      count, cutoff, and retention days. Cross-tenant — no tenant
      context (PaymentEvent isn't tenant-scoped; routing happens via
      the linked Order). Audit row lands with ``tenant=None``.
    """

    from apps.orders.models import PaymentEvent

    days = int(getattr(settings, "PAYMENT_EVENT_RETENTION_DAYS", 90))
    cutoff = timezone.now() - timedelta(days=days)

    deleted, _per_model = PaymentEvent.objects.filter(received_at__lt=cutoff).delete()
    logger.info(
        "orders.payment_event.cleanup deleted=%d cutoff=%s days=%d",
        deleted,
        cutoff.isoformat(),
        days,
    )
    write_audit(
        action=AUDIT_PAYMENT_EVENT_CLEANUP,
        target="orders.PaymentEvent",
        payload={
            "deleted": deleted,
            "cutoff": cutoff.isoformat(),
            "retention_days": days,
        },
    )
    return deleted
