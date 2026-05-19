"""Loyalty service-layer helpers.

Three entry points cover Phase 1.a needs:

  * :func:`get_or_create_account` — lazy upsert when a customer first
    earns or redeems. No migration step for new customers.
  * :func:`credit_points` — append a positive event + bump balance.
    Idempotent on (account, EARN_VISIT, booking) — the subscriber's
    safety net against eventbus retries.
  * :func:`redeem_points` — append a negative event with bounds:
    ≥ MIN_REDEMPTION, ≤ floor balance, ≤ cap of visit price.

### Why service-layer, not model methods

- Each operation writes LoyaltyEvent + bumps account.balance in one
  transaction. That's a unit-of-work — service-layer concern.
- Anti-abuse rules (≥50 min, ≤30% cap) live here so future Owner
  config (Q-L4 → §10.3) can override them per tenant without
  touching the model.

### Audit

Every points mutation also writes an :class:`apps.audit.AuditLog`
row. Loyalty disputes are billing-grade — the audit trail and the
event log are both required for forensic defensibility.

### Idempotency

``credit_points`` for ``EARN_VISIT`` uses ``get_or_create`` on the
``(account, event_type=earn_visit, booking)`` key — guarded by the
:class:`LoyaltyEvent`'s partial unique constraint. A second invocation
with the same booking quietly returns the existing row without
double-counting.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from django.db import transaction
from django.utils import timezone

from apps.audit.services import write_audit
from apps.loyalty.models import LoyaltyAccount, LoyaltyEvent, LoyaltyReferral
from apps.tenancy.context import current_tenant

if TYPE_CHECKING:
    from apps.booking.models import BookingRequest
    from apps.identity.models import BotUser

logger = logging.getLogger(__name__)

# Anti-abuse parameters (handoff §5 «Redemption rules»).
# Future Owner config will override per tenant; constants here are the
# platform defaults.
MIN_REDEMPTION_POINTS = 50
REDEMPTION_CAP_PERCENT = 30  # of visit_price_rub

# Phase 2.d bonus event parameters (handoff §4 «Trigger events»).
LONG_RETURN_GAP_DAYS = 90
LONG_RETURN_BONUS_POINTS = 30
REFERRAL_BONUS_POINTS = 50


def get_or_create_account(customer: "BotUser") -> LoyaltyAccount:
    """Lazy upsert of a LoyaltyAccount for ``customer``.

    Reads ``current_tenant()`` from ContextVar; caller must enter
    ``tenant_scope(t)`` first. ValueError if no tenant.

    The account starts with ``balance=0, enrolled=True`` (per Q-L5
    automatic enrollment).
    """

    tenant = current_tenant()
    if tenant is None:
        raise ValueError("loyalty.get_or_create_account requires a tenant in scope.")

    account, _created = LoyaltyAccount.objects.get_or_create(
        customer=customer,
        defaults={"tenant": tenant, "balance": 0, "enrolled": True},
    )
    return account


def credit_points(
    account: LoyaltyAccount,
    *,
    points: int,
    event_type: str,
    reason: str = "",
    booking: "BookingRequest | None" = None,
    metadata: dict | None = None,
) -> LoyaltyEvent | None:
    """Credit positive points to ``account`` and append a LoyaltyEvent.

    Args:
      points: positive integer. Caller computes per earning rules
              (handoff §4).
      event_type: one of :class:`LoyaltyEvent.EventType` earn values.
      reason: human-readable note for the event row + audit log.
      booking: source BookingRequest for EARN_VISIT idempotency.
      metadata: implementation tags (correlation_id, calc breakdown).

    Returns:
      The created LoyaltyEvent, OR None when the credit was a no-op
      because (a) account opted out (Q-L12), or (b) idempotency hit —
      this booking already earned (EARN_VISIT path).

    Notes:
      The mutation happens inside ``transaction.atomic`` with row-level
      locking on the account — concurrent earners on the same account
      serialize correctly.
    """

    if points <= 0:
        raise ValueError(f"credit_points needs positive points, got {points}")

    metadata = metadata or {}

    if account.opted_out_at is not None or not account.enrolled:
        logger.info(
            "loyalty.credit.skipped_opt_out account=%s type=%s",
            account.pk,
            event_type,
        )
        return None

    # Idempotency: for EARN_VISIT, the (account, type, booking) triple is
    # the natural dedup key. A retry of the same booking.completed envelope
    # finds the row and returns it without bumping balance.
    if event_type == LoyaltyEvent.EventType.EARN_VISIT and booking is not None:
        existing = LoyaltyEvent.all_tenants.filter(
            account=account,
            event_type=LoyaltyEvent.EventType.EARN_VISIT,
            booking=booking,
        ).first()
        if existing is not None:
            logger.info(
                "loyalty.credit.idempotent_replay account=%s booking=%s",
                account.pk,
                booking.pk,
            )
            return None

    with transaction.atomic():
        locked = LoyaltyAccount.all_tenants.select_for_update().get(pk=account.pk)
        new_balance = locked.balance + points
        event = LoyaltyEvent.objects.create(
            tenant=locked.tenant,
            account=locked,
            event_type=event_type,
            points_delta=points,
            balance_after=new_balance,
            reason=reason[:200],
            booking=booking,
            metadata=metadata,
        )
        locked.balance = new_balance
        locked.save(update_fields=["balance", "updated_at"])

    write_audit(
        action=f"loyalty.{event_type}",
        target="LoyaltyAccount",
        target_id=account.pk,
        payload={
            "points_delta": points,
            "balance_after": new_balance,
            "reason": reason[:80],
            "booking_id": str(booking.pk) if booking else None,
        },
    )

    # Phase 2.d: bonus events tied to this EARN_VISIT (long-return,
    # referral completion). Run BEFORE recompute_tier so a tier transition
    # caused by this visit is reported once with both events in DB.
    if event_type == LoyaltyEvent.EventType.EARN_VISIT:
        _apply_visit_bonuses(account, current_event=event, booking=booking)

    # Phase 2.a: tier may have crossed a threshold on this EARN_VISIT.
    # Recompute eagerly — handoff §3 promises immediate tier-up celebration
    # on the visit that crossed. Skipped for non-EARN_VISIT types (manual
    # adjustments don't progress the tier ladder).
    if event_type == LoyaltyEvent.EventType.EARN_VISIT:
        recompute_tier(
            account,
            trigger_event_id=metadata.get("event_id", ""),
            correlation_id=metadata.get("correlation_id"),
        )

    return event


def _apply_visit_bonuses(
    account: LoyaltyAccount,
    *,
    current_event: LoyaltyEvent,
    booking: "BookingRequest | None",
) -> None:
    """Dispatch Phase 2.d bonus events triggered by an EARN_VISIT.

    Two bonuses (handoff §4):
      1. Long-return — previous EARN_VISIT > 90d ago → +30 EARN_RETURN
         to the visiting customer.
      2. Referral completion — this is the visiting customer's FIRST
         EARN_VISIT AND a PENDING LoyaltyReferral marks them as referee
         → +50 EARN_REFERRAL to the referrer + mark referral COMPLETED.

    Each path swallows its own errors so one bonus failure can't block
    the other or unwind the original EARN_VISIT credit.
    """

    try:
        _maybe_credit_long_return(account, current_event=current_event)
    except Exception:  # noqa: BLE001 — bonus failure never unwinds main credit
        logger.exception(
            "loyalty.bonus.long_return_failed account=%s booking=%s",
            account.pk,
            booking.pk if booking else None,
        )

    try:
        _maybe_complete_referral(account, booking=booking)
    except Exception:  # noqa: BLE001 — bonus failure never unwinds main credit
        logger.exception(
            "loyalty.bonus.referral_failed account=%s booking=%s",
            account.pk,
            booking.pk if booking else None,
        )


def _maybe_credit_long_return(
    account: LoyaltyAccount, *, current_event: LoyaltyEvent
) -> LoyaltyEvent | None:
    """Credit EARN_RETURN if the previous EARN_VISIT was > 90 days ago.

    First-ever visit: no «previous» → no bonus (we don't reward day-one
    sign-ups). Idempotency: one EARN_RETURN per booking — we anchor the
    event to the same booking as the triggering EARN_VISIT.
    """

    from datetime import timedelta

    if current_event.booking_id is None:
        return None  # safety — long-return is per-booking

    # Idempotent: already credited for this booking?
    existing = LoyaltyEvent.all_tenants.filter(
        account=account,
        event_type=LoyaltyEvent.EventType.EARN_RETURN,
        booking_id=current_event.booking_id,
    ).first()
    if existing is not None:
        return None

    prior = (
        LoyaltyEvent.all_tenants.filter(
            account=account,
            event_type=LoyaltyEvent.EventType.EARN_VISIT,
        )
        .exclude(pk=current_event.pk)
        .order_by("-occurred_at")
        .first()
    )
    if prior is None:
        return None  # first-ever visit

    gap = current_event.occurred_at - prior.occurred_at
    if gap < timedelta(days=LONG_RETURN_GAP_DAYS):
        return None

    with transaction.atomic():
        locked = LoyaltyAccount.all_tenants.select_for_update().get(pk=account.pk)
        new_balance = locked.balance + LONG_RETURN_BONUS_POINTS
        return_event = LoyaltyEvent.objects.create(
            tenant=locked.tenant,
            account=locked,
            event_type=LoyaltyEvent.EventType.EARN_RETURN,
            points_delta=LONG_RETURN_BONUS_POINTS,
            balance_after=new_balance,
            reason=f"long-return bonus (gap {gap.days}d ≥ {LONG_RETURN_GAP_DAYS}d)",
            booking_id=current_event.booking_id,
            metadata={
                "gap_days": gap.days,
                "prior_event_id": str(prior.pk),
                "trigger_event_id": str(current_event.pk),
            },
        )
        locked.balance = new_balance
        locked.save(update_fields=["balance", "updated_at"])

    write_audit(
        action="loyalty.earn_return",
        target="LoyaltyAccount",
        target_id=account.pk,
        payload={
            "points_delta": LONG_RETURN_BONUS_POINTS,
            "balance_after": new_balance,
            "gap_days": gap.days,
            "booking_id": str(current_event.booking_id),
        },
    )
    return return_event


def _maybe_complete_referral(
    account: LoyaltyAccount, *, booking: "BookingRequest | None"
) -> LoyaltyEvent | None:
    """Complete a PENDING referral if this is the customer's first visit.

    Handoff §3 «Referral»: 50 points to the referrer when the referee
    completes their first visit. Idempotent — once the LoyaltyReferral
    flips to COMPLETED, subsequent visits don't re-credit.

    Tenancy: same-tenant referrer + referee per Q-CO5 isolation (the
    unique constraint enforces 1 referee per tenant; cross-tenant has
    no link to find).
    """

    # «First visit» = current customer has exactly 1 EARN_VISIT event
    # (this just-written one). count==1 ⇒ this is it.
    visit_count = LoyaltyEvent.all_tenants.filter(
        account=account,
        event_type=LoyaltyEvent.EventType.EARN_VISIT,
    ).count()
    if visit_count != 1:
        return None  # not first

    pending = LoyaltyReferral.all_tenants.filter(
        tenant=account.tenant,
        referee_customer=account.customer,
        status=LoyaltyReferral.Status.PENDING,
    ).first()
    if pending is None:
        return None

    # Credit the referrer — load/create their account in the same tenant.
    referrer_account, _created = LoyaltyAccount.all_tenants.get_or_create(
        customer=pending.referrer_customer,
        defaults={
            "tenant": account.tenant,
            "balance": 0,
            "enrolled": True,
        },
    )

    if referrer_account.opted_out_at is not None or not referrer_account.enrolled:
        # Referrer opted out — mark referral completed (so it doesn't
        # keep blocking re-credits) but skip the payout.
        LoyaltyReferral.all_tenants.filter(pk=pending.pk).update(
            status=LoyaltyReferral.Status.COMPLETED,
            completed_booking=booking,
            completed_at=timezone.now(),
        )
        logger.info(
            "loyalty.referral.referrer_opted_out referral=%s referrer=%s",
            pending.pk,
            pending.referrer_customer_id,
        )
        return None

    with transaction.atomic():
        # Lock referrer's account row + double-check we're the winner
        # (CAS: status=PENDING). Two concurrent first-visits on the same
        # referee would race; the loser sees status=COMPLETED and skips.
        rowcount = LoyaltyReferral.all_tenants.filter(
            pk=pending.pk,
            status=LoyaltyReferral.Status.PENDING,
        ).update(
            status=LoyaltyReferral.Status.COMPLETED,
            completed_booking=booking,
            completed_at=timezone.now(),
        )
        if rowcount == 0:
            return None  # lost the race; another worker credited

        locked = LoyaltyAccount.all_tenants.select_for_update().get(pk=referrer_account.pk)
        new_balance = locked.balance + REFERRAL_BONUS_POINTS
        event = LoyaltyEvent.objects.create(
            tenant=locked.tenant,
            account=locked,
            event_type=LoyaltyEvent.EventType.EARN_REFERRAL,
            points_delta=REFERRAL_BONUS_POINTS,
            balance_after=new_balance,
            reason=f"referral converted (referee {pending.referee_customer_id})",
            booking=booking,
            metadata={
                "referral_id": str(pending.pk),
                "referee_customer_id": str(pending.referee_customer_id),
            },
        )
        locked.balance = new_balance
        locked.save(update_fields=["balance", "updated_at"])

    write_audit(
        action="loyalty.earn_referral",
        target="LoyaltyAccount",
        target_id=referrer_account.pk,
        payload={
            "points_delta": REFERRAL_BONUS_POINTS,
            "balance_after": new_balance,
            "referral_id": str(pending.pk),
            "referee_customer_id": str(pending.referee_customer_id),
        },
    )
    return event


def create_referral(
    *,
    referrer: "BotUser",
    referee: "BotUser",
) -> LoyaltyReferral | None:
    """Create a PENDING LoyaltyReferral linking referrer→referee.

    Caller must be in tenant_scope; referrer and referee must be in the
    same tenant (Q-CO5 — cross-tenant customers are separate identities).

    Returns:
      The new LoyaltyReferral, OR None when the referee already has a
      referral row (per-tenant unique constraint). The «already referred»
      case is silent (the existing referrer keeps their claim).

    No-op cases:
      - Self-referral: referrer == referee → ValueError
      - Cross-tenant: referrer.tenant_id != referee.tenant_id → ValueError
    """

    if referrer.pk == referee.pk:
        raise ValueError("loyalty.referral: self-referral not allowed")
    if referrer.tenant_id != referee.tenant_id:
        raise ValueError("loyalty.referral: referrer and referee must share tenant")

    tenant = current_tenant()
    if tenant is None:
        raise ValueError("loyalty.create_referral requires a tenant in scope.")

    referral, created = LoyaltyReferral.objects.get_or_create(
        tenant=tenant,
        referee_customer=referee,
        defaults={
            "referrer_customer": referrer,
            "status": LoyaltyReferral.Status.PENDING,
        },
    )
    if not created:
        return None  # silent: referee already has a referrer
    write_audit(
        action="loyalty.referral.created",
        target="LoyaltyReferral",
        target_id=referral.pk,
        payload={
            "referrer_id": str(referrer.pk),
            "referee_id": str(referee.pk),
        },
    )
    return referral


def redeem_points(
    account: LoyaltyAccount,
    *,
    points: int,
    visit_price_rub: int,
    booking: "BookingRequest",
    reason: str = "",
) -> LoyaltyEvent:
    """Redeem ``points`` against a booking. Returns the LoyaltyEvent.

    Bounds (handoff §5):
      - ``points`` must be ≥ :data:`MIN_REDEMPTION_POINTS`
      - ``points`` must be ≤ account.balance (no negative balance)
      - ``points`` must be ≤ floor(visit_price_rub × CAP%) — 30% default

    Raises:
      ValueError: any bound violated. Caller decides the user-facing
        message (Mini App preview endpoint owns the UX text).
    """

    if account.opted_out_at is not None or not account.enrolled:
        raise ValueError("loyalty: account opted out of program")
    if points < MIN_REDEMPTION_POINTS:
        raise ValueError(f"loyalty: minimum redemption is {MIN_REDEMPTION_POINTS} points")
    if points > account.balance:
        raise ValueError(f"loyalty: insufficient balance ({account.balance})")
    max_allowed = visit_price_rub * REDEMPTION_CAP_PERCENT // 100
    if points > max_allowed:
        raise ValueError(
            f"loyalty: redemption capped at {max_allowed} points "
            f"({REDEMPTION_CAP_PERCENT}% of {visit_price_rub} ₽)"
        )

    with transaction.atomic():
        locked = LoyaltyAccount.all_tenants.select_for_update().get(pk=account.pk)
        new_balance = locked.balance - points
        event = LoyaltyEvent.objects.create(
            tenant=locked.tenant,
            account=locked,
            event_type=LoyaltyEvent.EventType.REDEEM,
            points_delta=-points,
            balance_after=new_balance,
            reason=reason[:200] or f"redeemed against booking {booking.pk}",
            booking=booking,
            metadata={"visit_price_rub": visit_price_rub},
        )
        locked.balance = new_balance
        locked.save(update_fields=["balance", "updated_at"])

    write_audit(
        action="loyalty.redeem",
        target="LoyaltyAccount",
        target_id=account.pk,
        payload={
            "points_delta": -points,
            "balance_after": new_balance,
            "booking_id": str(booking.pk),
            "visit_price_rub": visit_price_rub,
        },
    )
    return event


def revoke_visit_points(
    account: LoyaltyAccount,
    *,
    booking: "BookingRequest",
    reason: str = "",
    metadata: dict | None = None,
) -> LoyaltyEvent | None:
    """Revoke previously earned EARN_VISIT points for ``booking``.

    Handoff §4 «Edge case: refunded visit»: when a visit is cancelled
    after points were credited (typically a post-completion cancel via
    admin or a YClients-side status flip), we negate the original
    earning so the balance reflects the real state.

    Returns:
      The created LoyaltyEvent (REFUND_REVOKE), OR None when:
      - No EARN_VISIT exists for this booking (cancellation happened
        before completion — normal path, nothing to revoke).
      - A REFUND_REVOKE already exists for this booking (idempotent
        replay of the booking.cancelled envelope).

    Balance clamp:
      Per handoff §14 «Refund chain breaks balance to negative»: balance
      MUST NOT go below 0. If the revoke amount exceeds current balance
      (customer redeemed the points already), we revoke only the amount
      down to 0 and log a «недосостояние» note in the event metadata.
    """

    metadata = metadata or {}

    earn_event = (
        LoyaltyEvent.all_tenants.filter(
            account=account,
            event_type=LoyaltyEvent.EventType.EARN_VISIT,
            booking=booking,
        )
        .order_by("occurred_at")
        .first()
    )
    if earn_event is None:
        # No earning to revoke — cancellation happened before the visit
        # completed. Silent no-op.
        logger.info(
            "loyalty.revoke.no_earn_event account=%s booking=%s",
            account.pk,
            booking.pk,
        )
        return None

    # Idempotency: already revoked?
    existing = LoyaltyEvent.all_tenants.filter(
        account=account,
        event_type=LoyaltyEvent.EventType.REFUND_REVOKE,
        booking=booking,
    ).first()
    if existing is not None:
        logger.info(
            "loyalty.revoke.idempotent_replay account=%s booking=%s",
            account.pk,
            booking.pk,
        )
        return None

    earned_amount = earn_event.points_delta  # positive integer

    with transaction.atomic():
        locked = LoyaltyAccount.all_tenants.select_for_update().get(pk=account.pk)
        # Balance clamp: cannot debit below 0. Customer may have already
        # redeemed those points elsewhere — accept partial revoke + log it.
        revoke_amount = min(earned_amount, locked.balance)
        underflow = earned_amount - revoke_amount
        new_balance = locked.balance - revoke_amount

        event_metadata = dict(metadata)
        event_metadata["earned_points"] = earned_amount
        event_metadata["revoke_amount"] = revoke_amount
        if underflow > 0:
            event_metadata["underflow"] = underflow
            event_metadata["clamp_reason"] = "balance_already_redeemed"

        event = LoyaltyEvent.objects.create(
            tenant=locked.tenant,
            account=locked,
            event_type=LoyaltyEvent.EventType.REFUND_REVOKE,
            points_delta=-revoke_amount,
            balance_after=new_balance,
            reason=reason[:200] or f"booking cancelled → revoke earn ({earn_event.pk})",
            booking=booking,
            metadata=event_metadata,
        )
        locked.balance = new_balance
        locked.save(update_fields=["balance", "updated_at"])

    write_audit(
        action="loyalty.refund_revoke",
        target="LoyaltyAccount",
        target_id=account.pk,
        payload={
            "points_delta": -revoke_amount,
            "balance_after": new_balance,
            "booking_id": str(booking.pk),
            "earned_points": earned_amount,
            "underflow": underflow,
        },
    )

    # Phase 2.a: a revoke may push the customer below the regular/favorite
    # threshold (post-completion cancellation downgrade). Recompute eagerly
    # — handoff §3 «Tier downgrade policy» kicks in on visit-count revocation.
    recompute_tier(
        account,
        trigger_event_id=metadata.get("event_id", ""),
        correlation_id=metadata.get("correlation_id"),
    )

    return event


# ── Tier thresholds (handoff §3) ────────────────────────────────────────
# Phase 2.a uses visit count only. LTV-based crossing (8 000 ₽ / 30 000 ₽)
# is deferred to Phase 2.b together with bonus-event multipliers.
TIER_REGULAR_VISIT_THRESHOLD = 4
TIER_FAVORITE_VISIT_THRESHOLD = 12


def _effective_visit_count(account: LoyaltyAccount) -> int:
    """Count EARN_VISIT events minus REFUND_REVOKE events for ``account``.

    REFUND_REVOKE is uniquely keyed by booking, so subtracting its count
    correctly cancels out the original earn — a cancelled-after-completed
    visit doesn't keep contributing to tier progress.

    Reads ``all_tenants`` because the caller may not be inside a tenant
    scope; the account-level filter is the security boundary.
    """

    return (
        LoyaltyEvent.all_tenants.filter(
            account=account, event_type=LoyaltyEvent.EventType.EARN_VISIT
        ).count()
        - LoyaltyEvent.all_tenants.filter(
            account=account, event_type=LoyaltyEvent.EventType.REFUND_REVOKE
        ).count()
    )


def _derive_tier(visit_count: int) -> str:
    """Map visit count to the tier per handoff §3 thresholds."""

    if visit_count >= TIER_FAVORITE_VISIT_THRESHOLD:
        return LoyaltyAccount.Tier.FAVORITE
    if visit_count >= TIER_REGULAR_VISIT_THRESHOLD:
        return LoyaltyAccount.Tier.REGULAR
    return LoyaltyAccount.Tier.STARTER


def recompute_tier(
    account: LoyaltyAccount,
    *,
    trigger_event_id: str | None = None,
    correlation_id: str | None = None,
) -> str | None:
    """Derive the account's current tier and persist if it changed.

    Args:
      trigger_event_id: optional ULID of the eventbus envelope that
        caused this recompute (carried into the TIER_CHANGED metadata
        for forensic linking).
      correlation_id: optional ULID for cross-bus correlation.

    Returns:
      The new tier value when it changed, OR None for a no-op
      recompute (most calls are no-ops — only every 4th and 12th
      visit cross a threshold).

    Side effects on change:
      - LoyaltyAccount.tier + tier_changed_at updated
      - LoyaltyEvent (TIER_CHANGED, points_delta=0, metadata={old, new})
      - customer.tier.changed envelope emitted on the domain bus
      - Audit log row

    Concurrency: per-account select_for_update inside transaction.atomic.
    Two concurrent EARN_VISIT calls won't double-fire TIER_CHANGED — the
    second one re-derives off the already-updated tier and finds nothing
    to change.
    """

    from apps.eventbus import services as eventbus_services

    visit_count = _effective_visit_count(account)
    new_tier = _derive_tier(visit_count)

    with transaction.atomic():
        locked = LoyaltyAccount.all_tenants.select_for_update().get(pk=account.pk)
        if locked.tier == new_tier:
            return None  # no change

        old_tier = locked.tier
        now = timezone.now()
        locked.tier = new_tier
        locked.tier_changed_at = now
        locked.save(update_fields=["tier", "tier_changed_at", "updated_at"])

        reason = f"visits={visit_count} crossed {old_tier}→{new_tier}"
        LoyaltyEvent.objects.create(
            tenant=locked.tenant,
            account=locked,
            event_type=LoyaltyEvent.EventType.TIER_CHANGED,
            points_delta=0,
            balance_after=locked.balance,
            reason=reason,
            metadata={
                "old_tier": old_tier,
                "new_tier": new_tier,
                "visit_count": visit_count,
                "trigger_event_id": trigger_event_id or "",
            },
        )

    # Emit AFTER commit so subscribers see the row.
    try:
        eventbus_services.emit_customer_tier_changed(
            customer_id=str(account.customer_id),
            old_tier=old_tier,
            new_tier=new_tier,
            reason=reason,
            correlation_id=correlation_id,
            tenant=account.tenant,
        )
    except Exception:  # noqa: BLE001 — telemetry never breaks the tier flip
        logger.exception(
            "loyalty.tier.emit_failed account=%s old=%s new=%s",
            account.pk,
            old_tier,
            new_tier,
        )

    write_audit(
        action="loyalty.tier_changed",
        target="LoyaltyAccount",
        target_id=account.pk,
        payload={
            "old_tier": old_tier,
            "new_tier": new_tier,
            "visit_count": visit_count,
        },
    )

    return new_tier


def opt_out(account: LoyaltyAccount) -> None:
    """Customer opt-out per Q-L12. Stops accrual, retains balance.

    Existing points stay redeemable until the customer explicitly
    forfeits or the tenant disables redemption.
    """

    if account.opted_out_at is not None:
        return  # already out
    account.opted_out_at = timezone.now()
    account.enrolled = False
    account.save(update_fields=["enrolled", "opted_out_at", "updated_at"])
    write_audit(
        action="loyalty.opt_out",
        target="LoyaltyAccount",
        target_id=account.pk,
        payload={"balance_at_opt_out": account.balance},
    )
