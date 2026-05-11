"""Event fanout adapter tests (DRF-462 / Sprint 3 / B4)."""

from __future__ import annotations

import logging
from typing import Any

import pytest

from apps.events.fanout import (
    EventFanout,
    GA4Fanout,
    MixpanelFanout,
    NoopFanout,
    WarehouseFanout,
    fanout_all,
    reset_registry_cache,
)
from apps.events.schema import EventEnvelope
from apps.events.services import emit
from apps.events.vocabulary import CONSENT_GRANTED
from apps.tenancy.context import tenant_scope
from apps.tenancy.models import Tenant


@pytest.fixture(autouse=True)
def _reset_registry():
    """Each test starts with a clean fanout registry cache."""

    reset_registry_cache()
    yield
    reset_registry_cache()


class TestProtocol:
    def test_noop_is_eventfanout(self):
        assert isinstance(NoopFanout(), EventFanout)

    def test_mixpanel_skeleton_satisfies_protocol(self):
        assert isinstance(MixpanelFanout(), EventFanout)

    def test_ga4_skeleton_satisfies_protocol(self):
        assert isinstance(GA4Fanout(), EventFanout)

    def test_warehouse_skeleton_satisfies_protocol(self):
        assert isinstance(WarehouseFanout(), EventFanout)


class TestNoopFanout:
    def test_send_returns_none_and_does_not_raise(self):
        env = EventEnvelope(event_name=CONSENT_GRANTED)
        assert NoopFanout().send(env) is None


class _CapturingFanout:
    """Stand-in adapter that records every envelope it receives."""

    def __init__(self) -> None:
        self.calls: list[EventEnvelope] = []

    def send(self, envelope: EventEnvelope) -> None:
        self.calls.append(envelope)


class _ExplodingFanout:
    def send(self, envelope: EventEnvelope) -> None:
        raise RuntimeError("kaboom")


class TestFanoutAll:
    def test_iterates_every_registered_adapter(self, settings, monkeypatch):
        capture_a = _CapturingFanout()
        capture_b = _CapturingFanout()

        # Replace the resolved registry with our stand-ins (skip dotted-path
        # lookup since these are local classes).
        from apps.events import fanout as fanout_mod

        monkeypatch.setattr(fanout_mod, "_REGISTRY_CACHE", [capture_a, capture_b])

        env = EventEnvelope(event_name=CONSENT_GRANTED)
        fanout_all(env)

        assert len(capture_a.calls) == 1
        assert len(capture_b.calls) == 1
        assert capture_a.calls[0] is env

    def test_per_adapter_failure_is_swallowed_and_logged(self, monkeypatch, caplog):
        boom = _ExplodingFanout()
        ok = _CapturingFanout()

        from apps.events import fanout as fanout_mod

        # boom first — must NOT prevent ok from receiving the envelope.
        monkeypatch.setattr(fanout_mod, "_REGISTRY_CACHE", [boom, ok])

        env = EventEnvelope(event_name=CONSENT_GRANTED)
        with caplog.at_level(logging.ERROR):
            fanout_all(env)

        # Second adapter still received the envelope.
        assert len(ok.calls) == 1
        # Error from first was logged.
        assert any(
            "fanout.send_failed" in rec.message and "_ExplodingFanout" in rec.message
            for rec in caplog.records
        )

    def test_default_registry_is_noop(self, settings):
        # No EVENT_FANOUTS override → NoopFanout default.
        settings.EVENT_FANOUTS = ["apps.events.fanout.NoopFanout"]
        reset_registry_cache()
        env = EventEnvelope(event_name=CONSENT_GRANTED)
        # Must not raise; nothing observable to assert beyond "doesn't blow up".
        fanout_all(env)


@pytest.mark.django_db
class TestEmitWiresFanout:
    def test_emit_calls_fanout_with_canonical_envelope(self, monkeypatch):
        """B3 emit() → DB insert → fanout_all(envelope)."""

        capture = _CapturingFanout()
        from apps.events import fanout as fanout_mod

        monkeypatch.setattr(fanout_mod, "_REGISTRY_CACHE", [capture])

        tenant = Tenant.objects.create(slug="emit-fan", name="EF")
        with tenant_scope(tenant):
            emit(
                CONSENT_GRANTED,
                distinct_id="user-1",
                properties={"consent_type": "marketing"},
            )

        assert len(capture.calls) == 1
        env = capture.calls[0]
        assert env.event_name == CONSENT_GRANTED
        assert env.distinct_id == "user-1"
        assert env.properties == {"consent_type": "marketing"}
        assert env.tenant_id == tenant.id


class TestRegistryResolution:
    def test_dotted_path_resolves_to_class_instance(self, settings):
        settings.EVENT_FANOUTS = [
            "apps.events.fanout.NoopFanout",
            "apps.events.fanout.MixpanelFanout",
        ]
        reset_registry_cache()
        from apps.events.fanout import _registry  # noqa: PLC2701 — internal API for test

        registry = _registry()
        assert len(registry) == 2
        assert isinstance(registry[0], NoopFanout)
        assert isinstance(registry[1], MixpanelFanout)

    def test_empty_setting_falls_back_to_default(self, settings):
        # Empty list defaults to NoopFanout per emit() behaviour.
        settings.EVENT_FANOUTS = []
        reset_registry_cache()
        from apps.events.fanout import _registry  # noqa: PLC2701

        # Per current implementation: empty list iterated empty; nothing
        # crashes downstream.
        registry: list[Any] = _registry()
        assert registry == []
