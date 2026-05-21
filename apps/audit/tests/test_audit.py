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


# ---------------------------------------------------------------------------
# Audit retro hotfix coverage
# ---------------------------------------------------------------------------


class TestRetroB1UuidCoercion:
    """Pre-fix ``write_audit(target_id="123456")`` raised ValidationError
    inside .create() because target_id is UUIDField; the bare
    ``except Exception`` swallowed it and the audit row was SILENTLY
    LOST — forensic data corruption masked as a clean write.

    Post-fix non-UUID values flow into ``payload['target_id_raw']`` /
    ``payload['actor_id_raw']`` so the row still lands AND the
    contextual id is greppable via admin / SQL search.
    """

    def test_non_uuid_target_id_lands_in_payload_raw(self):
        from apps.audit.services import write_audit

        write_audit(
            action="retro.b1.non_uuid",
            target="YClientsRecord",
            target_id="123456",  # integer YClients ID — not a UUID
        )

        row = AuditLog.all_tenants.filter(action="retro.b1.non_uuid").first()
        assert row is not None, "audit row was silently lost — B1 contract violated"
        assert row.target_id is None  # UUID column stayed clean
        assert row.payload["target_id_raw"] == "123456"

    def test_non_uuid_actor_id_lands_in_payload_raw(self):
        from apps.audit.services import write_audit

        write_audit(
            action="retro.b1.non_uuid_actor",
            actor_id="ai_persona_v2",
        )

        row = AuditLog.all_tenants.filter(action="retro.b1.non_uuid_actor").first()
        assert row is not None
        assert row.actor_id is None
        assert row.payload["actor_id_raw"] == "ai_persona_v2"

    def test_valid_uuid_target_id_passes_through(self):
        # Negative regression: a real UUID still lands in the column.
        import uuid

        from apps.audit.services import write_audit

        u = uuid.uuid4()
        write_audit(
            action="retro.b1.valid_uuid",
            target_id=u,
        )
        row = AuditLog.all_tenants.filter(action="retro.b1.valid_uuid").first()
        assert row is not None
        assert str(row.target_id) == str(u)
        assert "target_id_raw" not in (row.payload or {})

    def test_uuid_shaped_string_is_parsed(self):
        # A UUID-shaped string still parses to the UUID column, not
        # the raw fallback.
        import uuid

        from apps.audit.services import write_audit

        u = uuid.uuid4()
        write_audit(
            action="retro.b1.uuid_string",
            target_id=str(u),
        )
        row = AuditLog.all_tenants.filter(action="retro.b1.uuid_string").first()
        assert row is not None
        assert str(row.target_id) == str(u)
        assert "target_id_raw" not in (row.payload or {})


class TestRetroY1PayloadSizeCap:
    """Pre-fix a buggy caller passing a 50KB embedded blob (LLM prompt,
    base64 image) landed a multi-MB row, bloating WAL and slowing
    every audit read. Post-fix payloads over
    :data:`AUDIT_PAYLOAD_MAX_BYTES` are replaced with a
    ``{truncated, size_bytes, cap_bytes, sha256}`` summary so the row
    still lands but stays small.
    """

    def test_oversize_payload_replaced_with_summary(self):
        from apps.audit.services import AUDIT_PAYLOAD_MAX_BYTES, write_audit

        # 16 KB of repeated 'a' guarantees we exceed the 8 KB cap.
        huge_blob = "a" * (AUDIT_PAYLOAD_MAX_BYTES * 2)
        write_audit(
            action="retro.y1.oversize",
            payload={"embedded": huge_blob},
        )
        row = AuditLog.all_tenants.filter(action="retro.y1.oversize").first()
        assert row is not None
        assert row.payload.get("truncated") is True
        assert row.payload.get("size_bytes") > AUDIT_PAYLOAD_MAX_BYTES
        assert "sha256" in row.payload
        # Original huge string is NOT in the persisted payload.
        assert "embedded" not in row.payload

    def test_in_budget_payload_passes_through(self):
        # Negative regression: a small payload lands intact.
        from apps.audit.services import write_audit

        write_audit(
            action="retro.y1.small",
            payload={"k": "v", "count": 3},
        )
        row = AuditLog.all_tenants.filter(action="retro.y1.small").first()
        assert row is not None
        assert row.payload.get("k") == "v"
        assert row.payload.get("count") == 3
        assert "truncated" not in row.payload

    def test_non_json_serialisable_payload_does_not_raise(self):
        # Contract: write_audit MUST NEVER RAISE — even when the caller
        # passes a payload Django's JSONField encoder can't serialise.
        # The row lands with a degraded summary; the surrounding
        # request transaction continues. Closes reviewer Y2 — pre-fix
        # this test only asserted "no exception" which would also have
        # passed if write_audit silently swallowed everything (the
        # exact pre-B1 failure class).
        from apps.audit.services import write_audit

        class _Weird:
            pass

        try:
            write_audit(
                action="retro.y1.weird",
                payload={"obj": _Weird()},
            )
            raised = None
        except Exception as exc:  # noqa: BLE001
            raised = exc
        assert raised is None, (
            f"write_audit raised {type(raised).__name__}: {raised} — "
            "contract violation (audit must never break the request)"
        )

        # The row landed with the un-serialisable value cast to its
        # str() repr via _cap_payload_size's default=str + round-trip.
        # Verifies the forensic-breadcrumb contract: even on caller
        # bugs, write_audit produces a row admins can grep, not silent
        # data loss (closes reviewer Y2).
        row = AuditLog.all_tenants.filter(action="retro.y1.weird").first()
        assert row is not None, (
            "expected an audit row even on un-serialisable payload — "
            "possible regression to the silent-swallow failure mode"
        )
        # _Weird → '<...test._Weird object at 0x...>' via default=str.
        obj_repr = (row.payload or {}).get("obj", "")
        assert "_Weird" in obj_repr, (
            f"expected str-cast repr of _Weird in payload, got {obj_repr!r}"
        )


class TestRetroY4ChunkedSweep:
    """Pre-fix the sweep issued a single UPDATE / DELETE over potentially
    millions of rows — long row locks, replication lag, statement
    timeout risk. Post-fix processes in chunks of 5000 rows with
    per-chunk transactions.
    """

    def test_chunked_soft_archive_processes_all_rows(self, settings):
        settings.AUDIT_LOG_RETENTION_DAYS = 30
        settings.AUDIT_LOG_RETENTION_MODE = "soft"
        now = timezone.now()
        # Create 20 old rows (well within a chunk but exercises the loop).
        for i in range(20):
            row = AuditLog.all_tenants.create(action=f"chunked.{i}")
            AuditLog.all_tenants.filter(pk=row.pk).update(created_at=now - timedelta(days=40))

        affected = cleanup_old_audit_logs()
        assert affected == 20

        archived = AuditLog.all_tenants.filter(
            action__startswith="chunked.",
            is_archived=True,
        )
        assert archived.count() == 20

    def test_chunked_soft_archive_is_idempotent(self, settings):
        # Re-running the sweep on already-archived rows is a no-op.
        settings.AUDIT_LOG_RETENTION_DAYS = 30
        settings.AUDIT_LOG_RETENTION_MODE = "soft"
        now = timezone.now()
        row = AuditLog.all_tenants.create(action="idempotent.soft")
        AuditLog.all_tenants.filter(pk=row.pk).update(
            created_at=now - timedelta(days=40),
            is_archived=True,
            archived_at=now,
        )

        affected = cleanup_old_audit_logs()
        assert affected == 0  # already archived; nothing to do
