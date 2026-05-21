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
        # B2 adversarial-pass: forensic headers use __reaper namespace.
        assert dlq_fields["__reaper.from"] == "ingress:max"
        assert dlq_fields["__reaper.entry_id"] == entry_id
        assert dlq_fields["__reaper.classification"] == "tenant_required_missing"
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

    def test_xadd_to_dlq_failure_leaves_entry_in_pel(self, fake_redis, settings, monkeypatch):
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


class TestReaperAdversarialPassRegressions:
    """Regression tests for blockers + design issues from adversarial-pass
    Code Reviewer on PR #508. Each maps to a specific catch — see commit
    message + memory `h3-waiver-pattern`.
    """

    def test_b1_invalid_batch_size_clamps_to_default(self, fake_redis, settings, caplog):
        """B1: misconfigured ``PEL_REAPER_BATCH_SIZE`` (non-int) must
        NOT crash Celery beat. Clamp logs ERROR + uses safe default."""

        settings.PEL_REAPER_ENABLED = True
        settings.PEL_REAPER_BATCH_SIZE = "not-a-number"

        @register("ingress:max")
        class _H(TenantAwareTask):
            def handle(self, payload):  # noqa: ANN001
                pass

        with caplog.at_level("ERROR", logger="apps.workers.reaper"):
            reaped = reaper.reap_pel_streams()

        assert reaped == 0
        assert any(
            "invalid_setting" in rec.message and "PEL_REAPER_BATCH_SIZE" in rec.message
            for rec in caplog.records
        )

    def test_b1_zero_batch_size_clamps_to_one(self, fake_redis, settings, caplog):
        """B1: batch_size=0 clamps to min=1 so forward progress is
        guaranteed even with a typo'd config."""

        settings.PEL_REAPER_ENABLED = True
        settings.PEL_REAPER_BATCH_SIZE = 0

        @register("ingress:max")
        class _H(TenantAwareTask):
            def handle(self, payload):  # noqa: ANN001
                pass

        fake_redis.xgroup_create("ingress:max", "consumers", "$", True)
        entry_id = fake_redis.xadd("ingress:max", {"data": "{}", "resolved_tenant_id": ""})
        fake_redis.pel.setdefault(("ingress:max", "consumers"), {})[entry_id] = {
            "data": "{}",
            "resolved_tenant_id": "",
        }

        with caplog.at_level("ERROR", logger="apps.workers.reaper"):
            reaped = reaper.reap_pel_streams()

        assert reaped == 1
        assert any("below_min_setting" in rec.message for rec in caplog.records)

    def test_b2_forensic_namespace_resists_field_spoofing(self, fake_redis, settings):
        """B2: ingress can't spoof reaper's forensic fields. Reaper's
        authoritative values win on canonical keys; original spoofed
        values preserved under ``__reaper.shadowed.<key>``."""

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
                "resolved_tenant_id": "",
                "__reaper.from": "ingress:vk",
                "__reaper.classification": "handler_failure",
            },
        )
        fake_redis.pel.setdefault(("ingress:max", "consumers"), {})[entry_id] = {
            "data": "{}",
            "resolved_tenant_id": "",
            "__reaper.from": "ingress:vk",
            "__reaper.classification": "handler_failure",
        }

        reaper.reap_pel_streams()

        _id, dlq_fields = fake_redis.xrange("ingress:max:dlq")[0]
        assert dlq_fields["__reaper.from"] == "ingress:max"
        assert dlq_fields["__reaper.classification"] == "tenant_required_missing"
        # Spoofed originals preserved for triage visibility.
        assert dlq_fields["__reaper.shadowed.__reaper.from"] == "ingress:vk"
        assert dlq_fields["__reaper.shadowed.__reaper.classification"] == "handler_failure"

    def test_b3_cursor_persists_across_ticks(self, fake_redis, settings):
        """B3: cursor written to Redis after each tick so next tick
        can resume from where this one left off (tail-starvation fix)."""

        settings.PEL_REAPER_ENABLED = True

        @register("ingress:max")
        class _H(TenantAwareTask):
            def handle(self, payload):  # noqa: ANN001
                pass

        fake_redis.xgroup_create("ingress:max", "consumers", "$", True)
        entry_id = fake_redis.xadd("ingress:max", {"data": "{}"})
        fake_redis.pel.setdefault(("ingress:max", "consumers"), {})[entry_id] = {
            "data": "{}",
        }

        reaper.reap_pel_streams()
        assert "pel_reaper:cursor:ingress:max" in fake_redis._kv

    def test_b3_read_cursor_returns_zero_when_unset(self, fake_redis):
        """B3: first invocation (no persisted cursor) returns ``"0"``
        so XAUTOCLAIM starts from the beginning."""

        from apps.workers.reaper import _read_cursor

        assert _read_cursor(fake_redis, "pel_reaper:cursor:unset") == "0"

    def test_d1_consumer_name_includes_hostname(self):
        """D-1: ``REAPER_CONSUMER_NAME`` includes hostname so two beat
        processes on different pods don't share consumer identity."""

        import socket

        from apps.workers.reaper import REAPER_CONSUMER_NAME

        assert REAPER_CONSUMER_NAME == f"reaper@{socket.gethostname()}"

    def test_d3_dlq_xadd_uses_maxlen(self, fake_redis, settings, monkeypatch):
        """D-3: DLQ XADD passes ``maxlen=`` + ``approximate=True`` so
        unbounded ingress misbehaviour can't grow DLQ forever."""

        settings.PEL_REAPER_ENABLED = True

        @register("ingress:max")
        class _H(TenantAwareTask):
            def handle(self, payload):  # noqa: ANN001
                pass

        fake_redis.xgroup_create("ingress:max", "consumers", "$", True)
        entry_id = fake_redis.xadd("ingress:max", {"data": "{}", "resolved_tenant_id": ""})
        fake_redis.pel.setdefault(("ingress:max", "consumers"), {})[entry_id] = {
            "data": "{}",
            "resolved_tenant_id": "",
        }

        recorded_xadd_kwargs = []
        real_xadd = fake_redis.xadd

        def xadd_spy(stream, fields, **kwargs):
            recorded_xadd_kwargs.append((stream, kwargs))
            return real_xadd(stream, fields, **kwargs)

        monkeypatch.setattr(fake_redis, "xadd", xadd_spy)

        reaper.reap_pel_streams()

        dlq_calls = [kwargs for (stream, kwargs) in recorded_xadd_kwargs if stream.endswith(":dlq")]
        assert len(dlq_calls) == 1
        assert dlq_calls[0].get("maxlen") == reaper.DLQ_STREAM_MAXLEN
        assert dlq_calls[0].get("approximate") is True

    def test_d5_non_utf8_bytes_do_not_crash_batch(self, fake_redis, settings):
        """D-5: non-UTF8 bytes in a field value MUST NOT crash the
        batch. ``errors="replace"`` lets the garbled row survive +
        the rest of the batch proceeds."""

        settings.PEL_REAPER_ENABLED = True

        @register("ingress:max")
        class _H(TenantAwareTask):
            def handle(self, payload):  # noqa: ANN001
                pass

        fake_redis.xgroup_create("ingress:max", "consumers", "$", True)
        bad_id = fake_redis.xadd("ingress:max", {"data": "OK"})
        good_id = fake_redis.xadd("ingress:max", {"data": "good"})
        fake_redis.pel.setdefault(("ingress:max", "consumers"), {})[bad_id] = {
            "data": b"\x80\x81\x82",
            b"resolved_tenant_id": b"",
        }
        fake_redis.pel[("ingress:max", "consumers")][good_id] = {
            "data": "good",
            "resolved_tenant_id": "",
        }

        # Must NOT raise UnicodeDecodeError.
        reaped = reaper.reap_pel_streams()

        assert reaped == 2
        assert len(fake_redis.xrange("ingress:max:dlq")) == 2


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
