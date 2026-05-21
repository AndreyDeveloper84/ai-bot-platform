"""Tests for :mod:`apps.eventbus.ingest_dispatcher` (Phase 0 / #432).

Pins the §5.1 idempotency contract (replay 3× → 1 effect), the
§8.1/§8.4/§8.5 outcome taxonomy, and the dedupe table semantics.
"""

from __future__ import annotations

import datetime as dt

import pytest

from apps.eventbus.ingest_dispatcher import (
    DispatchOutcome,
    dispatch_envelope,
    register,
    registered_handlers,
    unregister,
)
from apps.eventbus.ingest_envelope import IngestEnvelope
from apps.eventbus.models import IngestDedupe, IngestDLQ


pytestmark = pytest.mark.django_db


def _envelope(
    *,
    event_id: str = "01J9HXKM8Z2T4V6R8Q1P3D5F7E",
    event_name: str = "booking.created",
    event_version: int = 1,
) -> IngestEnvelope:
    return IngestEnvelope(
        event_id=event_id,
        event_name=event_name,
        event_version=event_version,
        occurred_at=dt.datetime(2026, 5, 21, 14, 32, 11, tzinfo=dt.timezone.utc),
        tenant_id="9c3a7e1b-4d52-4f8e-b3a1-7c2d8e1f0a5c",
        user_id="f1a2b3c4-d5e6-4789-9abc-def012345678",
        actor="user",
        correlation_id="a1b2c3d4-e5f6-7890-abcd-ef1234567890",
        causation_id=None,
        data={"appointment_id": "b8d3e4f5-1c2d-4e6f-8a9b-c3d4e5f6a7b8"},
    )


@pytest.fixture(autouse=True)
def _clean_registry():
    """Reset the module-level registry between tests."""
    yield
    for key in list(registered_handlers().keys()):
        unregister(*key)


class TestRegistry:
    def test_register_then_lookup(self) -> None:
        calls: list[IngestEnvelope] = []
        register("booking.created", 1, calls.append)
        assert ("booking.created", 1) in registered_handlers()

    def test_double_register_raises(self) -> None:
        register("booking.created", 1, lambda e: None)
        with pytest.raises(ValueError, match="already registered"):
            register("booking.created", 1, lambda e: None)

    def test_distinct_versions_coexist(self) -> None:
        """§4.2 — two versions of the same name can register in parallel."""
        register("booking.created", 1, lambda e: None)
        register("booking.created", 2, lambda e: None)
        keys = set(registered_handlers().keys())
        assert ("booking.created", 1) in keys
        assert ("booking.created", 2) in keys


class TestDispatchOK:
    def test_ok_runs_handler_and_writes_dedupe(self) -> None:
        calls: list[IngestEnvelope] = []
        register("booking.created", 1, calls.append)

        env = _envelope()
        result = dispatch_envelope(env)

        assert result.outcome is DispatchOutcome.OK
        assert len(calls) == 1
        assert IngestDedupe.objects.filter(event_id=env.event_id).count() == 1


class TestIdempotency:
    def test_replay_three_times_runs_handler_once(self) -> None:
        """§5.4 — the canonical idempotency contract pinned by #447."""
        calls: list[IngestEnvelope] = []
        register("booking.created", 1, calls.append)

        env = _envelope()
        result1 = dispatch_envelope(env)
        result2 = dispatch_envelope(env)
        result3 = dispatch_envelope(env)

        assert result1.outcome is DispatchOutcome.OK
        assert result2.outcome is DispatchOutcome.DUPLICATE
        assert result3.outcome is DispatchOutcome.DUPLICATE
        assert len(calls) == 1
        assert IngestDedupe.objects.filter(event_id=env.event_id).count() == 1


class TestUnknownName:
    def test_unknown_event_name_returns_422_outcome_and_writes_dlq(self) -> None:
        env = _envelope()
        # Bypass envelope validation to construct a §8.5 case — events
        # with valid-shape but unknown name reach dispatch.
        env_unknown = IngestEnvelope(
            event_id=env.event_id,
            event_name="booking.invented",  # not in §3
            event_version=1,
            occurred_at=env.occurred_at,
            tenant_id=env.tenant_id,
            user_id=env.user_id,
            actor=env.actor,
            correlation_id=env.correlation_id,
            causation_id=env.causation_id,
            data=env.data,
        )

        result = dispatch_envelope(env_unknown)

        assert result.outcome is DispatchOutcome.UNKNOWN_EVENT_NAME
        dlq_row = IngestDLQ.objects.get(event_id=env.event_id)
        assert dlq_row.reason == "unknown_event_name"
        assert dlq_row.raw_body["event_name"] == "booking.invented"


class TestUnknownVersion:
    def test_unknown_version_returns_outcome_and_writes_dlq(self) -> None:
        """§4.2 / §8.4 — known name, no registered handler at that version."""
        register("booking.created", 1, lambda e: None)
        env = _envelope(event_version=2)  # only v1 registered

        result = dispatch_envelope(env)

        assert result.outcome is DispatchOutcome.UNKNOWN_EVENT_VERSION
        dlq_row = IngestDLQ.objects.get(event_id=env.event_id)
        assert dlq_row.reason == "unknown_event_version"


class TestHandlerException:
    def test_exception_rolls_back_dedupe_and_returns_exception(self) -> None:
        """§5.1 — handler raises → dedupe row rolls back → next delivery re-processes."""

        def _boom(env: IngestEnvelope) -> None:
            raise RuntimeError("upstream timeout")

        register("booking.created", 1, _boom)
        env = _envelope()

        result = dispatch_envelope(env)

        assert result.outcome is DispatchOutcome.HANDLER_EXCEPTION
        assert isinstance(result.exception, RuntimeError)
        # No dedupe row (§5.1 atomic rollback) → Ayla's retry re-attempts.
        assert IngestDedupe.objects.filter(event_id=env.event_id).count() == 0
