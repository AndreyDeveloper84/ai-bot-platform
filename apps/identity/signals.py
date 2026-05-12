"""Identity signals (DRF-527 / Sprint 6 / P1).

* `auto_create_client_profile` — fires on BotUser save, ensures a
  ClientProfile row exists with default (zeroed) values. The
  recompute services (P2-P6) fill in real values; the signal just
  guarantees the row is there so downstream code can rely on
  ``bot_user.client_profile`` not raising DoesNotExist.

* `booking_completed` — Django signal **defined here** so callers
  (Phase 1 Booking model, Phase 0 tests) can fire it without
  importing the model that lifts it. P8 wires a handler that calls
  `recompute_profile(bot_user)`.
"""

from __future__ import annotations

from django.db.models.signals import post_save
from django.dispatch import Signal, receiver

from apps.identity.models import BotUser, ClientProfile


# Custom signal — Phase 0: fired from tests; Phase 1: fired from
# `Booking.save()` when the booking flips to completed.
# Keyword args: bot_user (BotUser instance), booking (optional opaque
# payload describing the visit), trace_id (optional).
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
