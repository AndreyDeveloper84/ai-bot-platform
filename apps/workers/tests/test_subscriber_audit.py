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
