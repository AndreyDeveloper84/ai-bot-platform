"""Exponential-backoff retry for transient LLM provider errors.

(Phase 1 / PI7 / DRF-858.)

Both ``openai`` and ``anthropic`` SDKs are wired without any retry
behaviour in our provider modules — a single transient 429 or 5xx is
propagated to the orchestrator and the user sees the static
"Извините, не могу ответить" fallback. Observation: ~95% of such
failures retry-successfully within a couple of seconds. This module
adds an in-provider retry layer so the user never sees the transient.

### Where this sits

The retry layer lives **inside** each provider's ``complete`` /
``embedding`` method, **below** the PI9 ``enforce_caps`` /
``record_usage`` glue. Concretely:

  1. ``enforce_caps`` runs (pre-call gate, may raise
     ``TenantQuotaExceeded`` — no retry).
  2. ``run_with_retry(_do_sdk_call)`` runs — the SDK call may retry
     1..N times inside this. Only the FINAL outcome (success or
     ``RetriableLLMError``) is observable to the caller.
  3. On success → ``record_usage`` once with the actual tokens used —
     retries do NOT double-charge the tenant counter.
  4. On exhaustion → ``RetriableLLMError`` propagates to the
     orchestrator, which writes an audit row, emits a Telegram alert
     to the manager (deduped per tenant per hour), and serves a
     static Russian fallback to the user.

### Why a custom layer instead of the SDK's built-in retries

Both SDKs expose retry knobs (``max_retries`` ctor kwarg) but they're
either disabled in our config or tuned for a different threat model
(per-request timeout, no jitter, no audit hook). We want:

* Audit row per failed attempt (operator visibility into transient
  storms before they tip into customer impact).
* Telemetry-driven backoff math (deterministic in tests).
* A custom predicate set — we deliberately retry 5xx + 429 +
  connection/timeout, and we deliberately don't retry 4xx (those are
  business errors; retrying wastes money + tokens).

### Predicate strategy

We classify by SDK exception **class name** (string comparison) so
that minor-version refactors inside ``openai`` / ``anthropic`` don't
silently widen or narrow the retriable set. As a fallback, we also
honour HTTP status-code inspection (``getattr(exc, "status_code",
None) in (429, 500, 502, 503, 504)``) for cases where the SDK has
unified errors into a single class but exposed status as an
attribute.

### Determinism in tests

``run_with_retry`` accepts ``_now`` and ``_sleep`` injection so the
test suite never burns wall-clock time. The default policy uses
``asyncio.sleep`` + ``time.monotonic`` — production parity. Tests
inject a captured-arg fake to assert the sleep durations match the
documented exponential progression.

### What this module does NOT do

* No circuit breaker. The per-model L7 cap covers the org-wide break;
  the per-tenant PI9 cap covers tenant misbehaviour. Retry handles
  the third axis (transient errors). Combining all three on one call
  site would be three layers of policy in one place — out of scope.
* No tenant-specific retry policy. Same numbers for every tenant.
  Customisation is a future ticket.
* No orchestrator-level retry. Retry is transparent to the orchestrator.
"""

from __future__ import annotations

import asyncio
import logging
import random
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TypeVar

logger = logging.getLogger(__name__)


T = TypeVar("T")


# ---------------------------------------------------------------------------
# Public API — RetryPolicy + RetriableLLMError
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RetryPolicy:
    """Tunable knobs for :func:`run_with_retry`.

    Defaults match the documented Phase 1 acceptance criteria:
    3 attempts, 1s base delay, 30s cap, ±25% jitter. Override in
    settings via ``LLM_RETRY_MAX_ATTEMPTS`` / ``LLM_RETRY_BASE_DELAY_S``
    / ``LLM_RETRY_MAX_DELAY_S`` env vars (config/settings/base.py).

    Attributes:
      max_attempts: total attempts including the first. ``3`` means
        the call is made up to 3 times — initial + 2 retries.
      base_delay_s: starting backoff. Doubles with each attempt
        (``base``, ``base * 2``, ``base * 4``, …).
      max_delay_s: hard cap on a single backoff window. With
        ``base=1`` and ``max=30``, the sleep sequence is
        ``[1s, 2s, 4s, 8s, 16s, 30s, 30s, …]`` (clipped at attempt 6).
      jitter: random scaling factor applied to each computed delay.
        ``0.25`` = each sleep is ``computed * (1 ± 0.25)``. Prevents
        thundering-herd retries from N concurrent callers all backing
        off in lockstep. Set to ``0`` for deterministic tests.
    """

    max_attempts: int = 3
    base_delay_s: float = 1.0
    max_delay_s: float = 30.0
    jitter: float = 0.25


DEFAULT_POLICY = RetryPolicy()


class RetriableLLMError(Exception):
    """Raised when :func:`run_with_retry` exhausted all attempts.

    Caller (the orchestrator) catches this at the outer boundary of
    the pipeline turn and serves a static Russian fallback to the
    user + writes an audit row + emits a Telegram alert to the salon
    manager (deduped per tenant per hour).

    Attributes:
      attempts: how many times the underlying callable was tried.
        Equals ``policy.max_attempts`` on exhaustion (always — we
        never raise this before exhaustion).
      last_error: the final retriable exception that caused give-up.
        Preserved as ``__cause__`` via standard exception chaining
        too — both reads work.
    """

    def __init__(self, *, attempts: int, last_error: BaseException) -> None:
        self.attempts = attempts
        self.last_error = last_error
        super().__init__(
            f"RetriableLLMError(attempts={attempts}, "
            f"last_error={type(last_error).__name__}: {last_error})"
        )


# ---------------------------------------------------------------------------
# Predicates — what counts as retriable?
# ---------------------------------------------------------------------------

# Status codes considered transient. 429 = rate-limited (vendor side);
# 5xx = vendor server fault. NEVER retry 4xx other than 429 — those
# are business errors (auth, bad request, not found, permission) and
# retrying wastes money + tokens.
_RETRIABLE_STATUS_CODES: frozenset[int] = frozenset({429, 500, 502, 503, 504})


# Class names we treat as retriable for the OpenAI SDK. Stable across
# minor versions (openai>=1.0 settled on these names). Anything not
# in this set + not status-code-matching falls through to "not
# retriable" and propagates immediately.
_OPENAI_RETRIABLE_CLASS_NAMES: frozenset[str] = frozenset(
    {
        "RateLimitError",
        "APIConnectionError",
        "APITimeoutError",
        "InternalServerError",
    }
)


# Symmetric set for Anthropic — the SDK exposes the same class names
# as OpenAI for the equivalent failure modes (deliberate alignment in
# both SDK designs). Verified against anthropic>=0.40 module surface.
_ANTHROPIC_RETRIABLE_CLASS_NAMES: frozenset[str] = frozenset(
    {
        "RateLimitError",
        "APIConnectionError",
        "APITimeoutError",
        "InternalServerError",
    }
)


def _is_retriable_by_class_or_status(
    exc: BaseException,
    *,
    retriable_class_names: frozenset[str],
) -> bool:
    """Two-layer retriability check.

    Layer 1: walk the exception's MRO and match class names against
    ``retriable_class_names``. This catches every variant of the SDK's
    retriable error classes regardless of import path
    (``openai.RateLimitError`` vs ``openai._exceptions.RateLimitError``).

    Layer 2: fallback to HTTP-status-code inspection. Some SDK
    versions unify everything into ``APIStatusError`` and expose the
    code as ``exc.status_code``. We treat the documented transient
    set (429, 500, 502, 503, 504) as retriable; everything else
    (400, 401, 403, 404, 422, …) propagates immediately.

    Non-SDK exceptions (``ValueError``, ``RuntimeError``, …) never
    match either layer and propagate.
    """
    # Layer 1: class-name MRO walk.
    for klass in type(exc).__mro__:
        if klass.__name__ in retriable_class_names:
            return True

    # Layer 2: status-code fallback (for unified APIStatusError shape).
    status = getattr(exc, "status_code", None)
    if status is None:
        # Some SDKs surface status via .response.status_code instead.
        response = getattr(exc, "response", None)
        if response is not None:
            status = getattr(response, "status_code", None)
    try:
        if status is not None and int(status) in _RETRIABLE_STATUS_CODES:
            return True
    except (TypeError, ValueError):
        pass

    return False


def is_retriable_openai(exc: BaseException) -> bool:
    """True for OpenAI 429 / 5xx / connection / timeout — those are
    transient and worth retrying.

    Retriable classes (must be in the exception's MRO):
      * ``RateLimitError`` (HTTP 429)
      * ``APIConnectionError`` (network failure)
      * ``APITimeoutError`` (client-side timeout)
      * ``InternalServerError`` (HTTP 5xx)

    Non-retriable (we fall through to False — caller re-raises):
      * ``AuthenticationError`` (401)
      * ``BadRequestError`` (400)
      * ``PermissionDeniedError`` (403)
      * ``NotFoundError`` (404)
      * ``UnprocessableEntityError`` (422)
      * any non-SDK exception (``ValueError``, …)

    Status-code fallback applies for SDK versions that flatten
    errors into ``APIStatusError`` — we treat
    ``getattr(exc, "status_code", None) in (429, 500, 502, 503, 504)``
    as retriable too.
    """
    return _is_retriable_by_class_or_status(
        exc, retriable_class_names=_OPENAI_RETRIABLE_CLASS_NAMES
    )


def is_retriable_anthropic(exc: BaseException) -> bool:
    """True for Anthropic 429 / 5xx / connection / timeout. Symmetric
    with :func:`is_retriable_openai` — both SDKs use the same class
    names for the equivalent failure modes.

    See :func:`is_retriable_openai` for the full retriable / non-
    retriable lists.
    """
    return _is_retriable_by_class_or_status(
        exc, retriable_class_names=_ANTHROPIC_RETRIABLE_CLASS_NAMES
    )


# ---------------------------------------------------------------------------
# Core retry loop
# ---------------------------------------------------------------------------


async def run_with_retry(
    fn: Callable[[], Awaitable[T]],
    *,
    policy: RetryPolicy = DEFAULT_POLICY,
    is_retriable: Callable[[BaseException], bool],
    on_attempt_failed: Callable[[int, BaseException], Awaitable[None]] | None = None,
    _now: Callable[[], float] | None = None,
    _sleep: Callable[[float], Awaitable[None]] | None = None,
    _random: Callable[[], float] | None = None,
) -> T:
    """Run ``fn()`` with exponential backoff on retriable errors.

    Args:
      fn: zero-arg async callable. We invoke it ``[1, policy.max_attempts]``
        times until it returns successfully or all attempts are exhausted.
      policy: backoff knobs (see :class:`RetryPolicy`).
      is_retriable: predicate run on each caught exception. Return True
        to keep retrying; False to re-raise immediately. Decoupling
        the predicate from the policy lets the same retry loop serve
        both providers.
      on_attempt_failed: optional async hook fired after each failed
        attempt (whether we retry or give up). Receives
        ``(attempt_number, exception)``; attempt is 1-indexed.
        Used by providers to write an audit row + telemetry.
      _now / _sleep / _random: test-injection seams. Default to
        ``time.monotonic`` / ``asyncio.sleep`` / ``random.random``.
        Passing fakes lets tests run with zero wall-clock without
        monkeypatching modules.

    Returns:
      Whatever ``fn()`` returns on a successful attempt.

    Raises:
      RetriableLLMError: when all attempts are exhausted. ``attempts``
        equals ``policy.max_attempts``; ``last_error`` is the final
        retriable exception.
      Any non-retriable exception: re-raised AS-IS the moment
        ``is_retriable(exc)`` returns False. ``on_attempt_failed`` is
        NOT invoked for non-retriable errors — those are business
        errors, not transient ones, and the audit row would be
        misleading.
    """
    sleep = _sleep if _sleep is not None else asyncio.sleep
    rand = _random if _random is not None else random.random
    # _now is reserved for future telemetry (per-attempt duration).
    # Not currently used inside the loop but accepted as a parameter
    # so test signatures stay forward-compatible.
    _ = _now or time.monotonic

    # LLM retro Y4: catch ``Exception``, not ``BaseException``. Bare
    # ``except BaseException`` swallowed ``KeyboardInterrupt``,
    # ``SystemExit``, and ``asyncio.CancelledError`` — a worker shut
    # down mid-retry would have its cancel signal absorbed and the
    # retry loop would keep firing until the predicate returned
    # non-retriable. ``CancelledError`` is explicitly re-raised below
    # to defend against a caller's predicate accidentally treating it
    # as retriable.
    last_exc: BaseException | None = None
    for attempt in range(1, policy.max_attempts + 1):
        try:
            return await fn()
        except asyncio.CancelledError:
            # Worker shutdown / task cancellation — propagate IMMEDIATELY
            # so the runtime can unwind, regardless of any predicate.
            raise
        except Exception as exc:  # noqa: BLE001 — caller's predicate decides
            if not is_retriable(exc):
                # Non-retriable: propagate immediately without firing
                # on_attempt_failed (it's reserved for retriable
                # transients — see docstring).
                raise

            last_exc = exc
            if on_attempt_failed is not None:
                try:
                    await on_attempt_failed(attempt, exc)
                except Exception:  # noqa: BLE001
                    # Telemetry must never break retry. Log + continue.
                    logger.exception("retry.on_attempt_failed_hook_failed attempt=%d", attempt)

            if attempt >= policy.max_attempts:
                break

            delay = _compute_backoff_delay(
                attempt=attempt,
                policy=policy,
                rand=rand,
            )
            logger.info(
                "retry.sleeping attempt=%d/%d delay=%.3fs exc=%s",
                attempt,
                policy.max_attempts,
                delay,
                type(exc).__name__,
            )
            await sleep(delay)

    # All attempts exhausted. ``last_exc`` is guaranteed set because
    # we only reach this point after at least one retriable failure
    # (a non-retriable would have re-raised inside the loop).
    assert last_exc is not None  # noqa: S101 — invariant, see above
    raise RetriableLLMError(attempts=policy.max_attempts, last_error=last_exc) from last_exc


# ---------------------------------------------------------------------------
# Backoff math (separated for direct unit testing)
# ---------------------------------------------------------------------------


def _compute_backoff_delay(
    *,
    attempt: int,
    policy: RetryPolicy,
    rand: Callable[[], float],
) -> float:
    """Compute one sleep delay between retry attempts.

    Formula:
      ``base * 2 ** (attempt - 1)``, capped at ``max_delay_s``, then
      multiplied by a uniform jitter factor in ``[1 - jitter, 1 + jitter]``.

    Examples (base=1.0, max=30.0, jitter=0.25):
      * attempt=1 → base*1 = 1s, jittered to [0.75, 1.25]
      * attempt=2 → base*2 = 2s, jittered to [1.5, 2.5]
      * attempt=3 → base*4 = 4s, jittered to [3.0, 5.0]
      * attempt=6 → base*32 = 32s → CAPPED at 30s, jittered to [22.5, 37.5]

    Note that jitter is applied AFTER the cap, so a fully jittered
    delay can briefly exceed ``max_delay_s`` by up to ``jitter * max``.
    Acceptable — the cap is a centerline, not a hard ceiling, and the
    high side of jitter actively helps anti-thundering-herd by spreading
    retries.
    """
    # attempt is 1-indexed; first retry sleep uses base * 2^0 = base.
    raw = policy.base_delay_s * (2 ** (attempt - 1))
    capped = min(raw, policy.max_delay_s)
    if policy.jitter <= 0:
        return max(0.0, capped)
    # rand() yields [0.0, 1.0) — map to symmetric range around 1.0.
    multiplier = 1.0 + ((rand() - 0.5) * 2.0 * policy.jitter)
    return max(0.0, capped * multiplier)


# ---------------------------------------------------------------------------
# Settings helper
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Audit hook (shared by both providers)
# ---------------------------------------------------------------------------


# Audit action slug — emitted once per failed-but-retriable attempt.
# Distinct from ``llm.retry_exhausted`` (orchestrator-level, terminal).
AUDIT_RETRY_ATTEMPT_FAILED = "llm.retry_attempt_failed"

# Audit action slug — emitted by the orchestrator when all retry
# attempts are exhausted. Lives alongside the static fallback served
# to the user.
AUDIT_RETRY_EXHAUSTED = "llm.retry_exhausted"

# Event slug — emitted alongside the audit row by the orchestrator
# so analytics dashboards can chart retry-storm rate per tenant.
EVENT_RETRY_EXHAUSTED = "llm.retry_exhausted"


def _extract_status_code(exc: BaseException) -> int | None:
    """Pull the HTTP status off an SDK exception, if exposed.

    Tries ``exc.status_code`` (modern SDK style) then
    ``exc.response.status_code`` (older / wrapped style). Returns
    None when neither is set — that's fine; the audit row just won't
    carry a status_code field.
    """
    status = getattr(exc, "status_code", None)
    if status is None:
        response = getattr(exc, "response", None)
        if response is not None:
            status = getattr(response, "status_code", None)
    try:
        return int(status) if status is not None else None
    except (TypeError, ValueError):
        return None


async def write_retry_attempt_audit(
    *,
    provider: str,
    model: str,
    op: str,
    attempt: int,
    exc: BaseException,
) -> None:
    """Write one audit row per failed-but-retriable attempt.

    Async wrapper around the sync ``write_audit`` ORM call. Lives in
    this module so both providers share an identical payload shape —
    keeping audit-side analytics queries provider-agnostic.

    Failure here is swallowed (telemetry must never break the call
    path) — :func:`run_with_retry` itself catches exceptions from
    ``on_attempt_failed`` too, so this is a belt + braces guard.
    """
    from asgiref.sync import sync_to_async

    payload = {
        "provider": provider,
        "model": model,
        "op": op,
        "attempt": attempt,
        "error_class": type(exc).__name__,
        "status_code": _extract_status_code(exc),
        "retriable": True,
        "message": str(exc)[:200],
    }
    try:
        await sync_to_async(_sync_write_audit, thread_sensitive=False)(payload)
    except Exception:  # noqa: BLE001
        logger.exception(
            "retry.audit_write_failed provider=%s op=%s attempt=%d",
            provider,
            op,
            attempt,
        )


def _sync_write_audit(payload: dict) -> None:
    """Sync helper for :func:`write_retry_attempt_audit`."""
    from apps.audit.services import write_audit

    write_audit(
        AUDIT_RETRY_ATTEMPT_FAILED,
        target=f"{payload['provider']}.{payload['op']}",
        payload=payload,
    )


def policy_from_settings() -> RetryPolicy:
    """Build a :class:`RetryPolicy` from Django settings.

    Reads:
      * ``LLM_RETRY_MAX_ATTEMPTS`` (default 3)
      * ``LLM_RETRY_BASE_DELAY_S`` (default 1.0)
      * ``LLM_RETRY_MAX_DELAY_S`` (default 30.0)

    Single set of settings shared across both providers — same
    behaviour uniformly. Per-provider tuning, if it ever becomes
    necessary, will split into ``OPENAI_RETRY_*`` / ``ANTHROPIC_RETRY_*``
    later; until then one set keeps operations simple.
    """
    from django.conf import settings

    return RetryPolicy(
        max_attempts=int(getattr(settings, "LLM_RETRY_MAX_ATTEMPTS", 3)),
        base_delay_s=float(getattr(settings, "LLM_RETRY_BASE_DELAY_S", 1.0)),
        max_delay_s=float(getattr(settings, "LLM_RETRY_MAX_DELAY_S", 30.0)),
    )
