"""ReplayTrace model tests (DRF-497 / Sprint 5 / A1)."""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.utils import timezone

from apps.replay.models import ReplayTrace
from apps.tenancy.context import tenant_scope
from apps.tenancy.models import Tenant

pytestmark = pytest.mark.django_db


@pytest.fixture
def tenant() -> Tenant:
    return Tenant.objects.create(slug="rt-a", name="RT-A")


@pytest.fixture
def future_expiry():
    return timezone.now() + timedelta(days=30)


class TestReplayTrace:
    def test_create_defaults(self, tenant, future_expiry, settings):
        settings.STRICT_TENANT_SCOPE = "strict"
        with tenant_scope(tenant):
            row = ReplayTrace.objects.create(
                tenant=tenant,
                trace_id="trace-abc",
                expires_at=future_expiry,
            )
        row.refresh_from_db()
        assert row.redacted is True
        assert row.redaction_method == ""
        assert row.pipeline_steps == []

    def test_pipeline_steps_round_trip(self, tenant, future_expiry, settings):
        settings.STRICT_TENANT_SCOPE = "strict"
        steps = [
            {"name": "input", "text": "[PHONE]"},
            {"name": "intent", "value": "faq"},
            {"name": "tool_calls", "calls": [{"name": "search_kb", "args_hash": "abc"}]},
        ]
        with tenant_scope(tenant):
            row = ReplayTrace.objects.create(
                tenant=tenant,
                trace_id="trace-xyz",
                pipeline_steps=steps,
                redaction_method="regex_v1",
                expires_at=future_expiry,
            )
        row.refresh_from_db()
        assert row.pipeline_steps == steps
        assert row.redaction_method == "regex_v1"

    def test_tenant_scoping(self, settings, future_expiry):
        a = Tenant.objects.create(slug="rt-x", name="X")
        b = Tenant.objects.create(slug="rt-y", name="Y")

        settings.STRICT_TENANT_SCOPE = "strict"
        with tenant_scope(a):
            ReplayTrace.objects.create(tenant=a, trace_id="A1", expires_at=future_expiry)
        with tenant_scope(b):
            ReplayTrace.objects.create(tenant=b, trace_id="B1", expires_at=future_expiry)

        with tenant_scope(a):
            assert ReplayTrace.objects.count() == 1
            assert ReplayTrace.objects.first().trace_id == "A1"
        with tenant_scope(b):
            assert ReplayTrace.objects.count() == 1
            assert ReplayTrace.objects.first().trace_id == "B1"

        # Escape hatch sees both.
        assert ReplayTrace.all_tenants.count() == 2

    def test_indexes_declared(self):
        names = {tuple(idx.fields) for idx in ReplayTrace._meta.indexes}
        assert ("tenant", "-captured_at") in names
        assert ("trace_id",) in names
        assert ("expires_at",) in names

    def test_str_truncates_trace_id(self, tenant, future_expiry):
        row = ReplayTrace(
            tenant=tenant,
            trace_id="abcdefghijklmnop1234567890",
            expires_at=future_expiry,
        )
        s = str(row)
        assert "abcdefgh" in s  # first 8 chars present
        # Long trace_id NOT fully embedded in __str__
        assert "1234567890" not in s
