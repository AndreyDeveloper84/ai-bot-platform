"""Billing consumer tests (C4 — pilot 2026-08-15).

Pins the frozen-contract behaviours:

* the three C4 names parse at the envelope layer and dispatch to
  registered v1 handlers;
* dispatcher-level dedupe: a replayed ``event_id`` short-circuits
  (``DUPLICATE``) and the handler is NOT re-invoked;
* unknown ``event_version`` dead-letters (``unknown_event_version``),
  never silent acceptance;
* missing required payload keys are acked with a loud warning
  (permanent producer bug), unknown extras are ignored;
* money values stay out of log records.
"""

from __future__ import annotations

import datetime as dt
import logging
from typing import Any
from unittest.mock import MagicMock

import pytest

from apps.eventbus import ingest_dispatcher
from apps.eventbus.ingest_dispatcher import (
    DispatchOutcome,
    dispatch_envelope,
    registered_handlers,
)
from apps.eventbus.ingest_envelope import IngestEnvelope, parse_envelope
from apps.eventbus.models import IngestDLQ, IngestDedupe


pytestmark = pytest.mark.django_db

TENANT_ID = "9c3a7e1b-4d52-4f8e-b3a1-7c2d8e1f0a5c"
AYLA_USER_ID = "f1a2b3c4-d5e6-4789-9abc-def012345678"
SPECIALIST_ID = "7c2d8e1f-0a5c-4c3a-9e1b-4d52f8eb3a17"
APPOINTMENT_ID = "b8d3e4f5-1c2d-4e6f-8a9b-c3d4e5f6a7b8"

_PAYLOADS: dict[str, dict[str, Any]] = {
    "subscription.activated": {
        "specialist_id": SPECIALIST_ID,
        "tariff": "solo",
        "period_end": "2026-08-31",
    },
    "subscription.past_due": {
        "specialist_id": SPECIALIST_ID,
        "debt_amount": "690.00",
        "failed_attempts": 3,
    },
    "billing.fee_charged": {
        "specialist_id": SPECIALIST_ID,
        "appointment_id": APPOINTMENT_ID,
        "amount": "90.00",
        "period": "2026-08",
    },
}


@pytest.fixture(autouse=True)
def _fail_open_tenant_verify(settings) -> None:
    # Pre-#246 transition bridge — same pattern as the other consumer tests.
    settings.EVENT_INGEST_TENANT_VERIFY_FAIL_OPEN = True


def _envelope(
    event_name: str,
    *,
    data: dict[str, Any] | None = None,
    event_id: str = "01J9BILLING000000000000001",
    event_version: int = 1,
) -> IngestEnvelope:
    return IngestEnvelope(
        event_id=event_id,
        event_name=event_name,
        event_version=event_version,
        occurred_at=dt.datetime(2026, 7, 18, 12, 0, tzinfo=dt.timezone.utc),
        tenant_id=TENANT_ID,
        user_id=AYLA_USER_ID,
        actor="system",
        correlation_id="a1b2c3d4-e5f6-7890-abcd-ef1234567890",
        causation_id=None,
        data=data if data is not None else _PAYLOADS[event_name],
    )


class TestEnvelopeParse:
    @pytest.mark.parametrize("event_name", sorted(_PAYLOADS))
    def test_c4_names_parse(self, event_name: str) -> None:
        env = parse_envelope(
            {
                "event_id": "01J9BILLINGPARSE00000000000",
                "event_name": event_name,
                "event_version": 1,
                "occurred_at": "2026-07-18T12:00:00Z",
                "tenant_id": TENANT_ID,
                "user_id": AYLA_USER_ID,
                "actor": "system",
                "correlation_id": "c1",
                "causation_id": None,
                "data": _PAYLOADS[event_name],
            }
        )
        assert env.event_name == event_name


class TestDispatch:
    @pytest.mark.parametrize("event_name", sorted(_PAYLOADS))
    def test_v1_dispatches_ok(self, event_name: str) -> None:
        result = dispatch_envelope(_envelope(event_name))
        assert result.outcome == DispatchOutcome.OK

    @pytest.mark.parametrize("event_name", sorted(_PAYLOADS))
    def test_replay_is_duplicate_and_handler_runs_once(self, event_name: str) -> None:
        key = (event_name, 1)
        real_handler = registered_handlers()[key]
        spy = MagicMock(side_effect=real_handler)
        ingest_dispatcher._REGISTRY[key] = spy
        try:
            env = _envelope(event_name)
            assert dispatch_envelope(env).outcome == DispatchOutcome.OK
            assert dispatch_envelope(env).outcome == DispatchOutcome.DUPLICATE
            assert dispatch_envelope(env).outcome == DispatchOutcome.DUPLICATE
            assert spy.call_count == 1
            assert IngestDedupe.objects.filter(event_id=env.event_id).count() == 1
        finally:
            ingest_dispatcher._REGISTRY[key] = real_handler

    @pytest.mark.parametrize("event_name", sorted(_PAYLOADS))
    def test_unknown_version_dead_letters(self, event_name: str) -> None:
        env = _envelope(event_name, event_version=2)
        result = dispatch_envelope(env)
        assert result.outcome == DispatchOutcome.UNKNOWN_EVENT_VERSION
        assert IngestDLQ.objects.filter(
            event_id=env.event_id, reason="unknown_event_version"
        ).exists()


class TestPayloadValidation:
    def test_missing_required_key_acked_with_warning(self, caplog) -> None:
        env = _envelope(
            "subscription.past_due",
            data={"specialist_id": SPECIALIST_ID},  # debt_amount/failed_attempts gone
        )
        with caplog.at_level(logging.WARNING, logger="apps.eventbus.consumers.billing"):
            result = dispatch_envelope(env)
        assert result.outcome == DispatchOutcome.OK
        assert any("missing_payload_keys" in r.message for r in caplog.records)

    def test_unknown_extra_keys_ignored(self) -> None:
        env = _envelope(
            "subscription.activated",
            data={**_PAYLOADS["subscription.activated"], "future_field": {"x": 1}},
        )
        assert dispatch_envelope(env).outcome == DispatchOutcome.OK

    def test_money_values_not_logged(self, caplog) -> None:
        env = _envelope("subscription.past_due")
        with caplog.at_level(logging.INFO, logger="apps.eventbus.consumers.billing"):
            assert dispatch_envelope(env).outcome == DispatchOutcome.OK
        billing_records = [r for r in caplog.records if r.name == "apps.eventbus.consumers.billing"]
        assert billing_records
        assert all("690.00" not in r.getMessage() for r in billing_records)
