"""Per-tenant daily LLM cost + token cap (Phase 1 / PI9 / DRF-860).

Generalises the Sprint 7 / L7 per-MODEL Anthropic gate
(``apps.llm.providers.anthropic_provider._enforce_daily_token_cap``)
to per-TENANT, and applies it uniformly to BOTH the OpenAI and
Anthropic providers via a single shared module.

### Two independent budgets

Each tenant has TWO daily caps:

* ``Tenant.daily_token_cap`` — total tokens consumed (input + output,
  completion + embedding).
* ``Tenant.daily_cost_cap_usd`` — total USD spend (computed via
  :mod:`apps.llm.pricing`).

Either cap can trip independently. A tenant on the gpt-4o-mini cheap
path might exhaust the token cap long before the cost cap; a tenant
that occasionally uses gpt-4o on a high-context turn might exhaust the
cost cap on a small token count. We enforce both.

### Redis key shape

::

    llm_cost:<tenant_id>:<YYYY-MM-DD-UTC>:tokens          int counter
    llm_cost:<tenant_id>:<YYYY-MM-DD-UTC>:cost_microcents int counter
    llm_cost:<tenant_id>:<YYYY-MM-DD-UTC>:warned_80       presence flag
    llm_cost:<tenant_id>:<YYYY-MM-DD-UTC>:warned_100      presence flag

TTL = 86400s. Set on first write of the day; subsequent ``incr`` does
NOT reset TTL — once midnight UTC rolls past the original first-write,
the key expires naturally. The date in the key suffix means even if a
TTL race ever did briefly survive past midnight, the new day's reads
would hit a fresh key and start at 0.

### Cost stored as microcents (integer)

USD cost is stored as an integer count of microcents (1 USD =
100_000_000 microcents — 10^8 scale chosen so even the cheapest
embedding token cost rounds to ≥1 unit). Redis ``INCRBY`` is integer-
atomic; ``INCRBYFLOAT`` is supported but has drift caveats on very
small increments. Integer microcents avoid the drift and let us share
one ``incr`` codepath with the token counter.

### Concurrency

``cache.incr`` is atomic in Redis. The window between "read counter to
check cap" and "incr after successful call" is two operations — a
runaway tenant could overshoot by the size of one in-flight burst.
This is bounded (single-digit calls beyond cap) and acceptable; the
brief said "don't add Lua/locking", so we don't.

### Alerting

After every ``record_usage`` we compute the previous-vs-new percent of
each cap. Crossing 80% → one warning to ``tenant.manager_chat_id``;
crossing 100% → one "exhausted" alert. Both deduplicated via
``warned_80`` / ``warned_100`` Redis flags so subsequent calls don't
re-spam the manager. Empty manager_chat_id → log + skip the outbound
(cap is still enforced, telemetry still written).

### Exception contract

:class:`TenantQuotaExceeded` raised by :func:`enforce_caps` carries
``which_cap`` (``"token"`` | ``"cost"``), ``cap_value``,
``current_value`` so the orchestrator can write a precise audit row
when it catches the exception and serves the static fallback.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Literal

from django.core.cache import cache

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Module constants
# ---------------------------------------------------------------------------

# 24-hour TTL with UTC midnight ISO date in the key: natural rollover
# without needing a midnight cron tick.
_KEY_PREFIX = "llm_cost"
_TTL_SECONDS = 24 * 60 * 60

# 1 USD = 10^8 microcents. Integer scale chosen so even the cheapest
# embedding token cost rounds to ≥1 unit (text-embedding-3-small at
# $0.02 / 1M tokens → 2 microcents per token).
_MICROCENTS_PER_USD = Decimal(10**8)

# Threshold-crossing alerts. 80% = warning, 100% = exhausted. Both
# deduplicated per-tenant per-day via the warned_* flags.
_WARN_THRESHOLD = Decimal("0.80")
_EXHAUST_THRESHOLD = Decimal("1.00")

# Canonical event slug — same one the Sprint 7 / L7 per-model gate
# uses. Payload is extended (tenant_id + which_cap) but event_name
# stays stable so existing subscribers keep matching.
EVENT_PROVIDER_QUOTA_EXCEEDED = "llm.provider_quota_exceeded"

# Audit action slug used by the orchestrator when it catches
# TenantQuotaExceeded and serves the static fallback. Distinct from
# the event slug — events go to the analytics fanout, audits go to
# the per-tenant forensic table.
AUDIT_QUOTA_FALLBACK = "llm.quota_exhausted_fallback"


# Russian-language alert templates. Sent via the existing MAX outbound
# channel to ``tenant.manager_chat_id`` when a daily threshold is
# crossed. Both lines fit a single MAX message body (no media).
_ALERT_80_TEMPLATE = (
    "⚠️ LLM-расходы за сегодня дошли до 80% дневного лимита "
    "(tokens={tokens_used}/{token_cap} или ${cost_used}/${cost_cap}). "
    "При необходимости пересмотрите cap в админке."
)
_ALERT_100_TEMPLATE = (
    "🚨 LLM-расходы исчерпали дневной лимит. Бот переключился на "
    'fallback "Извините, лимит на сегодня исчерпан". Сброс в 00:00 UTC.'
)


# ---------------------------------------------------------------------------
# DTOs + exceptions
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CurrentUsage:
    """Snapshot of one tenant's day-so-far LLM consumption.

    Returned by :func:`get_current_usage` for admin / dashboard reads.
    """

    tokens_used: int
    cost_used_usd: Decimal
    token_cap: int
    cost_cap_usd: Decimal
    percent_of_token_cap: float
    percent_of_cost_cap: float


class TenantQuotaExceeded(Exception):
    """Raised by :func:`enforce_caps` when either daily cap is at 100%.

    Attributes:
      tenant_id: stringified UUID of the tenant whose cap tripped.
      which_cap: ``"token"`` or ``"cost"`` — which of the two budgets
                 caused the rejection.
      cap_value: the configured cap (int tokens or Decimal USD).
      current_value: the day-so-far counter at the time of refusal.

    The orchestrator catches this, returns a static Russian "лимит
    исчерпан" fallback, and writes an audit row with the same shape.
    """

    def __init__(
        self,
        *,
        tenant_id: str,
        which_cap: Literal["token", "cost"],
        cap_value: int | Decimal,
        current_value: int | Decimal,
    ) -> None:
        self.tenant_id = tenant_id
        self.which_cap: Literal["token", "cost"] = which_cap
        self.cap_value = cap_value
        self.current_value = current_value
        super().__init__(
            f"TenantQuotaExceeded(tenant={tenant_id}, cap={which_cap}, "
            f"current={current_value}, limit={cap_value})"
        )


# ---------------------------------------------------------------------------
# Public API — get / enforce / record
# ---------------------------------------------------------------------------


async def get_current_usage(tenant_id: str) -> CurrentUsage:
    """Return the day-so-far usage snapshot for a tenant.

    Reads ``Tenant.daily_token_cap`` + ``Tenant.daily_cost_cap_usd``
    via a sync_to_async ORM hop, then layers the Redis-side counters
    on top. Missing counters → 0 (no key yet for today).
    """
    from asgiref.sync import sync_to_async

    token_cap, cost_cap_usd = await sync_to_async(_read_tenant_caps, thread_sensitive=False)(
        tenant_id
    )

    tokens_used = _read_tokens(tenant_id)
    cost_used = _read_cost_usd(tenant_id)

    percent_tokens = (tokens_used / token_cap) if token_cap > 0 else 0.0
    percent_cost = float(cost_used / cost_cap_usd) if cost_cap_usd > 0 else 0.0

    return CurrentUsage(
        tokens_used=tokens_used,
        cost_used_usd=cost_used,
        token_cap=token_cap,
        cost_cap_usd=cost_cap_usd,
        percent_of_token_cap=float(percent_tokens),
        percent_of_cost_cap=percent_cost,
    )


async def enforce_caps(
    tenant_id: str,
    *,
    projected_tokens: int = 0,
) -> None:
    """Pre-call gate. Raises when either cap is at or above 100%.

    Args:
      tenant_id: stringified tenant UUID.
      projected_tokens: optional pre-emptive overshoot guard. When >0
        we check ``(current + projected) >= cap`` for the token cap
        only (cost projection would require knowing the model up-front,
        which the caller doesn't always have). Helps reject a known-
        large prompt before burning the call.

    Raises:
      :class:`TenantQuotaExceeded` with ``which_cap`` set to ``"token"``
      or ``"cost"`` — whichever tripped first. Token check runs first
      so a tenant burning fast on cheap models sees the token-cap line.
    """
    from asgiref.sync import sync_to_async

    token_cap, cost_cap_usd = await sync_to_async(_read_tenant_caps, thread_sensitive=False)(
        tenant_id
    )

    tokens_used = _read_tokens(tenant_id)
    cost_used = _read_cost_usd(tenant_id)

    # Token cap — including the projected pre-emptive guard. We use
    # `>=` for the projection (would-cross-or-equal-cap) and the cap
    # value itself as the "already exhausted" reject. Both paths raise
    # with which_cap="token".
    if tokens_used >= token_cap or (
        projected_tokens > 0 and tokens_used + projected_tokens > token_cap
    ):
        await sync_to_async(_write_quota_telemetry, thread_sensitive=False)(
            tenant_id=tenant_id,
            which_cap="token",
            cap_value=token_cap,
            current_value=tokens_used,
        )
        raise TenantQuotaExceeded(
            tenant_id=tenant_id,
            which_cap="token",
            cap_value=token_cap,
            current_value=tokens_used,
        )

    if cost_used >= cost_cap_usd:
        await sync_to_async(_write_quota_telemetry, thread_sensitive=False)(
            tenant_id=tenant_id,
            which_cap="cost",
            cap_value=cost_cap_usd,
            current_value=cost_used,
        )
        raise TenantQuotaExceeded(
            tenant_id=tenant_id,
            which_cap="cost",
            cap_value=cost_cap_usd,
            current_value=cost_used,
        )


async def record_usage(
    tenant_id: str,
    *,
    tokens: int,
    cost_usd: Decimal,
) -> None:
    """Post-call accumulator. INCRs both counters and fires threshold
    alerts on cross-boundary moves (80%, 100%).

    Args:
      tenant_id: stringified tenant UUID.
      tokens: total tokens consumed by this call (input + output).
      cost_usd: USD cost from :func:`apps.llm.pricing.compute_cost`.

    Behaviour:
      - Increments ``tokens`` and ``cost_microcents`` keys; sets TTL on
        first write of the day per tenant.
      - Reads ``Tenant.manager_chat_id`` + caps via sync ORM.
      - If the new usage crossed 80% or 100% on EITHER cap, sends one
        outbound MAX message (deduplicated via the warned_* flags).
      - Telegram alert failure (no token, network error) logs WARN
        and returns — the accounting write succeeded; cap enforcement
        is unaffected.
    """
    from asgiref.sync import sync_to_async

    if tokens < 0 or cost_usd < Decimal(0):
        # Defensive: never decrement. A negative input here would be a
        # caller bug; we'd rather record 0 than corrupt the counter.
        logger.warning(
            "cost_tracker.record_usage.negative_input tenant=%s tokens=%s cost=%s",
            tenant_id,
            tokens,
            cost_usd,
        )
        return

    # Snapshot the current values BEFORE we increment — we need the
    # "previous" side of the cross-threshold check. The new values
    # come from the post-INCR atomic return so we don't race against
    # concurrent record_usage calls (LLM retro B3).
    previous_tokens = _read_tokens(tenant_id)
    previous_cost = _read_cost_usd(tenant_id)

    # Increment counters. _incr_with_ttl returns the post-INCR total
    # (atomic per Redis INCRBY) so two concurrent record_usage calls
    # see distinct ``new_*`` values that reflect the actual cumulative
    # state — pre-fix both calls computed ``new = previous + delta``
    # from the SAME snapshot of ``previous``, missing each other's
    # increment and either firing the 80% alert twice or skipping it
    # entirely when neither call individually crossed the threshold.
    new_tokens_post = (
        _incr_with_ttl(_tokens_key(tenant_id), tokens) if tokens > 0 else previous_tokens
    )
    cost_microcents_delta = _usd_to_microcents(cost_usd)
    if cost_microcents_delta > 0:
        new_cost_microcents = _incr_with_ttl(_cost_key(tenant_id), cost_microcents_delta)
        new_cost = _microcents_to_usd(new_cost_microcents)
    else:
        new_cost = previous_cost

    new_tokens = new_tokens_post

    # Read caps + manager_chat_id for the alert path.
    try:
        token_cap, cost_cap_usd, manager_chat_id = await sync_to_async(
            _read_tenant_alert_context, thread_sensitive=False
        )(tenant_id)
    except Exception:  # noqa: BLE001 — alerting must not break accounting
        logger.exception(
            "cost_tracker.record_usage.alert_ctx_failed tenant=%s",
            tenant_id,
        )
        return

    # Cross-threshold detection — fire at most one of each per day per
    # tenant. We check both caps; the alert payload reports both
    # numbers so the operator sees the full picture.
    prev_pct_tokens = _pct(previous_tokens, token_cap)
    new_pct_tokens = _pct(new_tokens, token_cap)
    prev_pct_cost = _pct(previous_cost, cost_cap_usd)
    new_pct_cost = _pct(new_cost, cost_cap_usd)

    crossed_80 = (
        prev_pct_tokens < _WARN_THRESHOLD <= new_pct_tokens
        or prev_pct_cost < _WARN_THRESHOLD <= new_pct_cost
    )
    crossed_100 = (
        prev_pct_tokens < _EXHAUST_THRESHOLD <= new_pct_tokens
        or prev_pct_cost < _EXHAUST_THRESHOLD <= new_pct_cost
    )

    if crossed_80 and not _flag_set(_warned_80_key(tenant_id)):
        await sync_to_async(_send_threshold_alert, thread_sensitive=False)(
            tenant_id=tenant_id,
            manager_chat_id=manager_chat_id,
            level=80,
            tokens_used=new_tokens,
            token_cap=token_cap,
            cost_used_usd=new_cost,
            cost_cap_usd=cost_cap_usd,
        )
        _set_flag(_warned_80_key(tenant_id))

    if crossed_100 and not _flag_set(_warned_100_key(tenant_id)):
        await sync_to_async(_send_threshold_alert, thread_sensitive=False)(
            tenant_id=tenant_id,
            manager_chat_id=manager_chat_id,
            level=100,
            tokens_used=new_tokens,
            token_cap=token_cap,
            cost_used_usd=new_cost,
            cost_cap_usd=cost_cap_usd,
        )
        _set_flag(_warned_100_key(tenant_id))


# ---------------------------------------------------------------------------
# Redis key helpers
# ---------------------------------------------------------------------------


def _today_utc() -> str:
    """UTC ISO date string for today. Used as the per-day key suffix."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _tokens_key(tenant_id: str) -> str:
    return f"{_KEY_PREFIX}:{tenant_id}:{_today_utc()}:tokens"


def _cost_key(tenant_id: str) -> str:
    return f"{_KEY_PREFIX}:{tenant_id}:{_today_utc()}:cost_microcents"


def _warned_80_key(tenant_id: str) -> str:
    return f"{_KEY_PREFIX}:{tenant_id}:{_today_utc()}:warned_80"


def _warned_100_key(tenant_id: str) -> str:
    return f"{_KEY_PREFIX}:{tenant_id}:{_today_utc()}:warned_100"


# ---------------------------------------------------------------------------
# Counter primitives — read / increment with TTL
# ---------------------------------------------------------------------------


def _read_tokens(tenant_id: str) -> int:
    value = cache.get(_tokens_key(tenant_id))
    if value is None:
        return 0
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _read_cost_usd(tenant_id: str) -> Decimal:
    value = cache.get(_cost_key(tenant_id))
    if value is None:
        return Decimal("0")
    try:
        microcents = int(value)
    except (TypeError, ValueError):
        return Decimal("0")
    return _microcents_to_usd(microcents)


def _incr_with_ttl(key: str, amount: int) -> int:
    """INCR key by amount; set TTL on first write of the day.

    Returns the post-INCR total so callers (notably ``record_usage``)
    can do threshold-crossing detection from the atomic post-state
    rather than from a non-atomic ``previous + delta`` snapshot —
    closes LLM retro B3.

    Mirrors the Sprint 7 / L7 pattern. ``cache.add`` only sets the TTL
    when the key was absent — on subsequent writes the TTL set by the
    first write of the day is preserved (Redis INCR doesn't reset it),
    so the key naturally expires 24h after the first call of each new
    UTC day per tenant.
    """
    if amount <= 0:
        # Caller skipped — read current and return so downstream
        # threshold-crossing logic sees the actual post-state.
        current = cache.get(key)
        try:
            return int(current) if current is not None else 0
        except (TypeError, ValueError):
            return 0

    incr = getattr(cache, "incr", None)
    if callable(incr):
        try:
            # Redis ``INCRBY`` returns the new total atomically — this is
            # what makes the post-INCR threshold-crossing race-free.
            new_total = incr(key, amount)
            cache.add(key, 0, timeout=_TTL_SECONDS)
            try:
                return int(new_total) if new_total is not None else 0
            except (TypeError, ValueError):
                return 0
        except ValueError:
            # Some backends raise on INCR of missing keys — fall through
            # to read-modify-write below.
            pass

    # Read-modify-write fallback (locmem in tests). Not atomic across
    # workers; locmem is single-process so fine for the test suite.
    current = cache.get(key)
    try:
        current_int = int(current) if current is not None else 0
    except (TypeError, ValueError):
        current_int = 0
    new_int = current_int + amount
    cache.set(key, new_int, timeout=_TTL_SECONDS)
    return new_int


def _flag_set(key: str) -> bool:
    """True iff the warned-* flag exists for the current UTC day."""
    return cache.get(key) is not None


def _set_flag(key: str) -> None:
    """Plant the warned-* flag with the same daily TTL."""
    cache.set(key, 1, timeout=_TTL_SECONDS)


# ---------------------------------------------------------------------------
# USD ↔ microcents
# ---------------------------------------------------------------------------


def _usd_to_microcents(amount: Decimal) -> int:
    """Round-half-up to integer microcents. 1 USD = 10^8 microcents.

    Round-half-up (not banker's rounding) so a flurry of cheap calls
    that each round down to 0 don't escape the cost cap — the half-up
    rule means a single embedding token at $0.00000002 still records
    as 2 microcents.
    """
    if amount <= Decimal(0):
        return 0
    quantised = (amount * _MICROCENTS_PER_USD).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    return int(quantised)


def _microcents_to_usd(microcents: int) -> Decimal:
    if microcents <= 0:
        return Decimal("0")
    return Decimal(microcents) / _MICROCENTS_PER_USD


def _pct(used: int | Decimal, cap: int | Decimal) -> Decimal:
    """Compute the used/cap ratio as Decimal. Cap=0 → 0 (no division)."""
    if cap is None or Decimal(cap) <= 0:
        return Decimal(0)
    return Decimal(used) / Decimal(cap)


# ---------------------------------------------------------------------------
# Sync ORM helpers (wrapped via sync_to_async by the public surface)
# ---------------------------------------------------------------------------


def _read_tenant_caps(tenant_id: str) -> tuple[int, Decimal]:
    """Look up ``daily_token_cap`` + ``daily_cost_cap_usd`` for a tenant.

    Returns sane defaults when the tenant doesn't exist (or fields are
    NULL for some reason) — the surrounding gate still rejects on cap
    exhaustion, but we don't want a row lookup failure to mask the
    actual cap value.
    """
    from apps.tenancy.models import Tenant

    # LLM retro Y3: pre-fix used logger.warning + generous defaults —
    # the typo'd tenant_id silently ran against a fictitious budget,
    # «mystery» costs accumulating against a tenant that no longer
    # existed. The original retro recommended raising; an in-PR
    # reviewer pointed out that ``enforce_caps`` runs INSIDE
    # ``provider.complete()`` (not at router lookup), so raising
    # would 500 the customer until every skill grows an outer
    # LLM-completion try/except (out of scope for this PR).
    #
    # Compromise: keep the soft-fail return-defaults behavior so the
    # customer flow never breaks, but UPGRADE the log level to ERROR
    # (Sentry-visible) and stamp the offending id so ops can triage.
    # Proper typed ``UnknownTenantError(LLMError)`` + skill envelope
    # is filed as the follow-up.
    try:
        row = Tenant.all_objects.values("daily_token_cap", "daily_cost_cap_usd").get(id=tenant_id)
    except Tenant.DoesNotExist:
        logger.error(
            "cost_tracker.tenant_not_found tenant=%s falling_back_to_defaults",
            tenant_id,
        )
        return (1_000_000, Decimal("50.00"))

    token_cap = int(row.get("daily_token_cap") or 0)
    cost_cap = row.get("daily_cost_cap_usd")
    if cost_cap is None:
        cost_cap = Decimal("0")
    elif not isinstance(cost_cap, Decimal):
        cost_cap = Decimal(str(cost_cap))
    return (token_cap, cost_cap)


def _read_tenant_alert_context(tenant_id: str) -> tuple[int, Decimal, str]:
    """Read caps + manager_chat_id in a single ORM hop.

    Separate from :func:`_read_tenant_caps` so the hot enforce-caps
    path doesn't pay for ``manager_chat_id`` it never uses.
    """
    from apps.tenancy.models import Tenant

    # See ``_read_tenant_caps`` for the Y3 rationale: loud log,
    # soft-fail defaults (proper typed exception deferred).
    try:
        row = Tenant.all_objects.values(
            "daily_token_cap", "daily_cost_cap_usd", "manager_chat_id"
        ).get(id=tenant_id)
    except Tenant.DoesNotExist:
        logger.error(
            "cost_tracker.alert_context.tenant_not_found tenant=%s falling_back_to_defaults",
            tenant_id,
        )
        return (1_000_000, Decimal("50.00"), "")

    token_cap = int(row.get("daily_token_cap") or 0)
    cost_cap = row.get("daily_cost_cap_usd") or Decimal("0")
    if not isinstance(cost_cap, Decimal):
        cost_cap = Decimal(str(cost_cap))
    return (token_cap, cost_cap, str(row.get("manager_chat_id") or ""))


def _write_quota_telemetry(
    *,
    tenant_id: str,
    which_cap: Literal["token", "cost"],
    cap_value: int | Decimal,
    current_value: int | Decimal,
) -> None:
    """Sync ORM writes for the quota-exceeded path — audit + event.

    Reuses the canonical ``llm.provider_quota_exceeded`` event slug
    introduced by the Sprint 7 / L7 Anthropic gate. Payload is extended
    with ``tenant_id`` + ``which_cap`` so the analytics fanout can tell
    the per-tenant trip apart from the org-wide Anthropic trip.
    """
    from apps.audit.services import write_audit
    from apps.events.services import emit

    payload: dict[str, Any] = {
        "tenant_id": tenant_id,
        "which_cap": which_cap,
        "cap": str(cap_value),
        "current": str(current_value),
        "date": _today_utc(),
        "source": "cost_tracker",
    }
    write_audit(
        EVENT_PROVIDER_QUOTA_EXCEEDED,
        target="TenantCostTracker",
        payload=payload,
    )
    emit(
        EVENT_PROVIDER_QUOTA_EXCEEDED,
        properties=payload,
    )


def _send_threshold_alert(
    *,
    tenant_id: str,
    manager_chat_id: str,
    level: int,
    tokens_used: int,
    token_cap: int,
    cost_used_usd: Decimal,
    cost_cap_usd: Decimal,
) -> None:
    """Send a 80% or 100% alert to the salon manager via MAX outbound.

    Empty ``manager_chat_id`` → log WARN, skip the send. The cap is
    still enforced and telemetry still written — alerting is a courtesy
    layer, not a precondition.
    """
    if not manager_chat_id:
        logger.warning(
            "cost_tracker.alert_skipped_no_manager_chat_id "
            "tenant=%s level=%d tokens=%d/%d cost=$%s/$%s",
            tenant_id,
            level,
            tokens_used,
            token_cap,
            cost_used_usd,
            cost_cap_usd,
        )
        return

    cost_used_str = _format_usd(cost_used_usd)
    cost_cap_str = _format_usd(cost_cap_usd)
    if level == 80:
        text = _ALERT_80_TEMPLATE.format(
            tokens_used=tokens_used,
            token_cap=token_cap,
            cost_used=cost_used_str,
            cost_cap=cost_cap_str,
        )
    else:
        text = _ALERT_100_TEMPLATE

    try:
        from apps.channels.max.outbound import send_message

        send_message(chat_id=manager_chat_id, text=text)
    except Exception:  # noqa: BLE001 — alerting must never break accounting
        logger.warning(
            "cost_tracker.alert_send_failed tenant=%s level=%d",
            tenant_id,
            level,
            exc_info=True,
        )


def _format_usd(amount: Decimal) -> str:
    """Format a Decimal USD value as ``"12.34"`` (2-decimal, no symbol)."""
    return f"{amount.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP):.2f}"
