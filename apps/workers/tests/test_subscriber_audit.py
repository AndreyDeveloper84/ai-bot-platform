"""Tests for the boot-time ``worker.subscriber_audit`` emit (issue #502).

Acceptance from tech lead 2026-05-22:

* One ``worker.subscriber_audit`` event per process boot.
* Payload includes handler name, frozen ``_RESOLVED_REQUIRES_TENANT``,
  MRO chain.
* Runbook query: read latest ``worker.subscriber_audit`` row.
* Tests must verify the event exists after a handler is registered +
  ``emit_subscriber_audit()`` runs.
"""

from __future__ import annotations

import asyncio
import logging
import threading

import pytest

from apps.events.models import Event
from apps.workers import subscriber_audit
from apps.workers.base import TenantAwareTask
from apps.workers.registry import clear_registry, iter_handlers, register

# Capture the real guard at module import time, BEFORE any autouse
# fixture has a chance to monkeypatch it. Tests that exercise the
# real guard reach for this constant instead of re-importing.
_REAL_SKIP_GUARD = subscriber_audit._is_management_command_that_should_skip

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _clean_registry_and_audit_guard(monkeypatch):
    """Reset both the handler registry and the per-process audit guard
    so each test gets a clean slate. ALSO bypass the pytest-skip
    guard by default — tests want to exercise the emit path. Tests
    that need the skip behaviour re-patch the guard explicitly.
    """

    clear_registry()
    subscriber_audit._reset_for_tests()
    # By default, bypass the management-command/pytest skip — tests
    # opt in to the skip behaviour via their own monkeypatches.
    monkeypatch.setattr(
        subscriber_audit,
        "_is_management_command_that_should_skip",
        lambda: False,
    )
    yield
    clear_registry()
    subscriber_audit._reset_for_tests()


class TestRegistryAccessor:
    def test_iter_handlers_returns_stream_handler_pairs(self):
        """Canonical accessor: ``(stream, handler_instance)`` pairs."""

        @register("ingress:max")
        class _H(TenantAwareTask):
            def handle(self, payload):  # noqa: ANN001
                pass

        pairs = iter_handlers()
        assert len(pairs) == 1
        stream, handler = pairs[0]
        assert stream == "ingress:max"
        assert isinstance(handler, _H)

    def test_iter_handlers_empty_when_no_registrations(self):
        assert iter_handlers() == []


class TestEmitSubscriberAudit:
    def test_emit_writes_one_event_with_handler_inventory(self):
        """Happy path: register a handler, call emit, verify the audit
        row exists with the expected payload shape."""

        @register("ingress:max")
        class _MaxHandler(TenantAwareTask):
            def handle(self, payload):  # noqa: ANN001
                pass

        result = subscriber_audit.emit_subscriber_audit()
        assert result is True, "expected emit to succeed"

        rows = list(Event.objects.filter(event_type="worker.subscriber_audit").values("payload"))
        assert len(rows) == 1
        payload = rows[0]["payload"]
        assert payload["subscriber_count"] == 1
        assert len(payload["handlers"]) == 1
        entry = payload["handlers"][0]
        assert entry["stream"] == "ingress:max"
        assert (
            entry["handler_class"]
            == "TestEmitSubscriberAudit.test_emit_writes_one_event_with_handler_inventory.<locals>._MaxHandler"
        )
        assert entry["requires_tenant"] is True  # default ClassVar
        assert any("TenantAwareTask" in c for c in entry["mro_chain"])

    def test_emit_idempotent_per_process(self):
        """Two consecutive calls within the same process emit only ONE
        audit row — the module-level ``_AUDIT_EMITTED`` guard. Per-
        process semantics so accidental re-call (test reload, manual
        invocation) doesn't double-write."""

        @register("ingress:max")
        class _H(TenantAwareTask):
            def handle(self, payload):  # noqa: ANN001
                pass

        first = subscriber_audit.emit_subscriber_audit()
        second = subscriber_audit.emit_subscriber_audit()

        assert first is True
        assert second is False, "second call must be a no-op"
        assert Event.objects.filter(event_type="worker.subscriber_audit").count() == 1

    def test_emit_captures_opt_out_subscriber(self):
        """``requires_tenant=False`` opt-outs surface on the audit row
        so operators can verify the inventory at flip time."""

        @register("ingress:system")
        class _SystemHandler(TenantAwareTask):
            # requires_tenant=False: documented opt-out — system-tier.
            requires_tenant = False

            def handle(self, payload):  # noqa: ANN001
                pass

        subscriber_audit.emit_subscriber_audit()
        row = Event.objects.filter(event_type="worker.subscriber_audit").first()
        assert row is not None
        entries = row.payload["handlers"]
        assert len(entries) == 1
        assert entries[0]["requires_tenant"] is False

    def test_emit_captures_multiple_handlers(self):
        """Two registrations → both appear in the inventory."""

        @register("ingress:max")
        class _MaxH(TenantAwareTask):
            def handle(self, payload):  # noqa: ANN001
                pass

        @register("ingress:vk")
        class _VkH(TenantAwareTask):
            def handle(self, payload):  # noqa: ANN001
                pass

        subscriber_audit.emit_subscriber_audit()
        row = Event.objects.filter(event_type="worker.subscriber_audit").first()
        assert row is not None
        assert row.payload["subscriber_count"] == 2
        streams = {h["stream"] for h in row.payload["handlers"]}
        assert streams == {"ingress:max", "ingress:vk"}


class TestEmitSafetyGuards:
    def test_emit_swallows_exceptions_returns_false(self, monkeypatch):
        """If the emit infrastructure raises (audit table missing during
        migrate, etc.), the helper logs + returns False — boot continues.
        Observability must not crash production."""

        from apps.events import services as events_services

        def boom(*args, **kwargs):
            raise RuntimeError("simulated audit table missing")

        monkeypatch.setattr(events_services, "emit", boom)

        result = subscriber_audit.emit_subscriber_audit()
        assert result is False

    def test_skip_during_pytest_invocation_when_argv_indicates(self, monkeypatch):
        """The skip-on-management-command guard. Re-patch the guard to
        the REAL implementation (not the autouse-bypass) + simulate
        pytest argv. Helper must return False without writing."""

        import sys

        # Restore the real guard (override the autouse fixture's bypass).
        monkeypatch.setattr(
            subscriber_audit,
            "_is_management_command_that_should_skip",
            _REAL_SKIP_GUARD,
        )
        monkeypatch.setattr(sys, "argv", ["/usr/local/bin/pytest", "tests/"])
        subscriber_audit._reset_for_tests()

        result = subscriber_audit.emit_subscriber_audit()
        assert result is False, "expected skip under pytest argv"
        assert Event.objects.filter(event_type="worker.subscriber_audit").count() == 0

    def test_skip_during_migrate_command(self, monkeypatch):
        """``manage.py migrate`` boots apps before audit table exists.
        The guard must short-circuit BEFORE the emit call so no INSERT
        attempt happens against the missing table."""

        import sys

        monkeypatch.setattr(
            subscriber_audit,
            "_is_management_command_that_should_skip",
            _REAL_SKIP_GUARD,
        )
        monkeypatch.setattr(sys, "argv", ["manage.py", "migrate"])
        subscriber_audit._reset_for_tests()

        result = subscriber_audit.emit_subscriber_audit()
        assert result is False
        assert Event.objects.filter(event_type="worker.subscriber_audit").count() == 0


class TestAppConfigReadyIntegration:
    """End-to-end: AppConfig.ready() actually fires the emit.

    This test exercises the real Django app-readiness path that the
    pre-flip checklist relies on. We re-invoke the WorkersConfig
    ready() hook directly (the autouse fixture above resets the guard
    before each test).
    """

    def test_ready_hook_emits_audit_when_handler_registered(self):
        """Simulating boot: handler registered + ready() called →
        audit row exists. Runbook query template:
        ``Event.objects.filter(event_type='worker.subscriber_audit').latest('created_at')``.
        """

        @register("ingress:max")
        class _H(TenantAwareTask):
            def handle(self, payload):  # noqa: ANN001
                pass

        from django.apps import apps as django_apps

        workers_config = django_apps.get_app_config("workers")
        workers_config.ready()

        rows = list(Event.objects.filter(event_type="worker.subscriber_audit"))
        assert len(rows) == 1
        assert rows[0].payload["subscriber_count"] == 1


class TestAsyncContextDetection:
    """DRF-1153 — the probe that decides whether the write needs a thread."""

    def test_false_on_a_plain_synchronous_thread(self):
        """gunicorn config.wsgi / celery / manage.py — no running loop."""

        assert subscriber_audit._running_in_async_context() is False

    def test_true_inside_a_running_event_loop(self):
        """uvicorn config.asgi — ``ready()`` runs inside the loop."""

        async def _probe():
            return subscriber_audit._running_in_async_context()

        assert asyncio.run(_probe()) is True


@pytest.mark.django_db(transaction=True)
class TestEmitUnderAsyncBoot:
    """DRF-1153 — the ASGI boot path.

    ``uvicorn config.asgi:application`` loads Django apps from inside a
    running event loop, so ``Event.objects.create()`` in
    ``apps.events.services.emit`` raised ``SynchronousOnlyOperation``.
    ``emit()`` swallowed it, logged ``events.emit_failed`` at ERROR, and
    returned — leaving ``emit_subscriber_audit()`` to log «emitted» and
    set its per-process guard with ZERO rows written.

    ``transaction=True`` because the fixed write path runs on a worker
    thread: a thread gets its own DB connection and would not see (or
    be seen by) the wrapping transaction that plain ``django_db`` uses.
    """

    @staticmethod
    def _register_one_handler():
        @register("ingress:max")
        class _MaxHandler(TenantAwareTask):
            def handle(self, payload):  # noqa: ANN001
                pass

        return _MaxHandler

    def test_emit_from_async_context_writes_the_audit_row(self):
        """The regression: under a running loop the row must land."""

        self._register_one_handler()

        async def _boot():
            return subscriber_audit.emit_subscriber_audit()

        assert asyncio.run(_boot()) is True

        rows = list(Event.objects.filter(event_type="worker.subscriber_audit"))
        assert len(rows) == 1, "audit row must exist after an ASGI-style boot"
        assert rows[0].payload["subscriber_count"] == 1
        assert rows[0].payload["handlers"][0]["stream"] == "ingress:max"

    def test_emit_from_async_context_logs_no_error(self, caplog):
        """The noise half of the ticket: zero ERROR records on boot."""

        self._register_one_handler()

        async def _boot():
            return subscriber_audit.emit_subscriber_audit()

        with caplog.at_level(logging.DEBUG):
            assert asyncio.run(_boot()) is True

        errors = [r for r in caplog.records if r.levelno >= logging.ERROR]
        assert errors == [], f"expected a silent boot, got: {[r.getMessage() for r in errors]}"
        assert not any("emit_failed" in r.getMessage() for r in caplog.records)
        assert any("workers.subscriber_audit.emitted" in r.getMessage() for r in caplog.records)

    def test_ready_hook_from_async_context_does_not_crash_boot(self):
        """End-to-end on the real production path: ``WorkersConfig.ready()``
        invoked from inside a running event loop must neither raise nor
        lose the audit row."""

        from django.apps import apps as django_apps

        self._register_one_handler()
        workers_config = django_apps.get_app_config("workers")

        async def _boot():
            # Must not raise — a failing audit may never break app loading.
            workers_config.ready()

        asyncio.run(_boot())

        assert Event.objects.filter(event_type="worker.subscriber_audit").count() == 1

    def test_emit_from_async_context_stays_idempotent(self):
        """The per-process guard survives the threaded path — two boots
        in one process still write exactly one row."""

        self._register_one_handler()

        async def _boot():
            return subscriber_audit.emit_subscriber_audit()

        first = asyncio.run(_boot())
        second = asyncio.run(_boot())

        assert first is True
        assert second is False
        assert Event.objects.filter(event_type="worker.subscriber_audit").count() == 1


class TestAsyncBootSafetyGuards:
    """DRF-1153 — the new code paths must keep the «never crash boot»
    property that the defensive try/except was put there to provide."""

    def test_boot_survives_thread_spawn_failure(self, monkeypatch):
        """``RuntimeError: can't start new thread`` (tight RLIMIT_NPROC or
        the systemd unit's MemoryMax) must be swallowed, not propagated
        into ``AppConfig.ready()``."""

        def _explode(*args, **kwargs):
            raise RuntimeError("can't start new thread")

        monkeypatch.setattr(threading, "Thread", _explode)

        async def _boot():
            return subscriber_audit.emit_subscriber_audit()

        assert asyncio.run(_boot()) is False
        assert subscriber_audit._AUDIT_EMITTED is False, "a failed attempt must stay retryable"

    def test_boot_survives_exception_inside_the_worker_thread(self, monkeypatch):
        """A raise on the worker thread is caught there; the caller sees
        ``False`` and app loading continues."""

        from apps.events import services as events_services

        def _boom(*args, **kwargs):
            raise RuntimeError("simulated audit table missing")

        monkeypatch.setattr(events_services, "emit", _boom)

        async def _boot():
            return subscriber_audit.emit_subscriber_audit()

        assert asyncio.run(_boot()) is False
        assert subscriber_audit._AUDIT_EMITTED is False

    def test_swallowed_insert_is_not_reported_as_emitted(self, monkeypatch, caplog):
        """``emit()`` swallows its own DB errors and returns ``False``.
        The audit must NOT claim success — that false positive is what
        made the ASGI inventory silently incomplete."""

        from apps.events import services as events_services

        monkeypatch.setattr(events_services, "emit", lambda *a, **kw: False)

        with caplog.at_level(logging.DEBUG):
            assert subscriber_audit.emit_subscriber_audit() is False

        assert subscriber_audit._AUDIT_EMITTED is False
        assert any(
            "workers.subscriber_audit.not_recorded" in r.getMessage() for r in caplog.records
        )
        assert not any("workers.subscriber_audit.emitted" in r.getMessage() for r in caplog.records)
