"""Rate + cost limits for the M6 AI drafts generate endpoint.

Two layered defences against runaway / abusive «✨ Предложить ответ»
generation, sitting on top of the existing tenant-level cap in
:mod:`apps.llm.cost_tracker`:

1. **Per-master rate limit** — fixed-window counter via
   :mod:`django.core.cache` (Redis-backed in prod, locmem in tests).
   Caps a single master at :data:`MAX_GENERATES_PER_MINUTE` per minute
   AND :data:`MAX_GENERATES_PER_DAY` per UTC-day. 429 with slug
   ``rate_limit_exceeded`` on either trip.

2. **Per-master cumulative cost cap** — sums
   :attr:`apps.conversations.models.AiDraft.llm_cost_usd` over the last
   24 hours for the master and rejects with slug ``cost_cap_exceeded``
   when the rolling total ≥ :data:`MAX_COST_PER_MASTER_USD_DAILY`.

### Why two checks?

* Rate limit catches the «fat-finger / network retry loop» pathology
  before any LLM call. Cheap (one cache hit) so it can run on every
  invocation.
* Cost cap catches the «stealth burner» pathology where each call is
  cheap individually but the cumulative spend across hundreds of
  generates over a day eats the salon's budget. Uses the DB-backed
  ``llm_cost_usd`` column as the source of truth — the cache can't
  authoritatively account across worker restarts.

Both run BEFORE the LLM call (the original cost guard ran AFTER, which
made it useless — see Blocker #3 in PR #535 follow-up). The rate limit
fires at the view layer, the cost cap fires inside the service layer
just after we acquire the per-conversation lock (Blocker #1).

### Cost cap value

:data:`MAX_COST_PER_MASTER_USD_DAILY` defaults to ``$5.00``. Rationale:
gpt-4o-mini master draft is ~$0.0005/call (~500-1000 input tokens, few
hundred output); $5 = 10,000 calls. That's well past any legitimate
per-master daily usage but still bounded enough to prevent runaway
spend if a master's UI gets into a generate loop. Tunable via
``settings.MASTER_DRAFT_COST_CAP_USD_DAILY``.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

from django.conf import settings
from django.core.cache import cache
from django.db.models import Sum
from django.utils import timezone as dj_timezone

logger = logging.getLogger(__name__)


# Defaults — overridable via settings for staging tuning.
MAX_GENERATES_PER_MINUTE = 10
MAX_GENERATES_PER_DAY = 100
MAX_COST_PER_MASTER_USD_DAILY = Decimal("5.00")
COST_WINDOW = timedelta(hours=24)

# Cache key TTLs (seconds). Minute counter only needs to outlive the
# minute it represents; day counter needs to last a full UTC day.
_TTL_MINUTE = 90  # ~ 1.5 minutes, enough to bridge a clock skew
_TTL_DAY = 90_000  # ~ 25 hours, enough to bridge a TZ skew


# ---------------------------------------------------------------------------
# DTOs + exceptions
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RateLimitResult:
    """Outcome of one rate-limit / cost-cap evaluation."""

    allowed: bool
    slug: str = ""  # "rate_limit_exceeded" | "cost_cap_exceeded" | ""
    detail: str = ""
    retry_after_seconds: int = 0


# ---------------------------------------------------------------------------
# Cache key helpers
# ---------------------------------------------------------------------------


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _minute_bucket(now: datetime) -> str:
    return now.strftime("%Y-%m-%d-%H-%M")


def _day_bucket(now: datetime) -> str:
    return now.strftime("%Y-%m-%d")


def _minute_key(master_id: str, now: datetime) -> str:
    return f"master_draft_rate:{master_id}:minute:{_minute_bucket(now)}"


def _day_key(master_id: str, now: datetime) -> str:
    return f"master_draft_rate:{master_id}:day:{_day_bucket(now)}"


# ---------------------------------------------------------------------------
# Cache-counter increment with TTL — mirrors the cost_tracker pattern.
# ---------------------------------------------------------------------------


def _incr_with_ttl(key: str, *, ttl: int) -> int:
    """INCR key by 1, ensuring TTL is set on first write of the window.

    Returns the post-INCR total. ``cache.incr`` is atomic on Redis;
    on locmem (tests) it works in-process. ``cache.add`` only sets the
    TTL when the key was absent — subsequent INCRs don't reset it, so
    the window expires naturally.

    Some backends raise ValueError when INCR-ing a missing key. We
    pre-seed via ``cache.add(key, 0, ttl)`` to avoid that, then INCR.
    """

    # Pre-seed to 0 if absent (sets the TTL). add() is a no-op if
    # the key already exists, preserving the original TTL.
    cache.add(key, 0, timeout=ttl)
    incr = getattr(cache, "incr", None)
    if callable(incr):
        try:
            new_total = incr(key, 1)
            try:
                return int(new_total) if new_total is not None else 0
            except (TypeError, ValueError):
                return 0
        except ValueError:
            pass

    # Read-modify-write fallback (locmem in some Django versions).
    current = cache.get(key)
    try:
        current_int = int(current) if current is not None else 0
    except (TypeError, ValueError):
        current_int = 0
    new_int = current_int + 1
    cache.set(key, new_int, timeout=ttl)
    return new_int


def _read_counter(key: str) -> int:
    value = cache.get(key)
    if value is None:
        return 0
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


# ---------------------------------------------------------------------------
# Limits config — read settings overrides lazily so tests can patch
# ---------------------------------------------------------------------------


def _get_per_minute_cap() -> int:
    return int(getattr(settings, "MASTER_DRAFT_RATE_PER_MINUTE", MAX_GENERATES_PER_MINUTE))


def _get_per_day_cap() -> int:
    return int(getattr(settings, "MASTER_DRAFT_RATE_PER_DAY", MAX_GENERATES_PER_DAY))


def _get_cost_cap_usd() -> Decimal:
    raw = getattr(settings, "MASTER_DRAFT_COST_CAP_USD_DAILY", MAX_COST_PER_MASTER_USD_DAILY)
    if isinstance(raw, Decimal):
        return raw
    return Decimal(str(raw))


# ---------------------------------------------------------------------------
# Public API — rate limit (view layer) + cost guard (service layer)
# ---------------------------------------------------------------------------


def check_and_consume_rate_limit(master_id: Any) -> RateLimitResult:
    """Increment per-master minute + day counters; reject on overflow.

    Called at the VIEW layer BEFORE any service work — keeps the
    blast radius to a single cache hit on the abuse path.

    Atomically increments both counters (we WANT to count the rejected
    attempt so a burst-then-cool-off doesn't fool the cap). If after
    the INCR either counter exceeds its cap → 429 with the slug.

    Retry-after derivation:
      * Per-minute trip → 60 - (current second of minute), bounded ≥1.
      * Per-day trip → seconds remaining to midnight UTC.

    Args:
      master_id: master UUID (any stringifiable id).

    Returns:
      :class:`RateLimitResult` with ``allowed`` set and (on rejection)
      a slug + retry_after_seconds.
    """

    mid = str(master_id)
    now = _now_utc()

    minute_total = _incr_with_ttl(_minute_key(mid, now), ttl=_TTL_MINUTE)
    day_total = _incr_with_ttl(_day_key(mid, now), ttl=_TTL_DAY)

    minute_cap = _get_per_minute_cap()
    day_cap = _get_per_day_cap()

    if minute_total > minute_cap:
        retry = max(1, 60 - now.second)
        logger.info(
            "ai_drafts.rate_limit.minute_exceeded master=%s count=%d cap=%d retry=%ds",
            mid,
            minute_total,
            minute_cap,
            retry,
        )
        return RateLimitResult(
            allowed=False,
            slug="rate_limit_exceeded",
            detail=f"too many draft generates: {minute_total}/{minute_cap} per minute",
            retry_after_seconds=retry,
        )

    if day_total > day_cap:
        end_of_day = now.replace(hour=23, minute=59, second=59, microsecond=0)
        retry = max(1, int((end_of_day - now).total_seconds()))
        logger.info(
            "ai_drafts.rate_limit.day_exceeded master=%s count=%d cap=%d retry=%ds",
            mid,
            day_total,
            day_cap,
            retry,
        )
        return RateLimitResult(
            allowed=False,
            slug="rate_limit_exceeded",
            detail=f"too many draft generates: {day_total}/{day_cap} per day",
            retry_after_seconds=retry,
        )

    return RateLimitResult(allowed=True)


def check_cost_cap(master_id: Any, tenant_id: Any) -> RateLimitResult:
    """Cumulative cost guard — rejects when the master has spent ≥ cap in 24h.

    Sums :attr:`AiDraft.llm_cost_usd` over the last
    :data:`COST_WINDOW` for ``(tenant, master)``. The DB column is the
    source of truth because:
      * It survives worker restarts (cache can be flushed).
      * It already exists on terminal drafts even after content is
        cleared (Blocker #5 — metadata stays for finance reconciliation).
      * The 30d purge sweep eventually zeros it out, but our 24h
        window never reaches into purged territory.

    Called inside the per-conversation lock (Blocker #1) BEFORE the
    LLM call (Blocker #3). Counts terminal + ACTIVE drafts.

    Args:
      master_id: master UUID.
      tenant_id: tenant UUID — passed explicitly because we filter
                 via ``all_tenants`` to bypass the tenant-scope manager
                 (master_api views don't wrap in tenant_scope).

    Returns:
      :class:`RateLimitResult` with ``slug="cost_cap_exceeded"`` on trip.
    """

    # Local import — AiDraft import inside ai_drafts.py is fine but
    # this module is a sibling, so we import here to avoid a circular
    # at module-load time.
    from apps.conversations.models import AiDraft

    cap = _get_cost_cap_usd()
    if cap <= Decimal(0):
        # Cap disabled by config — allow everything.
        return RateLimitResult(allowed=True)

    since = dj_timezone.now() - COST_WINDOW
    agg = AiDraft.all_tenants.filter(
        tenant_id=tenant_id,
        master_id=master_id,
        created_at__gte=since,
    ).aggregate(total=Sum("llm_cost_usd"))
    total_raw = agg.get("total")
    if total_raw is None:
        total = Decimal(0)
    elif isinstance(total_raw, Decimal):
        total = total_raw
    else:
        total = Decimal(str(total_raw))

    if total >= cap:
        logger.warning(
            "ai_drafts.cost_cap.exceeded master=%s tenant=%s total_usd=%s cap_usd=%s",
            master_id,
            tenant_id,
            total,
            cap,
        )
        return RateLimitResult(
            allowed=False,
            slug="cost_cap_exceeded",
            detail=(
                f"master daily draft cost ${total} exceeds cap ${cap}; "
                "try again tomorrow or ask the salon to raise the cap"
            ),
            # Per-master 24h rolling — no clean retry-after; use 1h as
            # a reasonable «check back later».
            retry_after_seconds=3600,
        )

    return RateLimitResult(allowed=True)


__all__ = [
    "COST_WINDOW",
    "MAX_COST_PER_MASTER_USD_DAILY",
    "MAX_GENERATES_PER_DAY",
    "MAX_GENERATES_PER_MINUTE",
    "RateLimitResult",
    "check_and_consume_rate_limit",
    "check_cost_cap",
]
