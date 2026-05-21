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

        # ``@shared_task`` always exposes ``.run`` — call the underlying
        # function directly (Celery wrapper is the production entry point).
        reaped = reap_pel.run()
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


class TestReaperFailureModes:
    """Per first-pass Code Reviewer follow-up: each side-effect's failure
    mode is distinct and tested separately so a future refactor can't
    accidentally merge them back into one try/except.
    """

    def test_xadd_to_dlq_failure_leaves_entry_in_pel(
        self, fake_redis, settings, monkeypatch
    ):
        """Failure mode 1: XADD to DLQ raises (Redis down, OOM, etc).
        Entry MUST stay in source PEL — no XACK fired — so the next
        tick can retry. Loop must continue to the next entry."""

        settings.PEL_REAPER_ENABLED = True

        @register("ingress:max")
        class _H(TenantAwareTask):
            def handle(self, payload):  # noqa: ANN001
                pass

        fake_redis.xgroup_create("ingress:max", "consumers", "$", True)
        entry_id = fake_redis.xadd(
            "ingress:max",
            {"data": "{}", "trace_id": "t-xadd-fail", "resolved_tenant_id": ""},
        )
        fake_redis.pel.setdefault(("ingress:max", "consumers"), {})[entry_id] = {
            "data": "{}",
            "trace_id": "t-xadd-fail",
            "resolved_tenant_id": "",
        }

        # Patch xadd to raise on the DLQ stream only.
        real_xadd = fake_redis.xadd

        def xadd_raises_on_dlq(stream, fields):
            if stream.endswith(":dlq"):
                raise RuntimeError("simulated redis-down on DLQ stream")
            return real_xadd(stream, fields)

        monkeypatch.setattr(fake_redis, "xadd", xadd_raises_on_dlq)

        reaped = reaper.reap_pel_streams()

        # reaper.reap_pel_once continues past the failing entry.
        assert reaped == 0
        # Entry MUST still be in PEL (XACK never fired).
        assert entry_id in fake_redis.pel[("ingress:max", "consumers")]
        # No audit row — XADD never succeeded, emit not reached.
        assert not Event.objects.filter(event_type="worker.pel_reaped").exists()

    def test_xack_failure_after_xadd_leaves_entry_for_re_reap(
        self, fake_redis, settings, monkeypatch
    ):
        """Failure mode 2: XADD succeeds, XACK raises. Entry is now in
        BOTH DLQ AND source PEL. Next tick will re-claim and produce a
        duplicate DLQ row — known degenerate state. Audit emit MUST be
        skipped on this entry so the next tick's audit row isn't a
        duplicate-of-a-duplicate."""

        settings.PEL_REAPER_ENABLED = True

        @register("ingress:max")
        class _H(TenantAwareTask):
            def handle(self, payload):  # noqa: ANN001
                pass

        fake_redis.xgroup_create("ingress:max", "consumers", "$", True)
        entry_id = fake_redis.xadd(
            "ingress:max",
            {"data": "{}", "trace_id": "t-xack-fail", "resolved_tenant_id": ""},
        )
        fake_redis.pel.setdefault(("ingress:max", "consumers"), {})[entry_id] = {
            "data": "{}",
            "trace_id": "t-xack-fail",
            "resolved_tenant_id": "",
        }

        def xack_raises(*args, **kwargs):
            raise RuntimeError("simulated XACK failure")

        monkeypatch.setattr(fake_redis, "xack", xack_raises)

        reaper.reap_pel_streams()

        # DLQ has the entry (XADD succeeded).
        assert len(fake_redis.xrange("ingress:max:dlq")) == 1
        # Source PEL still has the entry (XACK failed).
        assert entry_id in fake_redis.pel[("ingress:max", "consumers")]
        # No audit row — skipped because XACK failed.
        assert not Event.objects.filter(event_type="worker.pel_reaped").exists()

    def test_emit_failure_after_xadd_and_xack_continues_batch(
        self, fake_redis, settings, monkeypatch
    ):
        """Failure mode 3: XADD + XACK both succeed, audit emit raises.
        Entry is fully moved (DLQ + XACK'd source); only the audit row
        is missing — forensic gap. The batch MUST continue, not break,
        so the rest of the entries aren't held hostage by an audit-DB
        hiccup."""

        settings.PEL_REAPER_ENABLED = True

        @register("ingress:max")
        class _H(TenantAwareTask):
            def handle(self, payload):  # noqa: ANN001
                pass

        fake_redis.xgroup_create("ingress:max", "consumers", "$", True)
        good_id = fake_redis.xadd("ingress:max", {"data": "{}", "trace_id": "t-good"})
        bad_id = fake_redis.xadd("ingress:max", {"data": "{}", "trace_id": "t-bad"})
        for eid in (bad_id, good_id):
            fake_redis.pel.setdefault(("ingress:max", "consumers"), {})[eid] = {
                "data": "{}",
                "trace_id": "t",
            }

        # Patch emit to raise on the first call only — exercise the
        # «batch continues» invariant.
        from apps.workers import reaper as reaper_mod

        call_count = {"n": 0}
        real_emit = reaper_mod.emit

        def emit_raises_first(*args, **kwargs):
            call_count["n"] += 1
            if call_count["n"] == 1:
                raise RuntimeError("simulated audit DB hiccup")
            return real_emit(*args, **kwargs)

        monkeypatch.setattr(reaper_mod, "emit", emit_raises_first)

        reaped = reaper.reap_pel_streams()

        # Both entries reaped (XADD + XACK both succeeded for each).
        assert reaped == 2
        assert fake_redis.pel.get(("ingress:max", "consumers"), {}) == {}
        assert len(fake_redis.xrange("ingress:max:dlq")) == 2
        # Audit emit fired for both; first raised, second succeeded.
        # The second emission's audit row is in the DB.
        rows = Event.objects.filter(event_type="worker.pel_reaped").count()
        assert rows == 1, (
            "expected 1 audit row from the successful emit; "
            "the raising emit must not block the batch"
        )


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
