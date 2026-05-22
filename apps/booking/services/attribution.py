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

Q12-α continuation chain (issue #478, founder ACK 2026-05-22):
    Reschedules are NOT a billable event when they preserve the
    continuation chain (same service, within 90d of the chain root,
    no prior cancel). When the chain breaks (service swap, >90d,
    partial-failure terminator), the new row IS billable as a fresh
    sale. See :func:`compute_reschedule_continuation` for the decision
    helper and :func:`compute_billable` ``is_reschedule_continuation``
    / ``chain_break_reason`` kwargs for the writer.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from decimal import Decimal
from typing import TYPE_CHECKING
from uuid import UUID

logger = logging.getLogger(__name__)

if TYPE_CHECKING:  # avoid circular import at runtime
    from apps.booking.models import BookingRequest


# Q12-α threshold: founder ACK 2026-05-22 — 90 days from chain root
# visit_at. Strictly greater than → break; exactly equal → continuation.
RESCHEDULE_CONTINUATION_THRESHOLD_DAYS = 90


def _normalise_service_id(service_id_value: object) -> str:
    """Coerce a service identifier (UUID / str / int / None) to a
    comparable string. ``None`` → ``""`` so chains where neither old
    nor new has a service FK can pass through.

    Q12-α adversarial-pass B1: the LLM tool path (`apps/skills/booking/
    tools.py::execute_reschedule`) creates rows WITHOUT setting the
    ``service`` FK — so ``booking.service_id`` is None. Pre-fix the
    helper compared ``str(None)`` (= "None") vs ``""``, falsely flagging
    every LLM reschedule as a service_swap → 100 % of LLM reschedules
    silently overbilled. This helper normalises both sides so an
    «old None, new None/""» pair passes the swap check.
    """

    if service_id_value is None:
        return ""
    return str(service_id_value)


def compute_billable(
    *,
    booking_source: str,
    status: str,
    created_by: str = "execute_confirm",
    is_reschedule_continuation: bool = False,
    chain_break_reason: str | None = None,
) -> tuple[bool, str]:
    """Return ``(billable, billing_reason)`` per locked rule.

    ``status`` must be one of :class:`BookingRequest.Status` values
    ('confirmed' / 'cancelled' / 'rescheduled'). The rule fires
    ``billable=True`` only when status is 'confirmed'.

    Q12-α (issue #478): when ``created_by='execute_reschedule'``, the
    caller MUST compute continuation and pass the result via
    ``is_reschedule_continuation`` (+ ``chain_break_reason`` when
    False). The default for a reschedule with neither kwarg set is
    «chain broken» — better to over-charge once than to silently
    undercharge the salon.

    The caller is responsible for never re-running this on an existing
    row — :attr:`BookingRequest.billable` is set at write time and
    frozen.
    """

    # Adversarial-pass D2: refuse contradictory kwargs at function entry
    # — `is_reschedule_continuation=True` AND `chain_break_reason="..."`
    # would silently produce wrong audit. Better to raise loud than to
    # let the True branch win + leave an inconsistent finance row.
    if is_reschedule_continuation and chain_break_reason:
        raise ValueError(
            "compute_billable: is_reschedule_continuation=True is "
            "incompatible with a non-None chain_break_reason "
            f"({chain_break_reason!r}). Caller bug."
        )

    if booking_source != "ai_direct":
        return (False, f"NOT billable: booking_source={booking_source}")
    if status != "confirmed":
        return (False, f"NOT billable: status={status} (must be confirmed)")
    if created_by == "execute_reschedule":
        if is_reschedule_continuation:
            return (False, "reschedule_continuation")
        # Chain broken or caller forgot to compute continuation.
        # Default to «new sale» (billable=True) so we never silently
        # undercharge. Tag the reason so finance can grep.
        reason_tag = chain_break_reason or "missing_continuation_signal"
        return (True, f"reschedule_chain_broken: {reason_tag}")
    return (True, "ai_direct + confirmed: customer-initiated via execute_confirm")


def compute_reschedule_continuation(
    *,
    old: "BookingRequest",
    new_service_id: str | UUID | int | None,
    new_visit_at: datetime,
    threshold_days: int = RESCHEDULE_CONTINUATION_THRESHOLD_DAYS,
) -> tuple[bool, str | None, UUID | None]:
    """Decide whether a reschedule preserves the continuation chain.

    Per founder ACK 2026-05-22 (issue #478 close-out), the chain is
    preserved when ALL of the following hold:

    * **Same service_id** (strict equality — no category/price-equivalent
      lookup; founder explicit «strict service_id equality»).
    * **Within ``threshold_days`` of chain ROOT visit_at** (not the
      most-recent reschedule — the threshold is anchored to the original
      sale so the customer can't extend a non-billable chain forever by
      rescheduling every 89 days). Default 90.
    * (Implicit) The chain root is reachable — i.e. ``old`` is
      CONFIRMED. The CONFIRMED-only precondition is enforced upstream
      by :func:`apps.booking.services.reschedule.reschedule_customer_booking`;
      this helper trusts the caller.

    The «cancel breaks chain» rule from the founder ACK is enforced
    structurally — a cancelled row can't be the source of a reschedule
    (the service-layer check at ``services/reschedule.py`` rejects
    non-CONFIRMED status), so a continuation can't span a cancel.
    Partial-failure leaves the old row CANCELLED and writes NO new
    row, so there's no continuation surface to break.

    Args:
      old: The existing :class:`BookingRequest` being rescheduled.
        Already locked + verified CONFIRMED by the caller.
      new_service_id: The proposed new service identifier. ``str`` or
        ``int`` or ``UUID`` accepted; compared via ``==`` against
        ``old.service_id`` (the LLM tool path stores strings; the
        service-layer path stores UUIDs — both compare cleanly via
        ``str(...)`` coercion). Pass the value the caller intends to
        write on the new row.
      new_visit_at: The proposed new ``visit_at`` datetime.
      threshold_days: Override for tests. Production callers must use
        the default (90) — the founder-ACK'd threshold.

    Returns:
      ``(is_continuation, chain_break_reason, chain_root_id)``:

      * ``is_continuation`` — ``True`` when all chain-preservation
        conditions hold.
      * ``chain_break_reason`` — short tag (``"service_swap"`` /
        ``"over_90d"``) when False; ``None`` when True.
      * ``chain_root_id`` — the chain root's ``id`` when continuation
        (write this onto the new row's ``original_booking_event_id``);
        ``None`` when chain is broken (write None to start a fresh chain).
    """

    # Resolve the chain root: either ``old`` itself (if ``old`` is the
    # root) or the row ``old.original_booking_event_id`` points at.
    # We trust the FK invariant: a continuation row's ``original_booking_event_id``
    # always points at the ROOT, not at the immediate predecessor —
    # that's what the writers in services/reschedule.py and
    # skills/booking/tools.py enforce. Single-hop chain walk.
    from apps.booking.models import BookingRequest

    if old.original_booking_event_id is not None:
        # ``old`` is itself a continuation — its FK column already points
        # at the chain ROOT (not the immediate predecessor — that's the
        # invariant the writers enforce). Use ``all_tenants`` so a
        # tenant-active flag rotation can't blind the lookup; the
        # caller already holds ``tenant_scope(tenant)``.
        try:
            root = BookingRequest.all_tenants.get(id=old.original_booking_event_id)
        except BookingRequest.DoesNotExist:
            # Root deleted (shouldn't happen under PROTECT — defence
            # in depth). Treat as chain broken. Tech-lead double-pass
            # N2: ERROR-log so ops sees the PROTECT-bypass signal.
            logger.error(
                "billing.q12a.chain_root_missing old_id=%s root_id=%s "
                "tenant_id=%s — PROTECT FK bypassed, treating as new chain",
                old.id,
                old.original_booking_event_id,
                old.tenant_id,
            )
            return (False, "chain_root_missing", None)

        # Adversarial-pass D1: defence-in-depth — `original_booking_event_id`
        # MUST point at a ROOT (i.e. a row whose own
        # ``original_booking_event_id`` is None). If a DB-tampering /
        # buggy writer left a non-root in the FK, the 90d window would
        # be measured from the wrong anchor. Treat as chain broken
        # rather than trust the inconsistent pointer. Tech-lead N2:
        # ERROR-log so ops sees the invariant violation.
        if root.original_booking_event_id is not None:
            logger.error(
                "billing.q12a.chain_root_invariant_violated old_id=%s "
                "claimed_root_id=%s claimed_root.original_booking_event_id=%s "
                "tenant_id=%s — writer/DB corruption suspected",
                old.id,
                root.id,
                root.original_booking_event_id,
                old.tenant_id,
            )
            return (False, "chain_root_invariant_violated", None)
    else:
        root = old

    # Adversarial-pass B2: «cancel breaks chain» applies to the ROOT, not
    # just to ``old``. The structural CONFIRMED check on ``old`` doesn't
    # catch the case where the root has been cancelled (manual data fix,
    # GDPR erasure stub, audit job, etc.) while ``old`` (a non-root
    # link) is still CONFIRMED. Founder rule #1 is explicit — a
    # cancelled chain anywhere terminates the continuation.
    if root.status == BookingRequest.Status.CANCELLED:
        return (False, "chain_root_cancelled", None)

    # 1. Strict service equality. Normalise both sides via
    # ``_normalise_service_id`` so the LLM tool path (where rows lack
    # the ``service`` FK — see B1 adversarial-pass note) doesn't
    # falsely register a swap from ``str(None) != ""``. Both sides
    # ``""`` (no FK on either) → no swap, trust the caller's higher-
    # layer enforcement.
    new_svc = _normalise_service_id(new_service_id)
    if _normalise_service_id(old.service_id) != new_svc:
        # ``old.service_id`` is the most-recent link's service; if THAT
        # differs from the proposed new service, the chain breaks here.
        return (False, "service_swap", None)
    if _normalise_service_id(root.service_id) != new_svc:
        # Defence-in-depth: the chain root's service must match too.
        return (False, "service_swap", None)

    # 2. 90-day threshold measured from ROOT visit_at.
    if root.visit_at is None:
        # Legacy row without visit_at can't anchor a chain. Treat as
        # missing root → chain broken (fresh sale). Tech-lead N2:
        # ERROR-log so ops sees that a legacy pre-Phase-1 row got
        # caught in a chain — likely needs data backfill.
        logger.error(
            "billing.q12a.chain_root_no_visit_at root_id=%s tenant_id=%s "
            "— legacy row, fresh-sale fallback",
            root.id,
            root.tenant_id,
        )
        return (False, "chain_root_no_visit_at", None)
    # Adversarial-pass B4: defensive guard against TZ-naive new_visit_at.
    # ``root.visit_at`` is TZ-aware (Django USE_TZ=True). A naive
    # ``new_visit_at`` would raise `TypeError: can't compare offset-
    # naive and offset-aware datetimes` mid-transaction → rollback +
    # split-brain in the LLM tool path (YClients already cancelled).
    # Convert defensively by adopting root's tzinfo if naive; this is
    # safe because the caller's intent is «same wall-clock instant».
    if new_visit_at.tzinfo is None:
        new_visit_at = new_visit_at.replace(tzinfo=root.visit_at.tzinfo)
    # Founder phrasing: «больше чем на 90 дней» = strictly greater.
    # Exactly 90d is continuation; 90d + 1 microsecond breaks.
    if new_visit_at > root.visit_at + timedelta(days=threshold_days):
        return (False, "over_90d", None)

    return (True, None, root.id)


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
    created_by: str = "execute_confirm",
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
        "created_by": created_by,
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
