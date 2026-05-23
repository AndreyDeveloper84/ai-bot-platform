"""Tests for :mod:`apps.eventbus.ingest_timeout` (PR #507 A12).

Pins the §8.10 per-handler 8s budget. The timeout wrapper is tested
in isolation by mocking :func:`dispatch_envelope` — pure timing
contract, no DB involvement. The inner dispatcher's tests
(:mod:`apps.eventbus.tests.test_ingest_dispatcher`) cover the DB
side; this module covers only the timeout-wrapping behaviour.

This isolation also sidesteps the SQLite test backend's lock-on-
threaded-write limitation: the executor runs ``dispatch_envelope``
in a worker thread, and SQLite + ``transaction=True`` test DB +
concurrent writes deadlock. Mocking the inner call removes the
threaded DB write entirely.
"""

from __future__ import annotations

import datetime as dt
import time
from unittest.mock import patch

from apps.eventbus.ingest_dispatcher import (
    DispatchOutcome,
    DispatchResult,
)
from apps.eventbus.ingest_envelope import IngestEnvelope
from apps.eventbus.ingest_timeout import (
    DEFAULT_HANDLER_TIMEOUT_S,
    dispatch_with_timeout,
)


def _envelope() -> IngestEnvelope:
    return IngestEnvelope(
        event_id="01J9HXKM8Z2T4V6R8Q1P3D5F7E",
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


def test_default_budget_matches_contract_8_seconds() -> None:
    """§8.10 — the contract value is 8 seconds. Pin it here."""
    assert DEFAULT_HANDLER_TIMEOUT_S == 8.0


def test_fast_inner_dispatch_returns_unchanged() -> None:
    """A fast (well-under-budget) inner dispatch result passes through."""
    sentinel_result = DispatchResult(outcome=DispatchOutcome.OK)
    with patch(
        "apps.eventbus.ingest_timeout.dispatch_envelope",
        return_value=sentinel_result,
    ):
        result = dispatch_with_timeout(_envelope(), timeout_s=2.0)
    assert result is sentinel_result


def test_slow_inner_dispatch_times_out_to_handler_exception() -> None:
    """§8.10 — inner call exceeding the budget surfaces as
    HANDLER_EXCEPTION with :class:`TimeoutError`."""

    def _slow_inner(envelope: IngestEnvelope) -> DispatchResult:
        time.sleep(2.0)
        return DispatchResult(outcome=DispatchOutcome.OK)

    with patch(
        "apps.eventbus.ingest_timeout.dispatch_envelope",
        side_effect=_slow_inner,
    ):
        start = time.monotonic()
        result = dispatch_with_timeout(_envelope(), timeout_s=0.2)
        elapsed = time.monotonic() - start

    assert result.outcome is DispatchOutcome.HANDLER_EXCEPTION
    assert isinstance(result.exception, TimeoutError)
    assert elapsed < 1.5, f"timeout did not fire; elapsed={elapsed:.2f}s"


def test_timeout_error_message_names_contract_section() -> None:
    """Operator triage relies on the exception text pointing at §8.10."""

    def _slow_inner(envelope: IngestEnvelope) -> DispatchResult:
        time.sleep(2.0)
        return DispatchResult(outcome=DispatchOutcome.OK)

    with patch(
        "apps.eventbus.ingest_timeout.dispatch_envelope",
        side_effect=_slow_inner,
    ):
        result = dispatch_with_timeout(_envelope(), timeout_s=0.1)

    assert "8.10" in str(result.exception)
    assert "budget" in str(result.exception)


def test_inner_raised_exception_propagates_through_executor() -> None:
    """A real (non-timeout) exception inside ``dispatch_envelope``
    still propagates as the wrapped result."""
    inner_result = DispatchResult(
        outcome=DispatchOutcome.HANDLER_EXCEPTION,
        exception=RuntimeError("real exception, not timeout"),
    )
    with patch(
        "apps.eventbus.ingest_timeout.dispatch_envelope",
        return_value=inner_result,
    ):
        result = dispatch_with_timeout(_envelope(), timeout_s=2.0)

    assert result is inner_result
    assert isinstance(result.exception, RuntimeError)
    assert not isinstance(result.exception, TimeoutError)
