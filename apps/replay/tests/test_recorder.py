"""TraceRecorder.capture() tests (DRF-498 / Sprint 5 / A2)."""

from __future__ import annotations

from unittest import mock

import pytest

from apps.replay.models import ReplayTrace
from apps.replay.recorder import TraceRecorder
from apps.tenancy.context import tenant_scope
from apps.tenancy.models import Tenant

pytestmark = pytest.mark.django_db


@pytest.fixture
def tenant() -> Tenant:
    return Tenant.objects.create(slug="rec-a", name="Rec-A")


@pytest.fixture
def recorder():
    return TraceRecorder()


class TestSampling:
    def test_sample_rate_zero_no_row(self, recorder, tenant, settings):
        settings.REPLAY_SAMPLE_RATE_TEST = 0.0
        with tenant_scope(tenant):
            result = recorder.capture("t1", [{"step": "input"}])
        assert result is None
        assert ReplayTrace.all_tenants.count() == 0

    def test_sample_rate_one_creates_row(self, recorder, tenant, settings):
        settings.REPLAY_SAMPLE_RATE_TEST = 1.0
        with tenant_scope(tenant):
            result = recorder.capture("t1", [{"step": "input"}])
        assert result is not None
        assert ReplayTrace.all_tenants.count() == 1
        row = ReplayTrace.all_tenants.first()
        assert row.trace_id == "t1"
        assert row.redaction_method == "regex_v1"


class TestRedactorWiring:
    def test_redactor_invoked_before_persist(self, tenant, settings):
        settings.REPLAY_SAMPLE_RATE_TEST = 1.0
        # Build a recorder with a real redactor, then verify the steps
        # got redacted on the resulting row.
        recorder = TraceRecorder()
        steps = [{"name": "input", "text": "call +74951234567 please"}]
        with tenant_scope(tenant):
            recorder.capture("t-red", steps)
        row = ReplayTrace.all_tenants.get(trace_id="t-red")
        # Phone redacted in DB; input untouched outside.
        assert "[PHONE]" in row.pipeline_steps[0]["text"]
        assert "+74951234567" not in row.pipeline_steps[0]["text"]
        # And the in-memory `steps` list still has the raw value
        # (recorder doesn't mutate the input).
        assert "+74951234567" in steps[0]["text"]


class TestNoTenantContext:
    def test_no_tenant_skips_capture(self, recorder, settings, caplog):
        import logging

        settings.REPLAY_SAMPLE_RATE_TEST = 1.0
        # No tenant_scope wrapper → current_tenant() returns None.
        with caplog.at_level(logging.INFO):
            result = recorder.capture("t-no-tenant", [])
        assert result is None
        assert ReplayTrace.all_tenants.count() == 0
        assert any("no_tenant" in rec.message for rec in caplog.records)


class TestSwallowsExceptions:
    def test_db_error_does_not_raise(self, tenant, settings, caplog):
        import logging

        settings.REPLAY_SAMPLE_RATE_TEST = 1.0
        recorder = TraceRecorder()

        # Patch ReplayTrace.all_tenants.create to raise.
        with mock.patch("apps.replay.models.ReplayTrace.all_tenants") as mocked_mgr:
            mocked_mgr.create.side_effect = RuntimeError("simulated DB down")
            with tenant_scope(tenant), caplog.at_level(logging.ERROR):
                result = recorder.capture("t-boom", [])
        # No crash; result is None; error logged.
        assert result is None
        assert any("capture_failed" in rec.message for rec in caplog.records)


class TestEventEmission:
    def test_replay_captured_event_emitted(self, tenant, settings):
        from apps.events.models import Event

        settings.REPLAY_SAMPLE_RATE_TEST = 1.0
        recorder = TraceRecorder()
        with tenant_scope(tenant):
            recorder.capture("t-evt", [{"step": "x"}])
        ev = Event.objects.filter(tenant=tenant, event_name="replay_captured").first()
        assert ev is not None
        assert ev.properties["trace_id"] == "t-evt"
        assert ev.properties["redaction_method"] == "regex_v1"
        assert ev.properties["steps_count"] == 1


class TestExpiresAtStamp:
    def test_expires_at_uses_retention_setting(self, tenant, settings):
        from datetime import timedelta

        from django.utils import timezone

        settings.REPLAY_SAMPLE_RATE_TEST = 1.0
        settings.REPLAY_RETENTION_DAYS = 7
        recorder = TraceRecorder()
        before = timezone.now()
        with tenant_scope(tenant):
            recorder.capture("t-exp", [])
        row = ReplayTrace.all_tenants.get(trace_id="t-exp")
        expected = before + timedelta(days=7)
        # Allow a few seconds for test execution time.
        delta = abs((row.expires_at - expected).total_seconds())
        assert delta < 5, f"expires_at off by {delta}s"
