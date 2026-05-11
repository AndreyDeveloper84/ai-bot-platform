"""DIY circuit breaker (DRF-428 / D1).

Implements CR-3 from PHASE0_DESIGN.md: closes the 3-month single-
provider window before multi-LLM routing lands in Sprint 7. ADR-0005
calls for an `LLMProvider` Protocol; this breaker is the resilience
primitive that wraps every provider implementation.

State machine:

    ┌─────────┐  failure_count >= threshold     ┌──────┐
    │ closed  │ ──────────────────────────────▶ │ open │
    │ (normal)│                                 │      │
    └─────────┘                                 └───┬──┘
        ▲                                           │  cooldown elapsed
        │  success in half-open                     ▼
    ┌──────┴─────┐    failure in half-open    ┌───────────┐
    │   closed   │ ◀─────────────────────────  │ half-open │
    │ (reset)    │    re-opens immediately     │   (probe) │
    └────────────┘                             └───────────┘

Default policy (per design doc):
  threshold = 5 failures within window_seconds = 60
  cooldown_seconds = 30 after open before allowing one probe

Why DIY (per review revision 3C):
  - Only one breaker needed in Sprint 1 (OpenAI).
  - <100 LoC, easy to test with freezegun.
  - Firm API `with_circuit_breaker(name, fn, ...)` is the contract we
    extract into ayla-ai-core when provider #2 arrives.
"""

from __future__ import annotations

import logging
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from enum import Enum
from typing import Awaitable, Callable, TypeVar

logger = logging.getLogger(__name__)

T = TypeVar("T")


class BreakerOpenError(Exception):
    """Raised by ``with_circuit_breaker`` when the breaker is currently open."""


class State(str, Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


@dataclass
class _BreakerCore:
    """Per-name breaker state. Lives in module-global ``_breakers`` registry."""

    name: str
    threshold: int = 5
    window_seconds: float = 60.0
    cooldown_seconds: float = 30.0
    state: State = State.CLOSED
    failures: deque[float] = field(default_factory=deque)
    opened_at: float | None = None
    # probe_in_flight = True between the moment HALF_OPEN admits one
    # call and the moment that call resolves (success → CLOSED, fail →
    # OPEN). Concurrent callers see probe_in_flight and short-circuit
    # with BreakerOpenError, so the recovering upstream sees at most
    # one probe at a time.
    probe_in_flight: bool = False
    lock: threading.Lock = field(default_factory=threading.Lock)


_breakers: dict[str, _BreakerCore] = {}
_registry_lock = threading.Lock()


def _get_or_create(
    name: str,
    threshold: int,
    window_seconds: float,
    cooldown_seconds: float,
) -> _BreakerCore:
    with _registry_lock:
        existing = _breakers.get(name)
        if existing is None:
            existing = _BreakerCore(
                name=name,
                threshold=threshold,
                window_seconds=window_seconds,
                cooldown_seconds=cooldown_seconds,
            )
            _breakers[name] = existing
        return existing


def _now() -> float:
    """Monotonic-like wall clock for the breaker. Patchable by freezegun."""

    return time.time()


def _prune_old_failures(core: _BreakerCore, now: float) -> None:
    cutoff = now - core.window_seconds
    while core.failures and core.failures[0] < cutoff:
        core.failures.popleft()


def _record_success(core: _BreakerCore) -> None:
    """A successful call — reset failure count and close half-open if needed."""

    with core.lock:
        if core.state == State.HALF_OPEN:
            _transition(core, State.CLOSED)
        core.failures.clear()


def _record_failure(core: _BreakerCore) -> None:
    """A failed call — increment count; may trip from closed → open."""

    now = _now()
    with core.lock:
        _prune_old_failures(core, now)
        core.failures.append(now)
        if core.state == State.HALF_OPEN:
            # Half-open probe failed → re-open immediately.
            _transition(core, State.OPEN, now=now)
        elif core.state == State.CLOSED and len(core.failures) >= core.threshold:
            _transition(core, State.OPEN, now=now)


def _transition(core: _BreakerCore, new_state: State, *, now: float | None = None) -> None:
    """Move to ``new_state``. Caller holds core.lock."""

    old = core.state
    core.state = new_state
    if new_state == State.OPEN:
        core.opened_at = now if now is not None else _now()
    elif new_state == State.CLOSED:
        core.opened_at = None
        core.failures.clear()
    logger.warning(
        "llm.breaker.transition name=%s from=%s to=%s failures=%d",
        core.name,
        old.value,
        new_state.value,
        len(core.failures),
    )

    # E2 — fire-and-forget Telegram alert on every state transition.
    # The alert helper is best-effort (never raises) and short-circuits
    # when `ADMIN_MAX_CHAT_ID` is unset (dev/CI default). We import
    # lazily to keep the breaker module free of Django settings
    # coupling at import time.
    try:
        from apps.orchestrator.llm.telegram_alert import send_breaker_alert

        send_breaker_alert(
            provider=core.name,
            transition=f"{old.value} → {new_state.value}",
            details={"failures": len(core.failures)},
        )
    except Exception:  # noqa: BLE001 — alerting must NEVER break breaker
        logger.exception("llm.breaker.alert_failed name=%s", core.name)


def _check_state(core: _BreakerCore) -> State:
    """Look at the current state, auto-promoting open → half-open after cooldown."""

    with core.lock:
        if core.state == State.OPEN:
            assert core.opened_at is not None
            if _now() - core.opened_at >= core.cooldown_seconds:
                _transition(core, State.HALF_OPEN)
        return core.state


async def with_circuit_breaker(
    name: str,
    fn: Callable[..., Awaitable[T]],
    *args,
    threshold: int = 5,
    window_seconds: float = 60.0,
    cooldown_seconds: float = 30.0,
    **kwargs,
) -> T:
    """Run ``fn(*args, **kwargs)`` through the named circuit breaker.

    Args:
      name: Unique identifier for the breaker (e.g. "openai.complete").
            Each name has its own state; first-time names get the
            default policy unless overridden by threshold/window/cooldown
            args (which are stored on first creation only).
      fn: Async callable to invoke.
      threshold/window_seconds/cooldown_seconds: Policy on first creation.

    Behaviour:
      - In CLOSED state: run ``fn``. On exception, record failure and
        re-raise. If threshold reached → trip to OPEN.
      - In OPEN state (cooldown not elapsed): raise BreakerOpenError
        immediately. ``fn`` is NOT called.
      - In OPEN state (cooldown elapsed): auto-promote to HALF_OPEN
        and let exactly one probe call through. Success → close.
        Failure → re-open.

    Raises:
      BreakerOpenError if state is OPEN.
      The underlying exception from ``fn`` if state is CLOSED/HALF_OPEN
      and the call fails.
    """

    core = _get_or_create(name, threshold, window_seconds, cooldown_seconds)

    # Atomic transition + admission check. We hold the lock long enough
    # to (a) auto-promote OPEN → HALF_OPEN if cooldown elapsed, and
    # (b) reserve the single probe slot in HALF_OPEN so concurrent
    # callers are short-circuited. Releasing before the upstream call
    # would let N callers all grab the same probe slot.
    with core.lock:
        if core.state == State.OPEN:
            assert core.opened_at is not None
            if _now() - core.opened_at >= core.cooldown_seconds:
                _transition(core, State.HALF_OPEN)
        if core.state == State.OPEN:
            logger.info("llm.breaker.short_circuit name=%s state=open", name)
            raise BreakerOpenError(
                f"Circuit breaker {name!r} is open; refusing call. "
                f"Cooldown {cooldown_seconds}s after open at {core.opened_at}.",
            )
        if core.state == State.HALF_OPEN:
            if core.probe_in_flight:
                # Another caller already holds the probe slot.
                logger.info("llm.breaker.short_circuit name=%s state=half_open_busy", name)
                raise BreakerOpenError(
                    f"Circuit breaker {name!r} is in half-open with a probe "
                    "already in flight; refusing concurrent calls.",
                )
            core.probe_in_flight = True
            is_probe = True
        else:
            is_probe = False

    try:
        result = await fn(*args, **kwargs)
    except Exception:
        _record_failure(core)
        if is_probe:
            # Failure released the slot when _record_failure transitioned
            # back to OPEN; explicit clear is belt-and-braces.
            with core.lock:
                core.probe_in_flight = False
        raise

    _record_success(core)
    if is_probe:
        with core.lock:
            core.probe_in_flight = False
    return result


def reset_breaker(name: str) -> None:
    """Hard reset — clear failures and force CLOSED. Test-only helper."""

    with _registry_lock:
        _breakers.pop(name, None)


def get_state(name: str) -> State | None:
    """Return the current state of a named breaker, or None if unknown."""

    core = _breakers.get(name)
    if core is None:
        return None
    return _check_state(core)
