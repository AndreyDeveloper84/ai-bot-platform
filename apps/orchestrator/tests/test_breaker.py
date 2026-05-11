"""Circuit breaker state machine tests (DRF-428 / D1).

Time-mocked with monkeypatch of ``apps.orchestrator.llm.breaker._now``
to drive cooldown transitions deterministically.
"""

from __future__ import annotations

import pytest

from apps.orchestrator.llm import breaker as br
from apps.orchestrator.llm.breaker import (
    BreakerOpenError,
    State,
    get_state,
    reset_breaker,
    with_circuit_breaker,
)

pytestmark = pytest.mark.asyncio


@pytest.fixture(autouse=True)
def _clear_breakers():
    """Reset the breaker registry between tests."""

    reset_breaker("test")
    yield
    reset_breaker("test")


class _Clock:
    """Manual clock for breaker time-mocking. Replaces ``breaker._now``."""

    def __init__(self) -> None:
        self.t = 0.0

    def __call__(self) -> float:
        return self.t

    def advance(self, seconds: float) -> None:
        self.t += seconds


@pytest.fixture
def clock(monkeypatch):
    c = _Clock()
    monkeypatch.setattr(br, "_now", c)
    return c


async def _success() -> str:
    return "ok"


async def _failure() -> str:
    raise RuntimeError("simulated outage")


# ---------------------------------------------------------------------------
# Closed state
# ---------------------------------------------------------------------------


class TestClosed:
    async def test_first_call_succeeds(self):
        result = await with_circuit_breaker("test", _success)
        assert result == "ok"
        assert get_state("test") == State.CLOSED

    async def test_failure_propagates(self):
        with pytest.raises(RuntimeError, match="simulated outage"):
            await with_circuit_breaker("test", _failure)
        # Below threshold — still closed.
        assert get_state("test") == State.CLOSED

    async def test_threshold_failures_trip_to_open(self, clock):
        for _ in range(5):
            with pytest.raises(RuntimeError):
                await with_circuit_breaker(
                    "test",
                    _failure,
                    threshold=5,
                    window_seconds=60,
                    cooldown_seconds=30,
                )
        assert get_state("test") == State.OPEN

    async def test_failures_outside_window_dont_count(self, clock):
        # Two failures, then 61s pass, then three failures — only 3 inside
        # window → still closed.
        for _ in range(2):
            with pytest.raises(RuntimeError):
                await with_circuit_breaker(
                    "test",
                    _failure,
                    threshold=5,
                    window_seconds=60,
                    cooldown_seconds=30,
                )
        clock.advance(61)
        for _ in range(3):
            with pytest.raises(RuntimeError):
                await with_circuit_breaker(
                    "test",
                    _failure,
                    threshold=5,
                    window_seconds=60,
                    cooldown_seconds=30,
                )
        assert get_state("test") == State.CLOSED


# ---------------------------------------------------------------------------
# Open state
# ---------------------------------------------------------------------------


class TestOpen:
    async def _trip_to_open(self, clock):
        for _ in range(5):
            with pytest.raises(RuntimeError):
                await with_circuit_breaker(
                    "test",
                    _failure,
                    threshold=5,
                    window_seconds=60,
                    cooldown_seconds=30,
                )
        assert get_state("test") == State.OPEN

    async def test_short_circuits_without_calling_fn(self, clock):
        await self._trip_to_open(clock)

        called: list[bool] = []

        async def must_not_call() -> str:
            called.append(True)
            return "never"

        with pytest.raises(BreakerOpenError):
            await with_circuit_breaker(
                "test",
                must_not_call,
                threshold=5,
                window_seconds=60,
                cooldown_seconds=30,
            )
        assert called == []  # underlying fn was NOT invoked

    async def test_open_persists_within_cooldown(self, clock):
        await self._trip_to_open(clock)
        clock.advance(15)  # cooldown is 30s
        assert get_state("test") == State.OPEN

    async def test_promotes_to_half_open_after_cooldown(self, clock):
        await self._trip_to_open(clock)
        clock.advance(31)
        assert get_state("test") == State.HALF_OPEN


# ---------------------------------------------------------------------------
# Half-open state
# ---------------------------------------------------------------------------


class TestHalfOpen:
    async def _enter_half_open(self, clock):
        for _ in range(5):
            with pytest.raises(RuntimeError):
                await with_circuit_breaker(
                    "test",
                    _failure,
                    threshold=5,
                    window_seconds=60,
                    cooldown_seconds=30,
                )
        clock.advance(31)
        assert get_state("test") == State.HALF_OPEN

    async def test_success_closes_breaker(self, clock):
        await self._enter_half_open(clock)
        result = await with_circuit_breaker(
            "test",
            _success,
            threshold=5,
            window_seconds=60,
            cooldown_seconds=30,
        )
        assert result == "ok"
        assert get_state("test") == State.CLOSED

    async def test_failure_reopens_immediately(self, clock):
        await self._enter_half_open(clock)
        with pytest.raises(RuntimeError):
            await with_circuit_breaker(
                "test",
                _failure,
                threshold=5,
                window_seconds=60,
                cooldown_seconds=30,
            )
        # One failure in half-open → immediately back to open (not waiting
        # for another N failures).
        assert get_state("test") == State.OPEN


# ---------------------------------------------------------------------------
# Multiple breakers — isolation
# ---------------------------------------------------------------------------


class TestHalfOpenConcurrency:
    """Regression test for fix #2 (post-review): half-open must admit
    EXACTLY ONE probe. Concurrent callers that hit while a probe is in
    flight get BreakerOpenError, not a second upstream call. Without
    this, a recovering OpenAI endpoint gets hammered by every queued
    request the moment cooldown elapses.
    """

    async def _trip_to_half_open(self, clock):
        for _ in range(5):
            with pytest.raises(RuntimeError):
                await with_circuit_breaker(
                    "test",
                    _failure,
                    threshold=5,
                    window_seconds=60,
                    cooldown_seconds=30,
                )
        clock.advance(31)
        assert get_state("test") == State.HALF_OPEN

    async def test_concurrent_probes_only_one_runs(self, clock):
        await self._trip_to_half_open(clock)

        call_count = 0
        # The probe blocks on this event so we can launch a second call
        # before the first finishes.
        proceed = __import__("asyncio").Event()

        async def slow_success() -> str:
            nonlocal call_count
            call_count += 1
            await proceed.wait()
            return "ok"

        import asyncio

        task1 = asyncio.create_task(
            with_circuit_breaker(
                "test",
                slow_success,
                threshold=5,
                window_seconds=60,
                cooldown_seconds=30,
            )
        )
        # Yield to let task1 reserve the probe slot.
        await asyncio.sleep(0)

        # Now a concurrent caller must see "probe in flight" and bail.
        with pytest.raises(BreakerOpenError, match="probe already in flight"):
            await with_circuit_breaker(
                "test",
                slow_success,
                threshold=5,
                window_seconds=60,
                cooldown_seconds=30,
            )
        assert call_count == 1  # only the first probe started the upstream call

        # Let task1 finish — should close the breaker and free the slot.
        proceed.set()
        await task1
        assert get_state("test") == State.CLOSED

    async def test_probe_failure_releases_slot_and_reopens(self, clock):
        await self._trip_to_half_open(clock)

        with pytest.raises(RuntimeError):
            await with_circuit_breaker(
                "test",
                _failure,
                threshold=5,
                window_seconds=60,
                cooldown_seconds=30,
            )
        # Failure in half-open → back to OPEN, probe slot released.
        assert get_state("test") == State.OPEN


class TestIsolation:
    async def test_different_names_are_independent(self, clock):
        reset_breaker("alpha")
        reset_breaker("beta")
        for _ in range(5):
            with pytest.raises(RuntimeError):
                await with_circuit_breaker(
                    "alpha",
                    _failure,
                    threshold=5,
                    window_seconds=60,
                    cooldown_seconds=30,
                )
        assert get_state("alpha") == State.OPEN
        # beta is untouched.
        result = await with_circuit_breaker("beta", _success)
        assert result == "ok"
        assert get_state("beta") == State.CLOSED
        reset_breaker("alpha")
        reset_breaker("beta")
