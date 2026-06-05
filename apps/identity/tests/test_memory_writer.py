"""Memory writer tests (#229 + spec §13 rows 14.8, 14.19, 14.23).

Per spec `docs/specs/memory-entry-schema.md` §11 + ADR-0011 §10.2 + §11.2.

# Tests in this file

| Row | What |
|---|---|
| 14.8 | Writer enforces minor protections + fail-CLOSED for yellow/red + green write succeeds without DOB lookup + minor_lock pre-empts |
| 14.19 | Zone promotion green→yellow/red without consent_token → ZonePromotionRequiresConsent |
| 14.23 | Fail-closed write path writes one `write_rejected_dob_lookup` audit row |

# Phase 0 reality

`_check_minor_protection` is a stub that ALWAYS raises
`MinorProtectionLookupFailed` until #597 lands the real Ayla REST DOB
lookup. All yellow/red writes therefore fail-closed and produce a
write_rejected_dob_lookup audit row. Tests verify this is the
observed behaviour.
"""

from __future__ import annotations

import uuid

import pytest
from django.utils import timezone

from apps.identity.models import MemoryEntry, RedZoneAccessLog, UserPersonalContext
from apps.identity.services.exceptions import ZonePromotionRequiresConsent
from apps.identity.services.memory_writer import (
    promote_zone,
    write_entry,
)

pytestmark = pytest.mark.django_db


@pytest.fixture
def upc():
    return UserPersonalContext.objects.create(user_id=uuid.uuid4())


# ───────────────────────────────────────────────────────────────────────
# Test 14.8 — writer enforces minor protections + fail-closed
# ───────────────────────────────────────────────────────────────────────


class TestMinorProtectionFailClosed:
    """Yellow/red writes fail-closed; green writes proceed normally.

    Per ADR-0011 §10.2 — when Ayla REST is unreachable OR returns
    «unknown», the write MUST be silently dropped and a
    write_rejected_dob_lookup audit row appended.
    """

    def test_yellow_write_fails_closed(self, upc) -> None:
        result = write_entry(
            user_id=upc.user_id,
            personal_context=upc,
            sensitivity_zone=MemoryEntry.SENSITIVITY_YELLOW,
            source=MemoryEntry.SOURCE_EXPLICIT,
            kind="lifestyle",
            content={"family": "married"},
            request_id=uuid.uuid4(),
            purpose="write_lifestyle",
            consent_at=timezone.now(),
        )
        assert result is None, "Yellow write must fail-closed without DOB"
        assert not MemoryEntry.objects.filter(user_id=upc.user_id).exists()

    def test_red_write_fails_closed(self, upc) -> None:
        result = write_entry(
            user_id=upc.user_id,
            personal_context=upc,
            sensitivity_zone=MemoryEntry.SENSITIVITY_RED,
            source=MemoryEntry.SOURCE_EXPLICIT,
            kind="contraindication",
            content={"condition": "diabetes"},
            request_id=uuid.uuid4(),
            purpose="write_contraindication",
            consent_at=timezone.now(),
        )
        assert result is None
        assert not MemoryEntry.objects.filter(user_id=upc.user_id).exists()

    def test_green_write_succeeds_no_dob_lookup(self, upc) -> None:
        """Green writes bypass minor-protection check entirely."""
        entry = write_entry(
            user_id=upc.user_id,
            personal_context=upc,
            sensitivity_zone=MemoryEntry.SENSITIVITY_GREEN,
            source=MemoryEntry.SOURCE_EXPLICIT,
            kind="preference",
            content={"likes": "manicure"},
            request_id=uuid.uuid4(),
            purpose="write_preference",
        )
        assert entry is not None
        assert entry.sensitivity_zone == MemoryEntry.SENSITIVITY_GREEN
        assert entry.user_id == upc.user_id

    def test_minor_lock_pre_empts_dob_lookup(self, upc) -> None:
        """UPC.minor_lock=True short-circuits before DOB lookup.

        Once #597 lands the real DOB lookup, minor_lock will be the
        post-fact recovery path (reconciliation found a minor → lock
        future writes). The behaviour from caller's perspective is the
        same: write_rejected_dob_lookup audit row + return None.
        """
        upc.minor_lock = True
        upc.save(update_fields=["minor_lock"])

        result = write_entry(
            user_id=upc.user_id,
            personal_context=upc,
            sensitivity_zone=MemoryEntry.SENSITIVITY_RED,
            source=MemoryEntry.SOURCE_EXPLICIT,
            kind="contraindication",
            content={"foo": "bar"},
            request_id=uuid.uuid4(),
            purpose="x",
            consent_at=timezone.now(),
        )
        assert result is None

    def test_minor_lock_does_not_block_green(self, upc) -> None:
        """Even with minor_lock, green writes still succeed (no PII)."""
        upc.minor_lock = True
        upc.save(update_fields=["minor_lock"])

        entry = write_entry(
            user_id=upc.user_id,
            personal_context=upc,
            sensitivity_zone=MemoryEntry.SENSITIVITY_GREEN,
            source=MemoryEntry.SOURCE_EXPLICIT,
            kind="preference",
            content={"likes": "manicure"},
            request_id=uuid.uuid4(),
            purpose="write_preference",
        )
        assert entry is not None
        assert entry.sensitivity_zone == MemoryEntry.SENSITIVITY_GREEN


# ───────────────────────────────────────────────────────────────────────
# Test 14.23 — fail-closed path writes audit row
# ───────────────────────────────────────────────────────────────────────


class TestFailClosedAuditTrail:
    """Test 14.23 — write_rejected_dob_lookup audit row written on rejection."""

    def test_dob_lookup_failed_audit_for_red(self, upc) -> None:
        request_id = uuid.uuid4()

        write_entry(
            user_id=upc.user_id,
            personal_context=upc,
            sensitivity_zone=MemoryEntry.SENSITIVITY_RED,
            source=MemoryEntry.SOURCE_EXPLICIT,
            kind="symptom",
            content={"data": "x"},
            request_id=request_id,
            purpose="symptom_capture",
            consent_at=timezone.now(),
        )

        logs = RedZoneAccessLog.objects.filter(request_id=request_id)
        assert logs.count() == 1, "Exactly one fail-closed audit row expected"
        log = logs.get()
        assert log.access_type == RedZoneAccessLog.ACCESS_WRITE_REJECTED_DOB
        assert log.user_id == upc.user_id
        assert log.purpose == "symptom_capture"
        assert log.accessor_role == RedZoneAccessLog.ACCESSOR_SYSTEM_JOB
        assert log.accessor_principal.startswith("memory_writer:")
        assert log.accessor_principal.count(":") == 2  # writer:host:pid

    def test_dob_lookup_failed_audit_for_yellow(self, upc) -> None:
        """Yellow writes also produce the same audit row."""
        request_id = uuid.uuid4()
        write_entry(
            user_id=upc.user_id,
            personal_context=upc,
            sensitivity_zone=MemoryEntry.SENSITIVITY_YELLOW,
            source=MemoryEntry.SOURCE_EXPLICIT,
            kind="lifestyle",
            content={"data": "x"},
            request_id=request_id,
            purpose="lifestyle_capture",
            consent_at=timezone.now(),
        )
        log = RedZoneAccessLog.objects.filter(request_id=request_id).get()
        assert log.access_type == RedZoneAccessLog.ACCESS_WRITE_REJECTED_DOB

    def test_green_write_does_NOT_audit(self, upc) -> None:
        """Green writes don't touch the audit log."""
        log_count_before = RedZoneAccessLog.objects.count()
        write_entry(
            user_id=upc.user_id,
            personal_context=upc,
            sensitivity_zone=MemoryEntry.SENSITIVITY_GREEN,
            source=MemoryEntry.SOURCE_EXPLICIT,
            kind="preference",
            content={"data": "x"},
            request_id=uuid.uuid4(),
            purpose="preference",
        )
        assert RedZoneAccessLog.objects.count() == log_count_before


# ───────────────────────────────────────────────────────────────────────
# Test 14.19 — zone promotion without consent → ZonePromotionRequiresConsent
# ───────────────────────────────────────────────────────────────────────


class TestZonePromotionGuard:
    """Test 14.19 — green→yellow/red without consent_token raises."""

    def test_green_to_yellow_without_consent_raises(self, upc) -> None:
        entry = MemoryEntry.objects.create(
            user_id=upc.user_id,
            personal_context=upc,
            sensitivity_zone=MemoryEntry.SENSITIVITY_GREEN,
            source=MemoryEntry.SOURCE_EXPLICIT,
            kind="preference",
            content={"likes": "x"},
        )
        with pytest.raises(ZonePromotionRequiresConsent):
            promote_zone(entry=entry, new_zone=MemoryEntry.SENSITIVITY_YELLOW)

        entry.refresh_from_db()
        assert entry.sensitivity_zone == MemoryEntry.SENSITIVITY_GREEN, (
            "Zone must NOT have been mutated on a rejected promotion."
        )

    def test_green_to_red_without_consent_raises(self, upc) -> None:
        entry = MemoryEntry.objects.create(
            user_id=upc.user_id,
            personal_context=upc,
            sensitivity_zone=MemoryEntry.SENSITIVITY_GREEN,
            source=MemoryEntry.SOURCE_EXPLICIT,
            kind="preference",
            content={"likes": "x"},
        )
        with pytest.raises(ZonePromotionRequiresConsent):
            promote_zone(entry=entry, new_zone=MemoryEntry.SENSITIVITY_RED)

    def test_green_to_yellow_with_consent_succeeds(self, upc) -> None:
        """Token present → promotion succeeds, consent_at set in same UPDATE."""
        entry = MemoryEntry.objects.create(
            user_id=upc.user_id,
            personal_context=upc,
            sensitivity_zone=MemoryEntry.SENSITIVITY_GREEN,
            source=MemoryEntry.SOURCE_EXPLICIT,
            kind="preference",
            content={"likes": "x"},
        )
        promote_zone(
            entry=entry,
            new_zone=MemoryEntry.SENSITIVITY_YELLOW,
            consent_token="user-affirmed-2026-05-23",
        )
        entry.refresh_from_db()
        assert entry.sensitivity_zone == MemoryEntry.SENSITIVITY_YELLOW
        assert entry.consent_at is not None, (
            "Promotion must set consent_at in the same UPDATE — CHECK 2 "
            "requires yellow/red rows to have consent_at NOT NULL."
        )

    def test_yellow_to_green_no_consent_needed(self, upc) -> None:
        """Demotion is always allowed — privacy improvement."""
        entry = MemoryEntry.objects.create(
            user_id=upc.user_id,
            personal_context=upc,
            sensitivity_zone=MemoryEntry.SENSITIVITY_YELLOW,
            source=MemoryEntry.SOURCE_EXPLICIT,
            kind="preference",
            content={"likes": "x"},
            consent_at=timezone.now(),
        )
        promote_zone(entry=entry, new_zone=MemoryEntry.SENSITIVITY_GREEN)
        entry.refresh_from_db()
        assert entry.sensitivity_zone == MemoryEntry.SENSITIVITY_GREEN

    def test_red_to_yellow_no_consent_needed(self, upc) -> None:
        """Red→yellow demotion also free — moving toward less sensitivity."""
        entry = MemoryEntry.objects.create(
            user_id=upc.user_id,
            personal_context=upc,
            sensitivity_zone=MemoryEntry.SENSITIVITY_RED,
            source=MemoryEntry.SOURCE_EXPLICIT,
            kind="contraindication",
            content={"x": "y"},
            consent_at=timezone.now(),
        )
        promote_zone(entry=entry, new_zone=MemoryEntry.SENSITIVITY_YELLOW)
        entry.refresh_from_db()
        assert entry.sensitivity_zone == MemoryEntry.SENSITIVITY_YELLOW


# ───────────────────────────────────────────────────────────────────────
# Round-5 F2 regression — fail-closed audit row must survive caller rollback
# ───────────────────────────────────────────────────────────────────────


@pytest.mark.django_db(transaction=True)
class TestFailClosedAuditDurability:
    """Round-5 F2 regression — write_rejected_dob_lookup audit MUST persist
    durably via `atomic(durable=True)` (ADR-0011 §11.3).

    # Why transaction=True (Round-5 F2-A finding)

    Default `@pytest.mark.django_db` wraps each test in a TestCase
    transaction that rolls back at test end. Crucially that wrapper
    sets `connection.set_autocommit(False)` (so `in_atomic_block=True`)
    but DOES NOT create a savepoint, so `savepoint_ids=[]`. Django's
    `atomic(durable=True)` check inspects `savepoint_ids`, sees empty,
    and DOES NOT raise — meaning the audit INSERT runs inside the
    pytest wrapper txn and is rolled back at test end. Tests would
    pass for the WRONG reason: row visible inside wrapper, but not
    actually committed durably.

    `transaction=True` switches pytest-django to TransactionTestCase
    semantics: NO wrapper atomic. Tests run in autocommit mode; the
    DB is cleaned via TRUNCATE between tests. Now:
      - `atomic(durable=True)` inside `_audit_write_rejected` is truly
        the top-level atomic → commits the INSERT on exit.
      - After `write_entry` returns, the audit row IS on disk.
      - Tests that wrap our writer in `atomic()` create a real savepoint
        → `durable=True` correctly raises RuntimeError.
    """

    def test_audit_committed_by_durable_atomic(self, upc) -> None:
        """write_entry returns → audit row is committed durably (NOT just visible).

        Under transaction=True there is no wrapper txn to give false-
        positive «visible inside wrapper» readings. If this assertion
        passes, the row was genuinely committed by the durable atomic.
        """
        request_id = uuid.uuid4()

        write_entry(
            user_id=upc.user_id,
            personal_context=upc,
            sensitivity_zone=MemoryEntry.SENSITIVITY_RED,
            source=MemoryEntry.SOURCE_EXPLICIT,
            kind="symptom",
            content={"data": "x"},
            request_id=request_id,
            purpose="symptom_capture",
            consent_at=timezone.now(),
        )

        log = RedZoneAccessLog.objects.filter(request_id=request_id).first()
        assert log is not None, (
            "Audit row not committed by durable atomic — write_entry "
            "returned but the row is missing. F2 fix not effective."
        )
        assert log.access_type == RedZoneAccessLog.ACCESS_WRITE_REJECTED_DOB

    def test_audit_survives_subsequent_rollback(self, upc) -> None:
        """Round-5 F2-A headline regression — audit row survives explicit
        rollback in a separate transaction after write_entry returns.

        Proves the durable atomic actually decoupled the audit INSERT
        from any subsequent rollback. A pre-F2 implementation (bare
        ORM create() without durable atomic) under transaction=True
        would also survive this specific scenario (autocommit), but
        under mixed contexts (caller wrapping us in atomic and rolling
        back) would fail. The combination of `durable=True` + this
        regression test locks in the invariant across all contexts.
        """
        from django.db import transaction

        request_id = uuid.uuid4()

        write_entry(
            user_id=upc.user_id,
            personal_context=upc,
            sensitivity_zone=MemoryEntry.SENSITIVITY_RED,
            source=MemoryEntry.SOURCE_EXPLICIT,
            kind="symptom",
            content={"data": "rejected"},
            request_id=request_id,
            purpose="symptom_capture",
            consent_at=timezone.now(),
        )

        # Open a SEPARATE atomic, modify the audit row, then raise to
        # roll back the UPDATE. The original audit row was already
        # committed by the durable atomic before write_entry returned;
        # this rollback only affects the in-atomic UPDATE.
        try:
            with transaction.atomic():
                RedZoneAccessLog.objects.filter(request_id=request_id).update(
                    purpose="MODIFIED_WILL_ROLL_BACK"
                )
                raise RuntimeError("simulated caller-side rollback after writer")
        except RuntimeError:
            pass

        log = RedZoneAccessLog.objects.filter(request_id=request_id).first()
        assert log is not None, (
            "Audit row erased by subsequent rollback — durability invariant "
            "(ADR-0011 §11.3) violated. F2 fix not effective."
        )
        assert log.purpose == "symptom_capture", (
            "Audit row UPDATE rolled back correctly but the row itself "
            f"survived (durable INSERT). Got purpose={log.purpose!r}."
        )

    def test_durable_atomic_raises_when_caller_wraps(self, upc) -> None:
        """Outer atomic + write_entry → RuntimeError (fail-loud).

        Django's `atomic(durable=True)` raises `RuntimeError` when nested
        in another `atomic()` block that has created a savepoint. Under
        transaction=True the caller's explicit `atomic()` creates a real
        savepoint, so the durable check fires correctly.

        Contract: callers MUST NOT wrap `write_entry()` in their own
        transaction — fail-loud per Q2 fork 2026-05-25.
        """
        from django.db import transaction

        with pytest.raises(RuntimeError, match="durable atomic"):
            with transaction.atomic():
                write_entry(
                    user_id=upc.user_id,
                    personal_context=upc,
                    sensitivity_zone=MemoryEntry.SENSITIVITY_RED,
                    source=MemoryEntry.SOURCE_EXPLICIT,
                    kind="symptom",
                    content={"data": "x"},
                    request_id=uuid.uuid4(),
                    purpose="symptom_capture",
                    consent_at=timezone.now(),
                )

    def test_green_write_inside_atomic_still_works(self, upc) -> None:
        """Green writes don't trigger the audit path, so caller atomic is fine.

        Confirms F2 fix doesn't break the green-write happy path —
        only the fail-closed audit path is durable.
        """
        from django.db import transaction

        with transaction.atomic():
            entry = write_entry(
                user_id=upc.user_id,
                personal_context=upc,
                sensitivity_zone=MemoryEntry.SENSITIVITY_GREEN,
                source=MemoryEntry.SOURCE_EXPLICIT,
                kind="preference",
                content={"likes": "manicure"},
                request_id=uuid.uuid4(),
                purpose="write_preference",
            )
        assert entry is not None
        assert entry.sensitivity_zone == MemoryEntry.SENSITIVITY_GREEN
