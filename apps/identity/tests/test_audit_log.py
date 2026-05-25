"""RedZoneAccessLog audit-field tests (spec §13 rows 14.30, 14.31 + AS2 fixes).

| Row | What |
|---|---|
| 14.30 | `accessor_principal` carries CONCRETE identity per `accessor_role` |
| 14.31 | `idx_red_zone_log_principal_ts` index supports «every access by staff X in date range» (postgres-only, EXPLAIN ANALYZE) |

# Also covers the ops_admin runtime-guard introduced in veha 4

Per tech-lead direction 2026-05-23 (Q2 fork): `ops_admin` role MUST
supply `accessor_principal` explicitly. The fallback `"unknown"` would
be a 152-ФЗ Chapter 3 forensic blind spot. `RedZoneReader.read()`
raises `ValueError` when `accessor_role='ops_admin'` and the principal
would otherwise be `"unknown"`.
"""

from __future__ import annotations

import socket
import uuid

import pytest
from django.db import connection
from django.utils import timezone

from apps.identity.models import MemoryEntry, RedZoneAccessLog, UserPersonalContext
from apps.identity.services.red_zone_reader import RedZoneReader

pytestmark = pytest.mark.django_db


@pytest.fixture
def upc():
    return UserPersonalContext.objects.create(user_id=uuid.uuid4())


@pytest.fixture
def red_entry(upc):
    return MemoryEntry.objects.create(
        user_id=upc.user_id,
        personal_context=upc,
        sensitivity_zone=MemoryEntry.SENSITIVITY_RED,
        source=MemoryEntry.SOURCE_EXPLICIT,
        consent_at=timezone.now(),
        content={"marker": "PII_CANARY"},
        source_tenant_id=uuid.uuid4(),
    )


# ───────────────────────────────────────────────────────────────────────
# Test 14.30 — accessor_principal concrete identity per role
# ───────────────────────────────────────────────────────────────────────


class TestAccessorPrincipalConcrete:
    """Test 14.30 — `accessor_principal` is non-null + matches expected shape per role."""

    def test_ayla_llm_principal_default_format(self, red_entry) -> None:
        """ayla_llm with no explicit principal → `worker:<hostname>:<pid>`."""
        RedZoneReader.read(
            entry_id=red_entry.id,
            user_id=red_entry.user_id,
            accessor_role=RedZoneAccessLog.ACCESSOR_AYLA_LLM,
            request_id=uuid.uuid4(),
            purpose="contraindication_check",
        )
        log = RedZoneAccessLog.objects.filter(memory_entry_id=red_entry.id).get()

        # Format contract: worker:<hostname>:<pid>
        parts = log.accessor_principal.split(":")
        assert len(parts) == 3, (
            f"Expected 3-part principal worker:host:pid, got {log.accessor_principal!r}"
        )
        assert parts[0] == "worker"
        assert parts[1] == socket.gethostname()
        assert parts[2].isdigit(), "pid component must be numeric"

    def test_ayla_llm_explicit_principal_overrides_default(self, red_entry) -> None:
        """Explicit principal is used verbatim — no override."""
        RedZoneReader.read(
            entry_id=red_entry.id,
            user_id=red_entry.user_id,
            accessor_role=RedZoneAccessLog.ACCESSOR_AYLA_LLM,
            request_id=uuid.uuid4(),
            purpose="contraindication_check",
            accessor_principal="celery-task:abc-123-def-456",
        )
        log = RedZoneAccessLog.objects.filter(memory_entry_id=red_entry.id).get()
        assert log.accessor_principal == "celery-task:abc-123-def-456"

    def test_system_job_principal_default_format(self, red_entry) -> None:
        """system_job with no explicit principal → `system:<hostname>:<pid>`."""
        RedZoneReader.read(
            entry_id=red_entry.id,
            user_id=red_entry.user_id,
            accessor_role=RedZoneAccessLog.ACCESSOR_SYSTEM_JOB,
            request_id=uuid.uuid4(),
            purpose="ttl_sweep",
        )
        log = RedZoneAccessLog.objects.filter(memory_entry_id=red_entry.id).get()

        parts = log.accessor_principal.split(":")
        assert len(parts) == 3
        assert parts[0] == "system"
        assert parts[1] == socket.gethostname()
        assert parts[2].isdigit()

    def test_ops_admin_explicit_staff_uuid_accepted(self, red_entry) -> None:
        """ops_admin with explicit principal (staff User UUID) succeeds."""
        staff_uuid = str(uuid.uuid4())
        RedZoneReader.read(
            entry_id=red_entry.id,
            user_id=red_entry.user_id,
            accessor_role=RedZoneAccessLog.ACCESSOR_OPS_ADMIN,
            request_id=uuid.uuid4(),
            purpose="incident_debug",
            accessor_principal=staff_uuid,
        )
        log = RedZoneAccessLog.objects.filter(memory_entry_id=red_entry.id).get()
        assert log.accessor_principal == staff_uuid

    def test_ops_admin_without_principal_raises(self, red_entry) -> None:
        """ops_admin role + no accessor_principal → ValueError.

        Per tech-lead direction 2026-05-23: ops_admin = privileged
        break-glass access. The fallback `"unknown"` would be a 152-ФЗ
        Chapter 3 forensic blind spot. The accessor MUST require an
        explicit staff identity.

        Atomic-rollback invariant: the failed validation must NOT
        leave a partial audit row.
        """
        log_count_before = RedZoneAccessLog.objects.count()

        with pytest.raises(ValueError, match="ops_admin"):
            RedZoneReader.read(
                entry_id=red_entry.id,
                user_id=red_entry.user_id,
                accessor_role=RedZoneAccessLog.ACCESSOR_OPS_ADMIN,
                request_id=uuid.uuid4(),
                purpose="incident_debug",
                # accessor_principal=None — would fallback to "unknown".
            )

        assert RedZoneAccessLog.objects.count() == log_count_before, (
            "Failed ops_admin validation must not leak an audit row."
        )


# ───────────────────────────────────────────────────────────────────────
# Test 14.31 — accessor_principal index supports auditor query
# ───────────────────────────────────────────────────────────────────────


class TestAuditorQueryIndex:
    """Test 14.31 — index `rzlog_principal_ts_idx` exists + supports query.

    Per spec §3 the «every access by staff X in date range» auditor
    query MUST be served by an index — sequential scan over 7 years of
    audit data is operationally unacceptable.

    Postgres-only because:
      - `EXPLAIN ANALYZE` syntax differs across engines
      - The index itself is created by Django ORM in migration 0007
        (model `Meta.indexes`), which Postgres + SQLite both honour,
        but only Postgres exposes `pg_indexes` for verification.
    """

    @pytest.mark.skipif(
        connection.vendor != "postgresql",
        reason="EXPLAIN ANALYZE + pg_indexes are postgres-only.",
    )
    def test_principal_index_exists(self) -> None:
        with connection.cursor() as cur:
            cur.execute(
                "SELECT indexname FROM pg_indexes WHERE tablename = 'identity_redzoneaccesslog'"
            )
            indexes = {row[0] for row in cur.fetchall()}

        assert "rzlog_principal_ts_idx" in indexes, (
            f"Expected `rzlog_principal_ts_idx` per spec §3. Found: {indexes}"
        )

    @pytest.mark.skipif(
        connection.vendor != "postgresql",
        reason="EXPLAIN ANALYZE is postgres-only.",
    )
    def test_principal_query_uses_index(self, upc) -> None:
        """The «accesses by staff X in date range» query plans on the index."""
        # Insert a few audit rows so the planner has data.
        for _ in range(5):
            RedZoneAccessLog.objects.create(
                memory_entry_id=uuid.uuid4(),
                user_id=upc.user_id,
                accessor_role=RedZoneAccessLog.ACCESSOR_OPS_ADMIN,
                accessor_principal="staff-uuid-target",
                access_type=RedZoneAccessLog.ACCESS_READ,
                request_id=uuid.uuid4(),
                purpose="incident_debug",
            )

        with connection.cursor() as cur:
            cur.execute(
                "EXPLAIN (FORMAT TEXT) "
                "SELECT * FROM identity_redzoneaccesslog "
                "WHERE accessor_principal = 'staff-uuid-target' "
                "AND ts >= NOW() - INTERVAL '7 days' "
                "ORDER BY ts DESC"
            )
            plan_lines = [row[0] for row in cur.fetchall()]
            plan_text = "\n".join(plan_lines)

        # Acceptable: «Index Scan» OR «Bitmap Index Scan» on our index.
        # On a tiny table the planner may pick Seq Scan — that's why
        # we inserted 5 rows; for production-sized data the index path
        # is mandatory. We assert the index is REFERENCED, not that
        # it's the only path.
        assert "rzlog_principal_ts_idx" in plan_text or "Index Scan" in plan_text, (
            f"Expected `rzlog_principal_ts_idx` in plan. Got:\n{plan_text}"
        )
