"""Tests for AuditLog + write_audit + retention (DRF-426 / B1)."""

from __future__ import annotations

from datetime import timedelta
from unittest.mock import patch
from uuid import uuid4

import pytest
from django.utils import timezone

from apps.audit.models import AuditLog
from apps.audit.services import write_audit
from apps.audit.tasks import cleanup_old_audit_logs
from apps.tenancy.context import tenant_scope
from apps.tenancy.models import Tenant

pytestmark = pytest.mark.django_db


class TestWriteAudit:
    """write_audit() — single entry point for audit-trail rows."""

    def test_writes_with_current_tenant(self):
        t = Tenant.objects.create(slug="t1", name="T1")
        with tenant_scope(t):
            write_audit("tenant.created", target="Tenant", target_id=t.id)

        row = AuditLog.all_tenants.get(action="tenant.created")
        assert row.tenant_id == t.id
        assert row.target == "Tenant"
        assert row.target_id == t.id

    def test_writes_without_tenant_context(self):
        # System events (no tenant in scope) — row has tenant=None.
        write_audit("system.startup", payload={"version": "0.1.0"})
        row = AuditLog.all_tenants.get(action="system.startup")
        assert row.tenant is None
        assert row.payload == {"version": "0.1.0"}

    def test_accepts_actor_and_payload(self):
        t = Tenant.objects.create(slug="t2", name="T2")
        actor = uuid4()
        with tenant_scope(t):
            write_audit(
                "user.login",
                target="User",
                target_id=actor,
                payload={"channel": "max"},
                actor_id=actor,
            )

        row = AuditLog.all_tenants.get(action="user.login")
        assert row.actor_id == actor
        assert row.target_id == actor
        assert row.payload["channel"] == "max"

    def test_swallows_db_errors_never_raises(self, caplog):
        # Force an error inside the create() call. write_audit must
        # log it and return without propagating — audit is observational.
        with patch(
            "apps.audit.models.AuditLog.all_tenants.create",
            side_effect=RuntimeError("simulated DB down"),
        ):
            with caplog.at_level("ERROR", logger="apps.audit.services"):
                write_audit("test.error")
        # No exception bubbled; log captured the swallow.
        assert any("audit.write_failed" in rec.message for rec in caplog.records)


class TestAuditLogModel:
    """Direct model contract."""

    def test_str_includes_action(self):
        t = Tenant.objects.create(slug="t3", name="T3")
        row = AuditLog.all_tenants.create(
            tenant=t, action="tenant.deactivated", target="Tenant", target_id=t.id
        )
        assert "tenant.deactivated" in str(row)
        assert str(t.id) in str(row)

    def test_tenant_scoping_via_default_manager(self):
        t1 = Tenant.objects.create(slug="t4a", name="T4a")
        t2 = Tenant.objects.create(slug="t4b", name="T4b")
        AuditLog.all_tenants.create(tenant=t1, action="t1.event")
        AuditLog.all_tenants.create(tenant=t2, action="t2.event")

        with tenant_scope(t1):
            visible = list(AuditLog.objects.values_list("action", flat=True))
        # Default manager scoped to t1.
        assert visible == ["t1.event"]

        # all_tenants escape hatch returns everything.
        assert AuditLog.all_tenants.count() == 2

    def test_tenant_nullable(self):
        # System events with no tenant in scope.
        AuditLog.all_tenants.create(action="system.startup")
        row = AuditLog.all_tenants.get(action="system.startup")
        assert row.tenant is None


class TestRetention:
    """cleanup_old_audit_logs honours AUDIT_LOG_RETENTION_DAYS."""

    def test_deletes_rows_older_than_cutoff(self, settings):
        settings.AUDIT_LOG_RETENTION_DAYS = 30
        now = timezone.now()

        # 2 rows older than cutoff, 1 newer.
        old1 = AuditLog.all_tenants.create(action="old.1")
        old2 = AuditLog.all_tenants.create(action="old.2")
        new = AuditLog.all_tenants.create(action="recent")
        AuditLog.all_tenants.filter(pk__in=[old1.pk, old2.pk]).update(
            created_at=now - timedelta(days=31),
        )

        deleted = cleanup_old_audit_logs()
        assert deleted == 2
        assert AuditLog.all_tenants.count() == 1
        assert AuditLog.all_tenants.first().pk == new.pk

    def test_default_retention_is_90_days(self, settings):
        # Don't override AUDIT_LOG_RETENTION_DAYS — base.py default.
        if hasattr(settings, "AUDIT_LOG_RETENTION_DAYS"):
            del settings.AUDIT_LOG_RETENTION_DAYS

        now = timezone.now()
        old = AuditLog.all_tenants.create(action="old")
        recent = AuditLog.all_tenants.create(action="recent")
        AuditLog.all_tenants.filter(pk=old.pk).update(created_at=now - timedelta(days=91))
        AuditLog.all_tenants.filter(pk=recent.pk).update(created_at=now - timedelta(days=30))

        deleted = cleanup_old_audit_logs()
        assert deleted == 1
        assert AuditLog.all_tenants.filter(pk=recent.pk).exists()
        assert not AuditLog.all_tenants.filter(pk=old.pk).exists()

    def test_returns_zero_when_nothing_to_delete(self, settings):
        settings.AUDIT_LOG_RETENTION_DAYS = 30
        AuditLog.all_tenants.create(action="recent")
        deleted = cleanup_old_audit_logs()
        assert deleted == 0
