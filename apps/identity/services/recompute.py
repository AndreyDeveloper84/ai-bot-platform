"""ClientProfile recompute orchestrator (DRF-532 / Sprint 6 / P6).

Glues P2 RFM + P3 LTV + P4 churn + P5 tier services into a single
write to :class:`ClientProfile`. The Celery beat task `recompute_profiles_daily`
(P7) calls this for every active bot_user; the `booking_completed` signal
(P8) calls it for one user on real-time updates.

### Inputs

- ``bot_user``: a :class:`BotUser` instance under an active tenant_scope.
- ``visits``: iterable of :class:`BookingFact`-shaped objects. Phase 0
  callers pass synthetic visits from :mod:`tests.fixtures.synthetic_visits`;
  Phase 1 callers pass a queryset adapter over the real Booking model.
  Signature stable across phases.

### Side effects

1. Computes RFM (P2), LTV (P3), churn (P4), tier (P5) — pure functions,
   no I/O during compute.
2. UPSERT-writes the ClientProfile row under ``tenant_scope`` so the
   default manager filter sees it.
3. Stamps ``last_recomputed_at`` to ``timezone.now()``.
4. Emits ``client_profile_recomputed`` event (B6 vocab) carrying the
   full RFM/LTV/churn/tier snapshot — observability + downstream
   experimentation hooks consume.

### Why not a Celery task itself

Keeping ``recompute_profile`` as a sync function gives three callers
the same contract: the beat task (P7), the signal handler (P8), and
shell-invoked maintenance scripts. The beat task wraps this in a Celery
task; the signal handler calls it directly inside the post_save hook.

### Tenant scope

Caller MUST enter ``tenant_scope(bot_user.tenant)`` before calling.
The function reads ``current_tenant()`` to verify scope alignment and
raises ``ValueError`` on mismatch — this is a programming error guard,
not a runtime exception path.
"""

from __future__ import annotations

import logging
from typing import Iterable

from django.utils import timezone

from apps.events.services import emit
from apps.events.vocabulary import CLIENT_PROFILE_RECOMPUTED
from apps.identity.models import BotUser, ClientProfile
from apps.identity.services.churn import compute_churn_risk
from apps.identity.services.ltv import compute_ltv
from apps.identity.services.rfm import compute_rfm
from apps.identity.services.tier import compute_tier
from apps.tenancy.context import current_tenant

logger = logging.getLogger(__name__)


def recompute_profile(bot_user: BotUser, visits: Iterable) -> ClientProfile:
    """Recompute every derived field for one bot_user.

    Args:
      bot_user: target :class:`BotUser`.
      visits: iterable of visit-like objects (``.visited_at``, ``.amount``).

    Returns:
      The persisted :class:`ClientProfile` instance.

    Raises:
      ValueError: if ``current_tenant()`` doesn't match ``bot_user.tenant``
        (programming error — caller forgot to enter tenant_scope).
    """

    tenant = current_tenant()
    if tenant is None or tenant.id != bot_user.tenant_id:
        raise ValueError(
            "recompute_profile must be called inside tenant_scope(bot_user.tenant); "
            f"got current_tenant={tenant} bot_user.tenant_id={bot_user.tenant_id}"
        )

    # Materialise once — services need to iterate twice (RFM frequency
    # + LTV cadence). list() is cheap for typical bot_user histories.
    visits_list = list(visits)

    rfm = compute_rfm(visits_list)
    ltv = compute_ltv(visits_list)

    # avg_visit_interval_days — derive from RFM aggregates. Phase 0
    # heuristic: span(visited_at) / max(N-1, 1). NULL when N < 2.
    avg_interval = _avg_interval_days(visits_list)

    churn = compute_churn_risk(
        recency_days=rfm.recency_days,
        avg_visit_interval_days=avg_interval,
        frequency_visits=rfm.frequency_visits,
    )

    tier = compute_tier(
        monetary_total=rfm.monetary_total,
        frequency_visits=rfm.frequency_visits,
    )

    profile, _created = ClientProfile.all_tenants.update_or_create(
        bot_user=bot_user,
        defaults={
            "tenant": bot_user.tenant,
            "recency_days": rfm.recency_days,
            "frequency_visits": rfm.frequency_visits,
            "monetary_total": rfm.monetary_total,
            "rfm_segment": rfm.rfm_segment,
            "ltv": ltv.total_spend,
            "predicted_ltv_12m": ltv.predicted_ltv_12m,
            "churn_risk": churn.churn_risk,
            "lifecycle_stage": churn.lifecycle_stage,
            "avg_visit_interval_days": avg_interval,
            "loyalty_tier": tier,
            "last_recomputed_at": timezone.now(),
        },
    )

    emit(
        CLIENT_PROFILE_RECOMPUTED,
        distinct_id=str(bot_user.id),
        properties={
            "rfm_segment": rfm.rfm_segment,
            "frequency_visits": rfm.frequency_visits,
            "monetary_total": str(rfm.monetary_total),
            "lifecycle_stage": churn.lifecycle_stage,
            "loyalty_tier": tier,
            "churn_risk": churn.churn_risk,
            "predicted_ltv_12m": str(ltv.predicted_ltv_12m),
        },
    )

    logger.info(
        "identity.recompute_profile bot_user=%s segment=%s tier=%s stage=%s",
        bot_user.id,
        rfm.rfm_segment,
        tier,
        churn.lifecycle_stage,
    )

    return profile


def _avg_interval_days(visits: list) -> int | None:
    """Derive avg_visit_interval_days from a visit list.

    Phase 0 heuristic mirroring RFM/LTV: span(first..last) / max(N-1, 1).
    Returns None when N < 2 — interval is undefined for single visits.
    """

    if len(visits) < 2:
        return None

    visited_dates = [v.visited_at for v in visits]
    span_days = (max(visited_dates) - min(visited_dates)).days
    return max(span_days // (len(visits) - 1), 0)
