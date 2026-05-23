"""RedZoneReader accessor tests (#229 + spec §13 rows 14.7, 14.16, 14.22, 14.27, 14.28, 14.29).

Per spec `docs/specs/memory-entry-schema.md` §10 + ADR-0011 §10 + §9.1.

# Tests in this file

| Row | What |
|---|---|
| 14.7 | Accessor exists with documented signature (entry_id, user_id, accessor_role, request_id, purpose, accessor_principal, expected_source_tenant_id) + happy-path audit-row creation |
| 14.16 | Direct ORM read (bypassing the accessor) does NOT create an audit row — accessor IS the audit path |
| 14.22 | Cross-tenant red SELECT → `TenantScopeViolation` BEFORE caller gets the row, atomic rollback prevents audit-row leak |
| 14.27 (AS1) | Non-existent entry → `DoesNotExist` AND no `RedZoneAccessLog` row commits (no-orphan-log invariant) |
| 14.28 (AS1) | Calling `.read()` without `user_id` arg → `TypeError` |
| 14.29 (AS1) | `entry.user_id ≠ caller user_id` → `DoesNotExist` (ownership check at query time) |

# Why these run on both engines

The accessor's atomicity + ownership + tenant-scope checks are
application-layer logic — they work on SQLite and Postgres. The RLS
filtering layer (veha 2) has its own postgres-only test file
(`test_db_security.py`). These tests deliberately don't depend on RLS.
"""

from __future__ import annotations

import inspect
import uuid

import pytest
from django.utils import timezone

from apps.identity.models import MemoryEntry, RedZoneAccessLog, UserPersonalContext
from apps.identity.services.exceptions import TenantScopeViolation
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
# Test 14.7 — accessor exists with documented signature
# ───────────────────────────────────────────────────────────────────────


class TestAccessorSignature:
    """Test 14.7 — RedZoneReader.read() exists with documented signature."""

    def test_read_classmethod_exists(self) -> None:
        assert hasattr(RedZoneReader, "read")
        assert callable(RedZoneReader.read)

    def test_signature_has_required_params(self) -> None:
        sig = inspect.signature(RedZoneReader.read)
        params = set(sig.parameters)
        # Spec §10 + round-2 AS1 (user_id, no-orphan-log) + AS2
        # (accessor_principal concrete identity).
        for name in (
            "entry_id",
            "user_id",
            "accessor_role",
            "request_id",
            "purpose",
            "accessor_principal",
            "expected_source_tenant_id",
        ):
            assert name in params, f"Signature missing required param {name!r}"


class TestHappyPathAuditsRead:
    """Happy-path read commits exactly one audit row with concrete principal."""

    def test_successful_read_writes_audit_row(self, red_entry) -> None:
        log_count_before = RedZoneAccessLog.objects.count()
        request_id = uuid.uuid4()

        entry = RedZoneReader.read(
            entry_id=red_entry.id,
            user_id=red_entry.user_id,
            accessor_role=RedZoneAccessLog.ACCESSOR_AYLA_LLM,
            request_id=request_id,
            purpose="contraindication_check",
            accessor_principal="worker:test-host:1",
        )

        assert entry.id == red_entry.id
        log = RedZoneAccessLog.objects.filter(request_id=request_id).get()
        assert log.memory_entry_id == red_entry.id
        assert log.user_id == red_entry.user_id
        assert log.access_type == RedZoneAccessLog.ACCESS_READ
        assert log.accessor_principal == "worker:test-host:1"
        assert log.purpose == "contraindication_check"
        assert RedZoneAccessLog.objects.count() == log_count_before + 1

    def test_default_principal_for_ayla_llm(self, red_entry) -> None:
        """No accessor_principal supplied → fallback to worker:host:pid."""
        RedZoneReader.read(
            entry_id=red_entry.id,
            user_id=red_entry.user_id,
            accessor_role=RedZoneAccessLog.ACCESSOR_AYLA_LLM,
            request_id=uuid.uuid4(),
            purpose="contraindication_check",
        )
        log = RedZoneAccessLog.objects.filter(memory_entry_id=red_entry.id).get()
        # Default principal format: "worker:{hostname}:{pid}"
        assert log.accessor_principal.startswith("worker:")
        assert log.accessor_principal.count(":") == 2


# ───────────────────────────────────────────────────────────────────────
# Test 14.27 (AS1) — no-orphan-log on SELECT failure
# ───────────────────────────────────────────────────────────────────────


class TestNoOrphanLogInvariant:
    """Test 14.27 — non-existent entry → DoesNotExist + audit rolled back."""

    def test_no_orphan_log_on_select_fail(self, upc) -> None:
        nonexistent_id = uuid.uuid4()
        log_count_before = RedZoneAccessLog.objects.count()

        with pytest.raises(MemoryEntry.DoesNotExist):
            RedZoneReader.read(
                entry_id=nonexistent_id,
                user_id=upc.user_id,
                accessor_role=RedZoneAccessLog.ACCESSOR_AYLA_LLM,
                request_id=uuid.uuid4(),
                purpose="contraindication_check",
            )

        # Atomic block rolled back — no audit row leaked.
        assert RedZoneAccessLog.objects.count() == log_count_before, (
            "RedZoneAccessLog row leaked on DoesNotExist — atomic rollback "
            "failed (round-2 AS1 no-orphan-log invariant violated)."
        )


# ───────────────────────────────────────────────────────────────────────
# Test 14.28 (AS1) — signature rejects calls missing user_id
# ───────────────────────────────────────────────────────────────────────


class TestSignatureRequiresUserId:
    """Test 14.28 — TypeError when user_id arg is missing.

    user_id is keyword-only (per signature) AND required (no default).
    Calling without it must fail at the call site, not silently skip
    the ownership check.
    """

    def test_missing_user_id_raises_typeerror(self) -> None:
        with pytest.raises(TypeError):
            RedZoneReader.read(  # type: ignore[call-arg]
                entry_id=uuid.uuid4(),
                accessor_role=RedZoneAccessLog.ACCESSOR_AYLA_LLM,
                request_id=uuid.uuid4(),
                purpose="contraindication_check",
            )


# ───────────────────────────────────────────────────────────────────────
# Test 14.29 (AS1) — ownership check at query time
# ───────────────────────────────────────────────────────────────────────


class TestOwnershipCheck:
    """Test 14.29 — entry.user_id ≠ caller user_id → DoesNotExist."""

    def test_ownership_mismatch_raises_doesnotexist(self, red_entry) -> None:
        other_user_id = uuid.uuid4()
        log_count_before = RedZoneAccessLog.objects.count()

        with pytest.raises(MemoryEntry.DoesNotExist):
            RedZoneReader.read(
                entry_id=red_entry.id,
                user_id=other_user_id,  # NOT the owner
                accessor_role=RedZoneAccessLog.ACCESSOR_AYLA_LLM,
                request_id=uuid.uuid4(),
                purpose="contraindication_check",
            )

        # No-orphan-log applies to ownership failures too.
        assert RedZoneAccessLog.objects.count() == log_count_before, (
            "Ownership-mismatch audit row leaked — no-orphan-log "
            "invariant doesn't cover the ownership-check path."
        )


# ───────────────────────────────────────────────────────────────────────
# Test 14.22 — cross-tenant red SELECT raises TenantScopeViolation
# ───────────────────────────────────────────────────────────────────────


class TestCrossTenantCarveOut:
    """Test 14.22 — red carve-out independent of STRICT_TENANT_REFUSE flag."""

    def test_cross_tenant_raises_scope_violation(self, red_entry) -> None:
        wrong_tenant = uuid.uuid4()
        log_count_before = RedZoneAccessLog.objects.count()

        with pytest.raises(TenantScopeViolation):
            RedZoneReader.read(
                entry_id=red_entry.id,
                user_id=red_entry.user_id,
                accessor_role=RedZoneAccessLog.ACCESSOR_AYLA_LLM,
                request_id=uuid.uuid4(),
                purpose="contraindication_check",
                expected_source_tenant_id=wrong_tenant,
            )

        # Spec §13 row 14.22 «BEFORE caller receives row»: audit row
        # must NOT be committed when the tenant check fails — atomic
        # rollback after the violation fires.
        assert RedZoneAccessLog.objects.count() == log_count_before

    def test_matching_tenant_succeeds(self, red_entry) -> None:
        entry = RedZoneReader.read(
            entry_id=red_entry.id,
            user_id=red_entry.user_id,
            accessor_role=RedZoneAccessLog.ACCESSOR_AYLA_LLM,
            request_id=uuid.uuid4(),
            purpose="contraindication_check",
            expected_source_tenant_id=red_entry.source_tenant_id,
        )
        assert entry.id == red_entry.id
        # Audit row IS committed on a matching-tenant success.
        log = RedZoneAccessLog.objects.filter(memory_entry_id=red_entry.id).first()
        assert log is not None
        assert log.access_type == RedZoneAccessLog.ACCESS_READ

    def test_none_expected_tenant_skips_check(self, red_entry) -> None:
        """expected_source_tenant_id=None → cross-tenant access allowed.

        Use case: ayla_llm building cross-tenant memory for the global
        UPC context — has no tenant pin to compare against.
        """
        entry = RedZoneReader.read(
            entry_id=red_entry.id,
            user_id=red_entry.user_id,
            accessor_role=RedZoneAccessLog.ACCESSOR_AYLA_LLM,
            request_id=uuid.uuid4(),
            purpose="cross_tenant_memory_build",
            expected_source_tenant_id=None,
        )
        assert entry.id == red_entry.id


# ───────────────────────────────────────────────────────────────────────
# Test 14.16 — direct ORM access is NOT instrumented
# ───────────────────────────────────────────────────────────────────────


class TestDirectAccessIsUnaudited:
    """Test 14.16 — direct `MemoryEntry.objects.get(id=red)` does not audit.

    Proves the architectural invariant: `RedZoneAccessLog` is written
    ONLY by `RedZoneReader.read()`, never by raw ORM access. This is
    the application-layer half of the defence-in-depth (the DB-layer
    half is RLS, tested in veha 2's `test_db_security.py`; the CI
    layer half is the AST lint, veha 4).

    The behaviour test demonstrates the delta: an audit row appears
    only when the sanctioned accessor is used.
    """

    def test_direct_access_does_not_audit(self, red_entry) -> None:
        log_count_before = RedZoneAccessLog.objects.count()

        # Direct ORM read — bypasses the accessor entirely. On SQLite
        # this returns the row (no RLS); on Postgres this returns no
        # rows because RLS hides red without a GUC. Either way, no
        # `RedZoneAccessLog` row is created — that's the invariant.
        MemoryEntry.objects.filter(id=red_entry.id).first()

        assert RedZoneAccessLog.objects.count() == log_count_before, (
            "RedZoneAccessLog row was written for a non-accessor read — "
            "the accessor is no longer the sole audit path."
        )

    def test_accessor_path_does_audit(self, red_entry) -> None:
        """Companion to the negative test: accessor DOES audit."""
        log_count_before = RedZoneAccessLog.objects.count()
        RedZoneReader.read(
            entry_id=red_entry.id,
            user_id=red_entry.user_id,
            accessor_role=RedZoneAccessLog.ACCESSOR_AYLA_LLM,
            request_id=uuid.uuid4(),
            purpose="contraindication_check",
        )
        assert RedZoneAccessLog.objects.count() == log_count_before + 1
