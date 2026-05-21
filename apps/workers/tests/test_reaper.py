"""Tests for the PEL reaper (issue #499).

Uses the same ``_FakeStreamRedis`` stub from ``test_consumer.py`` —
extended there with an ``xautoclaim`` method that matches the redis-py
tuple shape ``(next_cursor, claimed, deleted_ids)``.
"""

from __future__ import annotations

import json

import pytest

from apps.events.models import Event
from apps.ingress import streams
from apps.workers import reaper
from apps.workers.base import TenantAwareTask
from apps.workers.registry import clear_registry, register
from apps.workers.tasks import reap_pel
from apps.workers.tests.test_consumer import _FakeStreamRedis

pytestmark = pytest.mark.django_db


@pytest.fixture
def fake_redis(monkeypatch):
    fake = _FakeStreamRedis()
    monkeypatch.setattr(streams, "_client", lambda: fake)
    return fake


@pytest.fixture(autouse=True)
def _clean_registry():
    clear_registry()
    yield
    clear_registry()


# ---------------------------------------------------------------------------
# Disabled-mode behaviour — opt-in flag respected
# ---------------------------------------------------------------------------


class TestReaperDisabled:
    def test_reap_pel_streams_noops_when_disabled(self, fake_redis, settings):
        settings.PEL_REAPER_ENABLED = False
        # Even with PEL entries present, disabled mode returns 0.
        fake_redis.xgroup_create("ingress:max", "consumers", "$", True)
        fake_redis.xadd("ingress:max", {"data": "{}", "trace_id": "t1"})
        fake_redis.pel.setdefault(("ingress:max", "consumers"), {})["1700000000-1"] = {
            "data": "{}",
            "trace_id": "t1",
        }

        assert reaper.reap_pel_streams() == 0

    def test_celery_task_wrapper_respects_disabled(self, fake_redis, settings):
        # The Celery shared_task wraps reap_pel_streams — same opt-in.
        settings.PEL_REAPER_ENABLED = False
        assert reap_pel() == 0


# ---------------------------------------------------------------------------
# Happy path — terminal classification + DLQ move + XACK + audit emit
# ---------------------------------------------------------------------------


class TestReaperTerminalPath:
    def test_strict_mode_refusal_routed_to_dlq(self, fake_redis, settings):
        """B4 strict-mode refusal entry: empty resolved_tenant_id →
        classification ``tenant_required_missing`` → DLQ move + XACK +
        audit row."""

        settings.PEL_REAPER_ENABLED = True
        settings.PEL_REAPER_IDLE_SECONDS = 60
        settings.PEL_REAPER_BATCH_SIZE = 100

        @register("ingress:max")
        class _H(TenantAwareTask):
            def handle(self, payload):  # noqa: ANN001
                pass

        # Simulate a strict-mode refusal: entry in PEL with empty tenant id.
        fake_redis.xgroup_create("ingress:max", "consumers", "$", True)
        entry_id = fake_redis.xadd(
            "ingress:max",
            {
                "data": json.dumps({"hello": "world"}),
                "trace_id": "trace-A",
                "resolved_tenant_id": "",
            },
        )
        fake_redis.pel.setdefault(("ingress:max", "consumers"), {})[entry_id] = {
            "data": json.dumps({"hello": "world"}),
            "trace_id": "trace-A",
            "resolved_tenant_id": "",
        }

        reaped = reap_pel.run() if callable(getattr(reap_pel, "run", None)) else reap_pel()
        # apps.workers.tasks.reap_pel is a shared_task; calling it
        # directly invokes the underlying function.
        # Reap result depends on flow above; either way one entry should
        # have moved.
        assert reaped >= 1

        # Source PEL: empty (entry XACK'd by reaper).
        assert fake_redis.pel.get(("ingress:max", "consumers"), {}) == {}

        # DLQ stream: now contains the migrated entry with forensic headers.
        dlq_entries = fake_redis.xrange("ingress:max:dlq")
        assert len(dlq_entries) == 1
        _dlq_id, dlq_fields = dlq_entries[0]
        assert dlq_fields["_reaped_from"] == "ingress:max"
        assert dlq_fields["_reaped_entry_id"] == entry_id
        assert dlq_fields["_reaped_classification"] == "tenant_required_missing"
        # Original fields preserved.
        assert dlq_fields["trace_id"] == "trace-A"
        assert dlq_fields["resolved_tenant_id"] == ""

        # Audit row emitted with the correct event_type + payload shape.
        audit = list(
            Event.objects.filter(event_type="worker.pel_reaped").values("event_type", "payload")
        )
        assert len(audit) == 1
        row = audit[0]
        assert row["payload"]["stream"] == "ingress:max"
        assert row["payload"]["entry_id"] == entry_id
        assert row["payload"]["classification"] == "tenant_required_missing"
        assert row["payload"]["decision"] == "terminal"
        assert row["payload"]["dlq_stream"] == "ingress:max:dlq"

    def test_handler_failure_with_tenant_classified_separately(self, fake_redis, settings):
        """An entry that's in PEL but DOES carry a resolved tenant id
        (i.e. handler raised on a tenant-known dispatch — RuntimeError,
        upstream timeout, etc.) is classified ``handler_failure``, not
        ``tenant_required_missing``."""

        settings.PEL_REAPER_ENABLED = True

        @register("ingress:max")
        class _H(TenantAwareTask):
            def handle(self, payload):  # noqa: ANN001
                pass

        fake_redis.xgroup_create("ingress:max", "consumers", "$", True)
        entry_id = fake_redis.xadd(
            "ingress:max",
            {
                "data": "{}",
                "trace_id": "t-fail",
                "resolved_tenant_id": "9b1deb4d-3b7d-4bad-9bdd-2b0d7b3dcb6d",
            },
        )
        fake_redis.pel.setdefault(("ingress:max", "consumers"), {})[entry_id] = {
            "data": "{}",
            "trace_id": "t-fail",
            "resolved_tenant_id": "9b1deb4d-3b7d-4bad-9bdd-2b0d7b3dcb6d",
        }

        reap_pel()

        audit = list(Event.objects.filter(event_type="worker.pel_reaped").values("payload"))
        assert len(audit) == 1
        assert audit[0]["payload"]["classification"] == "handler_failure"


# ---------------------------------------------------------------------------
# Multi-stream + batch boundary
# ---------------------------------------------------------------------------


class TestReaperMultiStream:
    def test_reaps_every_registered_stream(self, fake_redis, settings):
        settings.PEL_REAPER_ENABLED = True

        @register("ingress:max")
        class _MaxHandler(TenantAwareTask):
            def handle(self, payload):  # noqa: ANN001
                pass

        @register("ingress:vk")
        class _VkHandler(TenantAwareTask):
            def handle(self, payload):  # noqa: ANN001
                pass

        for stream_name in ("ingress:max", "ingress:vk"):
            fake_redis.xgroup_create(stream_name, "consumers", "$", True)
            entry_id = fake_redis.xadd(stream_name, {"data": "{}", "trace_id": f"t-{stream_name}"})
            fake_redis.pel.setdefault((stream_name, "consumers"), {})[entry_id] = {
                "data": "{}",
                "trace_id": f"t-{stream_name}",
            }

        total = reaper.reap_pel_streams()
        assert total == 2
        # Each source-PEL emptied.
        assert fake_redis.pel.get(("ingress:max", "consumers"), {}) == {}
        assert fake_redis.pel.get(("ingress:vk", "consumers"), {}) == {}
        # Each DLQ holds one entry.
        assert len(fake_redis.xrange("ingress:max:dlq")) == 1
        assert len(fake_redis.xrange("ingress:vk:dlq")) == 1

    def test_batch_size_caps_per_tick(self, fake_redis, settings):
        settings.PEL_REAPER_ENABLED = True
        settings.PEL_REAPER_BATCH_SIZE = 2

        @register("ingress:max")
        class _H(TenantAwareTask):
            def handle(self, payload):  # noqa: ANN001
                pass

        fake_redis.xgroup_create("ingress:max", "consumers", "$", True)
        for _ in range(5):
            entry_id = fake_redis.xadd("ingress:max", {"data": "{}", "trace_id": "t"})
            fake_redis.pel.setdefault(("ingress:max", "consumers"), {})[entry_id] = {
                "data": "{}",
                "trace_id": "t",
            }

        first_tick = reaper.reap_pel_streams()
        assert first_tick == 2  # batch_size cap respected
        # 3 entries remain in PEL.
        assert len(fake_redis.pel[("ingress:max", "consumers")]) == 3

        second_tick = reaper.reap_pel_streams()
        assert second_tick == 2
        assert len(fake_redis.pel[("ingress:max", "consumers")]) == 1


# ---------------------------------------------------------------------------
# Empty / no-stream edge cases
# ---------------------------------------------------------------------------


class TestReaperEdgeCases:
    def test_no_registered_streams_returns_zero(self, fake_redis, settings):
        settings.PEL_REAPER_ENABLED = True
        assert reaper.reap_pel_streams() == 0

    def test_empty_pel_returns_zero(self, fake_redis, settings):
        settings.PEL_REAPER_ENABLED = True

        @register("ingress:max")
        class _H(TenantAwareTask):
            def handle(self, payload):  # noqa: ANN001
                pass

        # Stream registered, but no entries in PEL.
        fake_redis.xgroup_create("ingress:max", "consumers", "$", True)
        assert reaper.reap_pel_streams() == 0
        assert not list(fake_redis.xrange("ingress:max:dlq"))
