"""cleanup_expired_traces task tests (DRF-500 / Sprint 5 / A4)."""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.utils import timezone

from apps.audit.models import AuditLog
from apps.replay.models import ReplayTrace
from apps.replay.tasks import cleanup_expired_traces
from apps.tenancy.models import Tenant

pytestmark = pytest.mark.django_db


@pytest.fixture
def tenant() -> Tenant:
    return Tenant.objects.create(slug="ct-a", name="CT-A")


class TestCleanupExpiredTraces:
    def test_deletes_past_expiry_keeps_current(self, tenant):
        now = timezone.now()
        # Past-expiry row (deleted).
        ReplayTrace.all_tenants.create(
            tenant=tenant,
            trace_id="expired",
            expires_at=now - timedelta(seconds=1),
        )
        # Current row (kept).
        ReplayTrace.all_tenants.create(
            tenant=tenant,
            trace_id="fresh",
            expires_at=now + timedelta(days=30),
        )

        deleted = cleanup_expired_traces()
        assert deleted == 1
        remaining = list(ReplayTrace.all_tenants.values_list("trace_id", flat=True))
        assert remaining == ["fresh"]

    def test_boundary_strictly_less_than(self, tenant):
        """Filter is strict `<` — row with expires_at in the future survives."""

        # 1 minute margin so the cleanup call (run later in the test) still
        # sees the row as not-yet-expired.
        ReplayTrace.all_tenants.create(
            tenant=tenant,
            trace_id="boundary",
            expires_at=timezone.now() + timedelta(minutes=1),
        )
        deleted = cleanup_expired_traces()
        assert deleted == 0
        assert ReplayTrace.all_tenants.filter(trace_id="boundary").exists()

    def test_audit_row_written(self, tenant):
        now = timezone.now()
        ReplayTrace.all_tenants.create(
            tenant=tenant,
            trace_id="x",
            expires_at=now - timedelta(seconds=1),
        )
        cleanup_expired_traces()
        audit = AuditLog.all_tenants.filter(action="replay.cleanup_completed").first()
        assert audit is not None
        assert audit.payload["deleted_count"] == 1

    def test_empty_db_returns_zero_no_crash(self):
        # No rows at all — task succeeds with count=0.
        assert cleanup_expired_traces() == 0

    def test_cross_tenant_eviction(self):
        """System-level job uses all_tenants — evicts across tenants."""

        a = Tenant.objects.create(slug="ct-x", name="X")
        b = Tenant.objects.create(slug="ct-y", name="Y")
        now = timezone.now()
        ReplayTrace.all_tenants.create(
            tenant=a, trace_id="a-old", expires_at=now - timedelta(seconds=1)
        )
        ReplayTrace.all_tenants.create(
            tenant=b, trace_id="b-old", expires_at=now - timedelta(seconds=1)
        )
        deleted = cleanup_expired_traces()
        assert deleted == 2
        assert ReplayTrace.all_tenants.count() == 0
