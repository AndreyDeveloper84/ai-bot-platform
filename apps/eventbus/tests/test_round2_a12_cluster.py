"""Round-2 adversarial-pass regression tests — A12 cluster (AS5+AS6+AS11).

Each test in this module IS the bypass attempt per memory
``feedback_h3_waiver_pattern`` N=10 discipline. Tests are
``@pytest.mark.real_timeout`` so they exercise the actual
ThreadPoolExecutor + semaphore wrapper (not the SQLite-bypass
autouse from conftest.py).

AS5 — DB-pool bomb: bounded semaphore caps concurrent in-flight
to prevent connection exhaustion brownout.

AS6 — thread bomb: bounded semaphore rejects fast (503) instead of
queueing in the executor's unbounded SimpleQueue.

AS11 — coverage: this module IS the missing end-to-end test of the
real wrapper.
"""

from __future__ import annotations

import datetime as dt
import threading
import time
from unittest.mock import patch

import pytest

from apps.eventbus.ingest_dispatcher import DispatchOutcome, DispatchResult
from apps.eventbus.ingest_envelope import IngestEnvelope
from apps.eventbus.ingest_timeout import (
    _MAX_INFLIGHT,
    dispatch_with_timeout,
    inflight_count,
)


pytestmark = pytest.mark.real_timeout


def _wait_for_inflight_drain(timeout_s: float = 3.0) -> None:
    """Wait for the in-flight semaphore to drain to 0.

    Tests that run concurrent / timed-out handlers leave the orphan-
    tail draining in background threads. Without this, a downstream
    test that asserts ``inflight_count() == 0`` at setup fails on
    the leaked slots.
    """
    deadline = time.monotonic() + timeout_s
    while inflight_count() != 0 and time.monotonic() < deadline:
        time.sleep(0.02)


@pytest.fixture(autouse=True)
def _drain_inflight_between_tests():
    """Ensure clean inflight state at test start + give orphans time
    to release at teardown."""
    _wait_for_inflight_drain()
    yield
    _wait_for_inflight_drain()


def _envelope(event_id: str = "01J9HXKM8Z2T4V6R8Q1P3D5F7E") -> IngestEnvelope:
    return IngestEnvelope(
        event_id=event_id,
        event_name="booking.created",
        event_version=1,
        occurred_at=dt.datetime(2026, 5, 21, 14, 32, 11, tzinfo=dt.timezone.utc),
        tenant_id="9c3a7e1b-4d52-4f8e-b3a1-7c2d8e1f0a5c",
        user_id="f1a2b3c4-d5e6-4789-9abc-def012345678",
        actor="user",
        correlation_id="a1b2c3d4-e5f6-7890-abcd-ef1234567890",
        causation_id=None,
        data={"appointment_id": "b8d3e4f5-1c2d-4e6f-8a9b-c3d4e5f6a7b8"},
    )


class TestAS11RealWrapperEndToEnd:
    """Round-2 AS11 — exercise the actual ThreadPoolExecutor path.

    Previous test_ingest_timeout.py mocked ``dispatch_envelope`` AND
    relied on the SQLite-bypass conftest. These tests run the REAL
    wrapper with a mocked inner so the threadpool actually spins up
    a worker, submit/done-callback fire, semaphore acquire+release
    happen for real.
    """

    def test_real_wrapper_happy_path_returns_inner_result(self) -> None:
        """Smoke test — the wrapper passes the inner result through."""
        sentinel = DispatchResult(outcome=DispatchOutcome.OK)
        with patch(
            "apps.eventbus.ingest_timeout.dispatch_envelope",
            return_value=sentinel,
        ):
            result = dispatch_with_timeout(_envelope(), timeout_s=2.0)
        assert result is sentinel

    def test_real_wrapper_releases_semaphore_on_success(self) -> None:
        """In-flight count returns to 0 after a successful dispatch.

        Defense check for AS5: semaphore released via done-callback
        when inner returns normally.
        """
        sentinel = DispatchResult(outcome=DispatchOutcome.OK)
        with patch(
            "apps.eventbus.ingest_timeout.dispatch_envelope",
            return_value=sentinel,
        ):
            dispatch_with_timeout(_envelope(), timeout_s=2.0)
        # Allow the done-callback a brief moment to fire.
        time.sleep(0.05)
        assert inflight_count() == 0


class TestAS6ThreadBomb:
    """Round-2 AS6 — saturation MUST return SATURATED, NOT queue.

    Bypass attempt: fire MORE submits than _MAX_INFLIGHT workers.
    Without defense: requests queue in ThreadPoolExecutor's
    unbounded SimpleQueue + worker count climbs unbounded.
    Defense: bounded semaphore + non-blocking acquire = SATURATED
    return BEFORE submit.
    """

    def test_saturation_returns_saturated_not_queued(self) -> None:
        """Hold all MAX_INFLIGHT slots, then submit one more.

        The extra submit MUST return DispatchOutcome.SATURATED
        immediately, not block waiting for a slot. We hold the
        slots via slow handlers gated by an Event.
        """
        # Use a real (un-mocked) blocking handler to hold slots.
        slot_held = threading.Event()
        release = threading.Event()

        def _slow_inner(envelope):
            slot_held.set()
            release.wait(timeout=5.0)
            return DispatchResult(outcome=DispatchOutcome.OK)

        with patch(
            "apps.eventbus.ingest_timeout.dispatch_envelope",
            side_effect=_slow_inner,
        ):
            # Fill all slots via background threads.
            held_futures = []
            for i in range(_MAX_INFLIGHT):
                t = threading.Thread(
                    target=lambda i=i: dispatch_with_timeout(
                        _envelope(event_id=f"HELD-{i:022d}"),
                        timeout_s=10.0,
                    ),
                    daemon=True,
                )
                t.start()
                held_futures.append(t)

            # Wait for at least one slot to be acquired (i.e. the
            # background workers have started + entered _slow_inner).
            # Give a generous window for thread spin-up.
            slot_held.wait(timeout=2.0)
            # Spin briefly to let all _MAX_INFLIGHT slots fill.
            deadline = time.monotonic() + 2.0
            while inflight_count() < _MAX_INFLIGHT and time.monotonic() < deadline:
                time.sleep(0.01)

            # The (_MAX_INFLIGHT + 1)th request MUST get SATURATED.
            result = dispatch_with_timeout(
                _envelope(event_id="OVERFLOW" + "0" * 18),
                timeout_s=2.0,
            )

            # Release the held slots so the test exits cleanly.
            release.set()

        assert result.outcome is DispatchOutcome.SATURATED, (
            f"AS6 bypass succeeded: oversubscription returned "
            f"{result.outcome!r} instead of SATURATED — request queued "
            f"in the executor's unbounded SimpleQueue."
        )


class TestAS5OrphanHoldsSlotUntilCompletion:
    """Round-2 AS5 — timed-out orphan thread holds its semaphore slot.

    If we released the slot on ``result()`` timeout (i.e. when we
    give up waiting), the next request would submit a NEW thread
    while the orphan still holds a DB connection — DB pool
    exhaustion cascade. Defense: release via done-callback, which
    fires only when the orphan ACTUALLY completes.
    """

    def test_timeout_does_not_release_slot_immediately(self) -> None:
        """A timed-out handler's slot stays held until inner returns.

        Bypass attempt the test PREVENTS: an attacker could try to
        fill the inflight count past _MAX_INFLIGHT by submitting
        events that time out quickly — if the slot were released on
        timeout, capacity would seem unlimited but DB connections
        would still pile up.
        """
        release = threading.Event()

        def _hang_inner(envelope):
            release.wait(timeout=5.0)
            return DispatchResult(outcome=DispatchOutcome.OK)

        with patch(
            "apps.eventbus.ingest_timeout.dispatch_envelope",
            side_effect=_hang_inner,
        ):
            before = inflight_count()
            result = dispatch_with_timeout(_envelope(), timeout_s=0.2)
            # Slot is STILL held (orphan continues running).
            assert result.outcome is DispatchOutcome.HANDLER_EXCEPTION
            assert isinstance(result.exception, TimeoutError)
            assert inflight_count() == before + 1, (
                "AS5 bypass succeeded: slot released on timeout instead "
                "of done-callback; orphan would race a new submit on DB pool."
            )

            # Release the orphan + wait for done-callback to fire.
            release.set()
            deadline = time.monotonic() + 2.0
            while inflight_count() != before and time.monotonic() < deadline:
                time.sleep(0.02)
            assert inflight_count() == before
