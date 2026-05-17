"""Tests for AuditLog + write_audit + retention (DRF-426 / B1)."""

from __future__ import annotations

from datetime import timedelta
from unittest.mock import patch
from uuid import uuid4

import pytest
from django.utils import timezone

from apps.audit.models import AuditLog
from apps.audit.services import write_audit
from apps.audit.tasks import AUDIT_CLEANUP_ACTION, cleanup_old_audit_logs
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
        # 1 user "recent" row + 1 cleanup-run audit row (DRF-851).
        assert AuditLog.all_tenants.exclude(action=AUDIT_CLEANUP_ACTION).count() == 1
        assert AuditLog.all_tenants.exclude(action=AUDIT_CLEANUP_ACTION).first().pk == new.pk

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
        # Expect 0 "user" rows deleted — the cleanup also writes its
        # own audit row which is not counted in the return value.
        assert deleted == 0


class TestCleanupAuditRow:
    """Every cleanup run writes a `audit.retention.cleanup` row (DRF-851)."""

    def test_hard_mode_writes_audit_row_with_deleted_count(self, settings):
        settings.AUDIT_LOG_RETENTION_DAYS = 30
        settings.AUDIT_LOG_RETENTION_MODE = "hard"
        now = timezone.now()

        old1 = AuditLog.all_tenants.create(action="old.1")
        old2 = AuditLog.all_tenants.create(action="old.2")
        AuditLog.all_tenants.filter(pk__in=[old1.pk, old2.pk]).update(
            created_at=now - timedelta(days=31),
        )

        cleanup_old_audit_logs()
        row = AuditLog.all_tenants.get(action=AUDIT_CLEANUP_ACTION)
        assert row.payload["deleted"] == 2
        assert row.payload["mode"] == "hard"
        assert row.payload["retention_days"] == 30
        assert "cutoff" in row.payload
        # System action — no tenant context.
        assert row.tenant is None

    def test_soft_mode_writes_audit_row_with_mode_soft(self, settings):
        settings.AUDIT_LOG_RETENTION_DAYS = 30
        settings.AUDIT_LOG_RETENTION_MODE = "soft"
        now = timezone.now()

        old = AuditLog.all_tenants.create(action="old.soft")
        AuditLog.all_tenants.filter(pk=old.pk).update(
            created_at=now - timedelta(days=31),
        )

        cleanup_old_audit_logs()
        row = AuditLog.all_tenants.get(action=AUDIT_CLEANUP_ACTION)
        assert row.payload["mode"] == "soft"
        assert row.payload["deleted"] == 1

    def test_audit_row_subject_to_retention(self, settings):
        # The cleanup-run audit row is itself a candidate for retention.
        # Running the task twice with a fresh "ancient" cleanup row
        # between the two runs proves the slug is NOT excluded.
        settings.AUDIT_LOG_RETENTION_DAYS = 30
        settings.AUDIT_LOG_RETENTION_MODE = "hard"
        now = timezone.now()
        # Plant an "ancient" cleanup row from a previous run.
        ancient = AuditLog.all_tenants.create(action=AUDIT_CLEANUP_ACTION)
        AuditLog.all_tenants.filter(pk=ancient.pk).update(
            created_at=now - timedelta(days=31),
        )

        cleanup_old_audit_logs()
        # The ancient cleanup row was hard-deleted; only the new
        # cleanup row remains.
        rows = AuditLog.all_tenants.filter(action=AUDIT_CLEANUP_ACTION)
        assert rows.count() == 1
        assert rows.first().pk != ancient.pk


class TestSoftDelete:
    """Soft-delete mode + manager filtering (DRF-851 / PI1)."""

    def test_soft_mode_archives_instead_of_deleting(self, settings):
        settings.AUDIT_LOG_RETENTION_DAYS = 30
        settings.AUDIT_LOG_RETENTION_MODE = "soft"
        now = timezone.now()

        old = AuditLog.all_tenants.create(action="old.archive_me")
        recent = AuditLog.all_tenants.create(action="recent.keep_me")
        AuditLog.all_tenants.filter(pk=old.pk).update(
            created_at=now - timedelta(days=31),
        )

        affected = cleanup_old_audit_logs()
        assert affected == 1

        # Row still exists, but archived.
        archived_row = AuditLog.all_tenants.get(pk=old.pk)
        assert archived_row.is_archived is True
        assert archived_row.archived_at is not None
        assert archived_row.archived_at >= now

        # Recent row untouched.
        recent_row = AuditLog.all_tenants.get(pk=recent.pk)
        assert recent_row.is_archived is False
        assert recent_row.archived_at is None

    def test_soft_mode_is_idempotent(self, settings):
        # Running soft cleanup twice — second run flips 0 additional rows.
        settings.AUDIT_LOG_RETENTION_DAYS = 30
        settings.AUDIT_LOG_RETENTION_MODE = "soft"
        now = timezone.now()
        old = AuditLog.all_tenants.create(action="old.idempotent")
        AuditLog.all_tenants.filter(pk=old.pk).update(
            created_at=now - timedelta(days=31),
        )

        first = cleanup_old_audit_logs()
        assert first == 1

        second = cleanup_old_audit_logs()
        assert second == 0

    def test_hard_mode_is_idempotent(self, settings):
        # Running hard cleanup twice — second run deletes 0 user rows
        # (the row was already gone the first time).
        settings.AUDIT_LOG_RETENTION_DAYS = 30
        settings.AUDIT_LOG_RETENTION_MODE = "hard"
        now = timezone.now()
        old = AuditLog.all_tenants.create(action="old.hard_idempotent")
        AuditLog.all_tenants.filter(pk=old.pk).update(
            created_at=now - timedelta(days=31),
        )

        first = cleanup_old_audit_logs()
        assert first == 1

        # Wipe the first cleanup-run audit row so the second run can't
        # count its predecessor as a deletion.
        AuditLog.all_tenants.filter(action=AUDIT_CLEANUP_ACTION).delete()
        second = cleanup_old_audit_logs()
        assert second == 0

    def test_default_manager_hides_archived(self, settings):
        # Regression: archived rows must NOT appear via objects on
        # non-cleanup read paths.
        t = Tenant.objects.create(slug="archive-t", name="ArchiveT")
        # One live, one archived for the same tenant.
        live = AuditLog.all_tenants.create(tenant=t, action="live.event")
        archived = AuditLog.all_tenants.create(
            tenant=t,
            action="archived.event",
            is_archived=True,
            archived_at=timezone.now(),
        )

        with tenant_scope(t):
            visible = list(AuditLog.objects.values_list("action", flat=True))
        assert "live.event" in visible
        assert "archived.event" not in visible

        # all_tenants escape hatch sees both.
        all_actions = set(
            AuditLog.all_tenants.filter(pk__in=[live.pk, archived.pk]).values_list(
                "action", flat=True
            )
        )
        assert all_actions == {"live.event", "archived.event"}

    def test_archived_manager_returns_only_archived(self):
        t = Tenant.objects.create(slug="archive-t2", name="ArchiveT2")
        AuditLog.all_tenants.create(tenant=t, action="live.x")
        AuditLog.all_tenants.create(
            tenant=t,
            action="archived.x",
            is_archived=True,
            archived_at=timezone.now(),
        )

        with tenant_scope(t):
            archived_actions = list(AuditLog.archived.values_list("action", flat=True))
        assert archived_actions == ["archived.x"]

    def test_invalid_mode_falls_back_to_hard(self, settings, caplog):
        settings.AUDIT_LOG_RETENTION_DAYS = 30
        settings.AUDIT_LOG_RETENTION_MODE = "bogus"
        now = timezone.now()
        old = AuditLog.all_tenants.create(action="old.bogus_mode")
        AuditLog.all_tenants.filter(pk=old.pk).update(
            created_at=now - timedelta(days=31),
        )

        with caplog.at_level("WARNING", logger="apps.audit.tasks"):
            affected = cleanup_old_audit_logs()

        # Fell back to hard delete.
        assert affected == 1
        assert not AuditLog.all_tenants.filter(pk=old.pk).exists()
        # Audit row records the fallback mode as "hard".
        row = AuditLog.all_tenants.get(action=AUDIT_CLEANUP_ACTION)
        assert row.payload["mode"] == "hard"
