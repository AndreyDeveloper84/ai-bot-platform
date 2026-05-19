"""LoyaltySubscriber — credits points on ``booking.completed`` envelopes.

Phase 1.a wire-up (this PR): subscribe to the domain bus, lookup the
BookingRequest by id, compute the earning amount per handoff §4 default
rule, call :func:`apps.loyalty.services.credit_points`. Idempotent —
subscriber retries on the same envelope short-circuit at the service
layer.

### Earning formula (handoff §3 default)

::

    points = max(MIN_VISIT_POINTS, floor(service.price_from / 100))

  - Минимум 5 баллов за визит, даже если услуга бесплатная/без цены.
  - Price ladder: 500 ₽ = 5 points, 1 200 ₽ = 12 points, etc.

Tenant override (handoff §5 owner config) will swap the rule for a
custom one in a later PR; for now the platform default is the only
path.

### Activation

Add to the deploy env ``DOMAIN_EVENT_SUBSCRIBERS`` chain BEFORE
``AuditSubscriber`` so audit captures the post-credit state::

    DOMAIN_EVENT_SUBSCRIBERS=\\
        apps.loyalty.subscribers.LoyaltySubscriber,\\
        apps.eventbus.subscribers.AuditSubscriber

### Booking.completed wire-up — NOT YET

The taxonomy §3.1 spec'd ``booking.completed`` event has no producer
in the current codebase (it depends on Q-ATT-IMPL7 — the YClients
webhook port that flips BookingReminder lifecycle, or a future Celery
task watching ``visit_at`` past + status=CONFIRMED). Until then this
subscriber is a no-op in production — it registers, the dispatcher
calls it on every envelope, but no event_name matches.

The subscriber STILL ships now because:
  - Unit tests pin behavior end-to-end (subscriber + service + model)
  - When booking.completed wires up (separate PR), no Loyalty changes needed
  - Phase 2.2 narrative — bus subscriber lands without touching booking code
"""

from __future__ import annotations

import logging
import math
from typing import TYPE_CHECKING

from apps.loyalty import services as loyalty_services
from apps.loyalty.models import LoyaltyEvent
from apps.tenancy.context import tenant_scope

if TYPE_CHECKING:
    from apps.eventbus.envelope import Envelope

logger = logging.getLogger(__name__)

MIN_VISIT_POINTS = 5
POINTS_PER_RUB = 100  # 1 point per 100 ₽


class LoyaltySubscriber:
    """Credits points on ``booking.completed`` envelopes.

    Mapping (Envelope → LoyaltyEvent):
      - Reads ``envelope.data.booking_id``
      - Looks up BookingRequest (any tenant, FK-narrows to tenant)
      - Computes earning = max(5, floor(service.price_from / 100))
      - Calls ``credit_points(account, points, EARN_VISIT, booking=...)``

    Non-completed envelopes are silently skipped (subscribers see every
    event; one selector per subscriber).

    All exceptions are swallowed with logger.exception — the dispatcher's
    failure model treats a thrown subscriber as «this row failed,
    increment attempts». Loyalty failure on one event MUST NOT prevent
    AuditSubscriber from auditing it.
    """

    def handle(self, envelope: "Envelope") -> None:
        try:
            if envelope.event_name == "booking.completed":
                self._credit_visit(envelope)
            elif envelope.event_name == "booking.cancelled":
                self._revoke_visit(envelope)
            # All other event names: silently ignore — one selector per
            # subscriber, dispatcher hands us the full stream.
        except Exception:  # noqa: BLE001 — subscriber must never propagate
            logger.exception(
                "loyalty.subscriber.failed event=%s envelope=%s",
                envelope.event_name,
                envelope.event_id,
            )

    def _credit_visit(self, envelope: "Envelope") -> None:
        booking_id = envelope.data.get("booking_id")
        if not booking_id:
            logger.warning(
                "loyalty.subscriber.missing_booking_id envelope=%s",
                envelope.event_id,
            )
            return

        # Lazy import — BookingRequest model is loaded on Django app-ready,
        # but importing at module top creates a cycle through tenancy → audit
        # → events → eventbus → loyalty → booking. Local import is the
        # standard cycle-break per the codebase convention.
        from apps.booking.models import BookingRequest

        booking = BookingRequest.all_tenants.filter(pk=booking_id).first()
        if booking is None:
            logger.warning(
                "loyalty.subscriber.booking_not_found booking_id=%s envelope=%s",
                booking_id,
                envelope.event_id,
            )
            return

        if booking.bot_user is None:
            # GDPR-purged or wizard-only booking — no customer to credit.
            logger.info(
                "loyalty.subscriber.no_customer booking=%s",
                booking_id,
            )
            return

        points = self._compute_points(booking)
        reason = f"visit completed ({booking.service_name or 'service'})"

        # Enter the booking's tenant scope so services.get_or_create_account
        # sees the correct tenant (envelope tenant_id may be None on system
        # events, but booking.completed is always tenant-bound).
        with tenant_scope(booking.tenant):
            account = loyalty_services.get_or_create_account(booking.bot_user)
            loyalty_services.credit_points(
                account,
                points=points,
                event_type=LoyaltyEvent.EventType.EARN_VISIT,
                reason=reason,
                booking=booking,
                metadata={
                    "correlation_id": envelope.correlation_id or "",
                    "event_id": envelope.event_id,
                    "service_price_rub": _service_price(booking),
                },
            )

    def _revoke_visit(self, envelope: "Envelope") -> None:
        """Phase 1.b — revoke previously earned points on booking.cancelled.

        Handoff §4 «refunded visit» path. Looks up the BookingRequest,
        loads (or skips) the LoyaltyAccount, and asks the service layer
        to revoke. Service layer handles all the edge cases:
        - No EARN_VISIT for this booking → silent no-op (most common
          case: customer cancelled before visit completed).
        - Already revoked → idempotent no-op.
        - Balance underflow (already redeemed) → clamps at 0 + logs.
        """

        booking_id = envelope.data.get("booking_id")
        if not booking_id:
            logger.warning(
                "loyalty.subscriber.cancel.missing_booking_id envelope=%s",
                envelope.event_id,
            )
            return

        from apps.booking.models import BookingRequest
        from apps.loyalty.models import LoyaltyAccount

        booking = BookingRequest.all_tenants.filter(pk=booking_id).first()
        if booking is None:
            logger.info(
                "loyalty.subscriber.cancel.booking_not_found booking_id=%s",
                booking_id,
            )
            return

        if booking.bot_user is None:
            return  # GDPR-purged — nothing to revoke against

        account = LoyaltyAccount.all_tenants.filter(customer=booking.bot_user).first()
        if account is None:
            # No account = customer never earned anything here. Nothing
            # to revoke. (get_or_create_account is only called on credit
            # path; we don't want to create an empty account during
            # revoke flow.)
            return

        with tenant_scope(booking.tenant):
            loyalty_services.revoke_visit_points(
                account,
                booking=booking,
                reason=f"booking cancelled (event {envelope.event_id})",
                metadata={
                    "correlation_id": envelope.correlation_id or "",
                    "event_id": envelope.event_id,
                    "cancelled_by": envelope.data.get("cancelled_by", ""),
                    "cancellation_reason": envelope.data.get("cancellation_reason", ""),
                },
            )

    def _compute_points(self, booking) -> int:
        """Handoff §3 default: max(MIN_VISIT_POINTS, floor(price/100))."""
        price = _service_price(booking)
        if price <= 0:
            return MIN_VISIT_POINTS
        return max(MIN_VISIT_POINTS, math.floor(price / POINTS_PER_RUB))


def _service_price(booking) -> int:
    """Resolve the visit price for the earning formula.

    BookingRequest has a ``service`` FK to CatalogService whose
    ``price_from`` (Decimal) is the canonical price. Falls back to 0
    when missing — earning formula clamps to MIN_VISIT_POINTS in that case.

    Returns:
      Integer rubles (rounded down). Decimal precision below 1 ₽ is
      irrelevant for points calculation.
    """

    service = getattr(booking, "service", None)
    if service is None:
        return 0
    price = getattr(service, "price_from", None)
    if price is None:
        return 0
    try:
        return int(price)
    except (TypeError, ValueError):
        return 0
