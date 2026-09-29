"""Is the catalog mirror still being refreshed? (DRF-1494)

The pilot ran twelve days on a catalog frozen at 2026-08-23 — 94 mirrored
services against 265 upstream, none of the 14 active manicure rows — and
the outage was found by the owner, not by the platform. This module is the
answer to the second, heavier half of that defect: **why nobody noticed.**

### Why nothing could have noticed

The only field that looked like a health signal was
``Tenant.last_catalog_sync_at``, and it is not one. ``apps.catalog.services.sync``
writes ``max(external_updated_at)`` into it — an upstream *content*
watermark. A salon whose catalog nobody edited for three weeks shows a
three-week-old value on a perfectly healthy contour; a salon whose sync
died three weeks ago shows the identical value. No threshold over that
column can separate the two, so no alarm was ever buildable on it, and
none was built.

Beside it, ``apps/catalog/tasks.py`` claimed in its own docstring that the
fan-out counters were "surfaced to ``check_agents`` health for 'last sync
OK across N/M tenants'". ``check_agents`` does not exist anywhere in this
repository and never did. The single sentence describing catalog-sync
monitoring was a description of something unbuilt, which is worse than
silence: it answers the question "are we watching this?" with a yes.

So the freshness question is asked here, against
``Tenant.last_catalog_sync_ok_at`` — a wall-clock stamped on every run that
actually completed.

### The threshold, and why this number

Default ``CATALOG_SYNC_STALE_AFTER_SECONDS = 3600`` (one hour) — four
consecutive beat cycles.

The floor is set by what the healthy system can legitimately do:

* the beat fires every 15 minutes (``catalog_sync_every_15min``);
* a run that cannot take the advisory lock returns ``skipped`` and waits
  for the next tick, and the lock TTL is 25 minutes, so at most **two**
  consecutive cycles can be skipped by a genuinely slow predecessor;
* that predecessor is itself bounded by ``soft_time_limit=720`` (12 min).

So 15 min is normal, 30 min is a legal skip pair, and ~45 min is the worst
combination of a slow run plus a skip behind it. One hour is the first
round number outside that envelope: it cannot be produced by healthy
behaviour, which is what makes a page actionable rather than noise.

The ceiling is set by the client on the other end of the chat. A stale
catalog is not a degraded feature — the bot states, in good faith, that a
service the salon sells does not exist. An hour bounds how long one salon
can be telling people that before somebody is told. Twelve days was the
alternative.

Tune with the env var when a contour's cadence differs; the number is a
setting precisely so a tighter pilot can ask for less.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from django.conf import settings
from django.utils import timezone

from apps.identity.constants import GLOBAL_BOT_TENANT_SLUG
from apps.tenancy.models import Tenant

DEFAULT_STALE_AFTER_SECONDS = 3600


@dataclass(frozen=True)
class TenantSyncAge:
    """How long ago this tenant's catalog sync last completed.

    ``age_seconds`` is ``None`` when the tenant has never had a successful
    run. That is deliberately not folded into "infinitely stale": the
    caller renders the two differently ("never" reads as a provisioning
    gap, a number reads as a regression), while :attr:`is_stale` treats
    them the same, because to the client asking about a manicure they are
    the same outage.
    """

    slug: str
    tenant_id: str
    last_ok_at: datetime | None
    age_seconds: float | None
    threshold_seconds: int

    @property
    def is_stale(self) -> bool:
        if self.age_seconds is None:
            return True
        return self.age_seconds > self.threshold_seconds

    @property
    def age_human(self) -> str:
        if self.age_seconds is None:
            return "never"
        minutes = int(self.age_seconds // 60)
        if minutes < 60:
            return f"{minutes}m"
        hours, minutes = divmod(minutes, 60)
        if hours < 24:
            return f"{hours}h{minutes:02d}m"
        days, hours = divmod(hours, 24)
        return f"{days}d{hours:02d}h"


def stale_after_seconds() -> int:
    return int(getattr(settings, "CATALOG_SYNC_STALE_AFTER_SECONDS", DEFAULT_STALE_AFTER_SECONDS))


def sync_ages(*, now: datetime | None = None) -> list[TenantSyncAge]:
    """Age of every syncable tenant's last successful catalog sync.

    ``global_bot`` is excluded for the same reason
    :func:`apps.catalog.tasks.sync_catalog_for_all_tenants` skips it (#1019):
    it owns tenant-less global BotUsers and discovery, not a salon catalog,
    so it is never synced and would otherwise report "never" forever —
    a permanent red that trains the reader to ignore the signal.
    """
    moment = now or timezone.now()
    threshold = stale_after_seconds()
    ages: list[TenantSyncAge] = []
    for tenant in Tenant.objects.exclude(slug=GLOBAL_BOT_TENANT_SLUG).order_by("slug"):
        last_ok = tenant.last_catalog_sync_ok_at
        ages.append(
            TenantSyncAge(
                slug=tenant.slug,
                tenant_id=str(tenant.id),
                last_ok_at=last_ok,
                age_seconds=None if last_ok is None else (moment - last_ok).total_seconds(),
                threshold_seconds=threshold,
            )
        )
    return ages


def stale_tenants(*, now: datetime | None = None) -> list[TenantSyncAge]:
    """Only the tenants whose sync age is past the threshold."""
    return [age for age in sync_ages(now=now) if age.is_stale]
