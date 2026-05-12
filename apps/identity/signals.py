"""Identity signals (DRF-527 / Sprint 6 / P1 + DRF-534 / Sprint 6 / P8).

* `auto_create_client_profile` (P1) — fires on BotUser save, ensures a
  ClientProfile row exists with default (zeroed) values. The recompute
  services (P2-P6) fill in real values; the signal just guarantees the
  row is there so downstream code can rely on ``bot_user.client_profile``
  not raising DoesNotExist.

* `booking_completed` (P1 contract / P8 handler) — Django signal defined
  here so callers (Phase 1 Booking model, Phase 0 tests) can fire it
  without importing the model that lifts it. P8 connects
  :func:`on_booking_completed` to recompute the bot_user's profile.

### P8 handler design

The handler runs synchronously inside the post-save / dispatch hook.
Phase 0 traffic volume is low (single tenant, single booking event
per ~minute peak); inline recompute is well under p95 budget. Phase 1
will move to a Celery task chain when concurrent booking storms appear.

`recompute_profile` is idempotent + tenant_scope-aware — repeated
firings are safe.
"""

from __future__ import annotations

import logging

from django.db.models.signals import post_save
from django.dispatch import Signal, receiver

from apps.identity.models import BotUser, ClientProfile
from apps.tenancy.context import current_tenant, tenant_scope

logger = logging.getLogger(__name__)


# Custom signal — Phase 0: fired from tests; Phase 1: fired from
# `Booking.save()` when the booking flips to completed.
# Keyword args: bot_user (BotUser instance), visits (iterable of
# BookingFact-shaped objects describing the bot_user's full visit
# history through the current booking), trace_id (optional).
booking_completed = Signal()


@receiver(post_save, sender=BotUser)
def auto_create_client_profile(sender, instance: BotUser, created: bool, **kwargs):
    """Ensure every BotUser has a ClientProfile row with defaults.

    Idempotent — uses get_or_create so re-saves don't error.
    """

    if not created:
        return
    ClientProfile.all_tenants.get_or_create(
        bot_user=instance,
        defaults={"tenant": instance.tenant},
    )


@receiver(booking_completed)
def on_booking_completed(sender, **kwargs):
    """Recompute ClientProfile when a booking is completed (P8 / DRF-534).

    Expected kwargs:
      - bot_user: required, the user whose booking just completed.
      - visits: required, iterable of visit-like objects (BookingFact shape).
      - trace_id: optional, pipeline trace id for forensic.

    The handler is defensive — missing kwargs / wrong shapes log a warning
    and bail. It does NOT re-raise: Django signal handlers that raise
    would propagate up to the booking-create code path and could
    rollback the user's booking transaction, which is the wrong tradeoff.
    """

    bot_user = kwargs.get("bot_user")
    visits = kwargs.get("visits")

    if bot_user is None:
        logger.warning("identity.on_booking_completed.missing_bot_user kwargs=%s", kwargs)
        return
    if visits is None:
        logger.warning("identity.on_booking_completed.missing_visits bot_user=%s", bot_user.id)
        return

    # Import locally to avoid circular: recompute imports models +
    # services which may eventually import signals.
    from apps.identity.services.recompute import recompute_profile

    try:
        # If caller is already in tenant_scope (Phase 1 Booking.save
        # path is inside a request that scoped), reuse it. Otherwise
        # enter scope for the bot_user's tenant (test path).
        if current_tenant() is None:
            with tenant_scope(bot_user.tenant):
                recompute_profile(bot_user, visits)
        else:
            recompute_profile(bot_user, visits)
    except Exception:  # noqa: BLE001 — never propagate to caller's booking txn
        logger.exception(
            "identity.on_booking_completed.recompute_failed bot_user=%s",
            bot_user.id,
        )
