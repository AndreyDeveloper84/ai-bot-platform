"""Attribution computation — billable / billing_reason / score.

Implements the **locked billable rule** from the founder Q3 delta:

    billable = (booking_source == 'ai_direct') AND (status reaches CONFIRMED)
    billing_reason is set whenever billable is True (audit trail)
    billable is set once at attribution time, never recomputed

This is a deliberate simplification of the longer rule in
``docs/design/policies/attribution-policy.md`` §6 — the policy adds
``actor_type == 'customer'`` and ``created_by != 'execute_reschedule'``
gates that this rule does NOT enforce. The locked rule is the source of
truth for the BookingRequest writer; the policy file needs an r3
update via decisions-log batch (Q-ATT-LOCKED-1).

Score heuristic: mirrors attribution-policy §5 — pure ``ai_direct``
booking earns 1.00; everything else 0.00 unless the source explicitly
overrides (e.g. ai_assisted writer computes weighted score).
"""

from __future__ import annotations

from decimal import Decimal


def compute_billable(*, booking_source: str, status: str) -> tuple[bool, str]:
    """Return ``(billable, billing_reason)`` per locked rule.

    ``status`` must be one of :class:`BookingRequest.Status` values
    ('confirmed' / 'cancelled' / 'rescheduled'). The rule fires
    ``billable=True`` only when status is 'confirmed' (which is the
    insertion default — so freshly-created ai_direct rows are billable
    immediately).

    The caller is responsible for never re-running this on an existing
    row — :attr:`BookingRequest.billable` is set at write time and
    frozen.
    """

    if booking_source != "ai_direct":
        return (False, f"NOT billable: booking_source={booking_source}")
    if status != "confirmed":
        return (False, f"NOT billable: status={status} (must be confirmed)")
    return (True, "ai_direct + confirmed: customer-initiated via execute_confirm")


def compute_assist_score(*, booking_source: str) -> Decimal:
    """Return the analytics-only AI assist score for a booking source.

    Pure-ai_direct = 1.00; human/external = 0.00. The ai_assisted
    intermediate value is the writer's responsibility (depends on
    conversation metadata that the booking creation event doesn't carry
    natively — the writer computes and passes in).
    """

    if booking_source == "ai_direct":
        return Decimal("1.00")
    return Decimal("0.00")


def build_customer_attribution_metadata(
    *,
    conversation_id: str | None = None,
    test_mode: bool = False,
    booking_created_at: str | None = None,
) -> dict:
    """Build the minimal valid ``attribution_metadata`` for a customer
    booking from the Mini App's ``POST /api/v1/customer/bookings``.

    The customer-side writer always uses these values:

    * ``actor_type='customer'`` (per attribution-policy §4)
    * ``created_by='execute_confirm'`` (per attribution-policy §3
      ai_direct recognition rule)
    * ``started_by='customer'`` (customer-initiated by definition)

    Extra keys (``campaign_id``, ``from_inline_button`` per marketing
    Q3 delta) can be merged in by callers — the BookingRequest JSONField
    accepts arbitrary well-formed keys.
    """

    meta: dict = {
        "actor_type": "customer",
        "started_by": "customer",
        "created_by": "execute_confirm",
        "test_mode": test_mode,
    }
    if conversation_id is not None:
        meta["conversation_id"] = conversation_id
    if booking_created_at is not None:
        meta["booking_created_at"] = booking_created_at
    return meta


__all__ = [
    "build_customer_attribution_metadata",
    "compute_assist_score",
    "compute_billable",
]
