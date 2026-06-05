"""AuditSubscriber — Envelope → AuditLog mirror (Phase 2.2 PR-C)."""

from __future__ import annotations

import pytest

from apps.audit.models import AuditLog
from apps.eventbus import dispatcher, services, vocabulary as V
from apps.eventbus.dispatcher import reset_registry_cache
from apps.eventbus.subscribers import AuditSubscriber
from apps.tenancy.context import tenant_scope
from apps.tenancy.models import Tenant

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture(autouse=True)
def _wire_audit_subscriber(settings):
    settings.DOMAIN_EVENT_SUBSCRIBERS = ["apps.eventbus.subscribers.AuditSubscriber"]
    reset_registry_cache()
    yield
    reset_registry_cache()


def _emit_booking_created(tenant=None):
    return services.emit_booking_created(
        booking_id="bk-123",
        customer_id="cu-456",
        service_id="sv-789",
        slot_start="2026-05-19T10:00:00Z",
        booking_source="ai_direct",
        tenant=tenant,
        actor_type="ai",
        actor_id="ai_persona_v2",
    )


class TestAuditMirror:
    def test_dispatched_event_creates_audit_log_row(self):
        t = Tenant.objects.create(slug="aud-a", name="A")
        with tenant_scope(t):
            _emit_booking_created(tenant=t)

        dispatcher.dispatch_pending_events()

        audit_rows = AuditLog.all_tenants.filter(action=V.BOOKING_CREATED)
        assert audit_rows.count() == 1
        row = audit_rows.first()
        assert row.tenant_id == t.id
        assert row.target == "booking"
        # Domain id "bk-123" isn't a UUID → stored in payload, target_id=None.
        assert row.target_id is None
        assert row.payload["target_id_raw"] == "bk-123"
        # Same for non-UUID actor id "ai_persona_v2".
        assert row.actor_id is None
        assert row.payload["actor_id_raw"] == "ai_persona_v2"
        assert row.payload["event_id"]
        assert row.payload["actor_type"] == "ai"
        assert row.payload["data"]["booking_source"] == "ai_direct"

    def test_target_id_uuid_passes_through(self):
        # Domain id that IS a UUID lands in AuditLog.target_id directly.
        import uuid as _uuid

        booking_uuid = str(_uuid.uuid4())
        services.emit_booking_created(
            booking_id=booking_uuid,
            customer_id="c",
            service_id="s",
            slot_start="2026-05-19T10:00:00Z",
            booking_source="ai_direct",
        )

        dispatcher.dispatch_pending_events()

        row = AuditLog.all_tenants.filter(action=V.BOOKING_CREATED).latest("created_at")
        assert str(row.target_id) == booking_uuid
        # Not also duplicated into payload (only fallback path uses it).
        assert "target_id_raw" not in row.payload

    def test_target_id_none_when_domain_id_missing(self):
        # System events have no <domain>_id key. target_id stays None.
        services.emit(
            V.SYSTEM_MODULE_HEALTH_DEGRADED,
            {"module_name": "test", "severity": "info", "metric": "noop"},
            actor_type="system",
        )

        dispatcher.dispatch_pending_events()

        rows = AuditLog.all_tenants.filter(action=V.SYSTEM_MODULE_HEALTH_DEGRADED)
        row = rows.latest("created_at")
        assert row.target_id is None
        assert row.target == "system"

    def test_tenantless_event_writes_audit_with_null_tenant(self):
        # system.* events have tenant_id=None per taxonomy §8.
        services.emit(
            V.SYSTEM_MODULE_HEALTH_DEGRADED,
            {"module_name": "test", "severity": "info", "metric": "noop"},
            actor_type="system",
        )

        dispatcher.dispatch_pending_events()

        row = AuditLog.all_tenants.filter(action=V.SYSTEM_MODULE_HEALTH_DEGRADED).latest(
            "created_at"
        )
        assert row.tenant is None


class TestEnvelopeMapping:
    def test_payload_carries_full_envelope_summary(self):
        t = Tenant.objects.create(slug="aud-b", name="B")
        with tenant_scope(t):
            services.emit_customer_consent_changed(
                customer_id="cu-1",
                consent_type="ai_processing",
                granted=True,
                granted_at="2026-05-19T10:00:00Z",
                granted_via="mini_app",
                tenant=t,
                correlation_id="corr-XYZ",
            )

        dispatcher.dispatch_pending_events()

        row = AuditLog.all_tenants.filter(action=V.CUSTOMER_CONSENT_CHANGED).latest("created_at")
        assert row.payload["actor_role"] == "customer"
        assert row.payload["actor_type"] == "human"
        assert row.payload["correlation_id"] == "corr-XYZ"
        assert row.payload["data"]["consent_type"] == "ai_processing"
        assert row.payload["data"]["granted"] is True
        # Target derived from "customer.consent.changed" → "customer"
        assert row.target == "customer"
        # "cu-1" is not a UUID → stored in payload, target_id=None.
        assert row.target_id is None
        assert row.payload["target_id_raw"] == "cu-1"


class TestDirectInvocation:
    def test_subscriber_handle_dedupes_on_event_id(self):
        # Phase 2.2 cleanup #6: second handle() call on the same envelope
        # short-circuits via the event_id existence check, so AuditLog
        # ends up with exactly one row. This guards the dispatcher
        # at-least-once contract: a re-dispatched row (one subscriber in
        # the chain failed) won't grow the audit log linearly with retries.
        t = Tenant.objects.create(slug="aud-c", name="C")
        with tenant_scope(t):
            env = _emit_booking_created(tenant=t)

        sub = AuditSubscriber()
        sub.handle(env)
        sub.handle(env)

        rows = AuditLog.all_tenants.filter(payload__event_id=env.event_id)
        assert rows.count() == 1

    def test_dedup_holds_across_dispatcher_ticks(self, settings):
        # Phase 2.2 cleanup #6 — the scenario that originally motivated
        # the dedup: a multi-subscriber chain where one downstream
        # subscriber fails. The dispatcher commits AuditLog from the
        # successful AuditSubscriber but leaves is_dispatched=False;
        # next tick re-dispatches the row → AuditSubscriber runs again
        # → dedup must short-circuit.
        from apps.eventbus.dispatcher import reset_registry_cache as _reset

        class _FlakySub:
            calls = 0

            def handle(self, envelope):
                _FlakySub.calls += 1
                if _FlakySub.calls == 1:
                    raise RuntimeError("first tick fail")

        # Register Audit first (writes), Flaky second (fails on tick 1).
        settings.DOMAIN_EVENT_SUBSCRIBERS = [
            "apps.eventbus.subscribers.AuditSubscriber",
            f"{__name__}._FlakySubLocator",
        ]
        # Patch the dotted-path resolver to return our local class.
        import apps.eventbus.dispatcher as disp

        original_resolver = disp._resolve
        flaky_instance = _FlakySub()

        def _patched_resolve(path):
            if path.endswith("_FlakySubLocator"):
                return flaky_instance
            return original_resolver(path)

        disp._resolve = _patched_resolve
        _reset()

        try:
            t = Tenant.objects.create(slug="aud-e", name="E")
            with tenant_scope(t):
                env = _emit_booking_created(tenant=t)

            dispatcher.dispatch_pending_events()  # tick 1: flaky raises
            dispatcher.dispatch_pending_events()  # tick 2: flaky succeeds

            rows = AuditLog.all_tenants.filter(payload__event_id=env.event_id)
            assert rows.count() == 1  # dedup held across ticks
        finally:
            disp._resolve = original_resolver
            _reset()

    def test_dedup_isolates_by_event_id(self):
        # Two distinct envelopes (different event_ids) both land — dedup
        # is strictly per-event_id, not per event_name / tenant.
        t = Tenant.objects.create(slug="aud-d", name="D")
        with tenant_scope(t):
            env_one = _emit_booking_created(tenant=t)
            env_two = _emit_booking_created(tenant=t)

        sub = AuditSubscriber()
        sub.handle(env_one)
        sub.handle(env_two)

        assert env_one.event_id != env_two.event_id
        assert AuditLog.all_tenants.filter(payload__event_id=env_one.event_id).count() == 1
        assert AuditLog.all_tenants.filter(payload__event_id=env_two.event_id).count() == 1
