"""MemoryEntry DB CHECK constraint tests (spec §13 rows 14.9-14.14).

Per spec `docs/specs/memory-entry-schema.md` §4.

# Postgres-only

These tests verify 3 DB CHECK constraints on MemoryEntry:
1. `memory_entry_inferred_nullness` — source/last_inferred_at invariant
2. `memory_entry_yellow_red_requires_consent` — yellow/red consent OR tombstone
3. `memory_entry_deletion_reason_nullness` — live vs deleted state invariant

The migration adds these via NOT VALID + VALIDATE pattern (spec §12) using
RunPython that no-ops on SQLite (Postgres-only syntax). Tests are therefore
also Postgres-only — skipped on SQLite via `pytest.mark.skipif`.

Local dev (SQLite): tests skip; application-side mirror validation lives in
the memory_writer service (veha 3).

CI + production (Postgres): tests run and verify the constraints fire.
"""

from __future__ import annotations

import uuid

import pytest
from django.db import IntegrityError, connection, transaction
from django.utils import timezone

from apps.identity.models import MemoryEntry, UserPersonalContext

pytestmark = [
    pytest.mark.django_db,
    pytest.mark.skipif(
        connection.vendor != "postgresql",
        reason=(
            "CHECK constraints are added via NOT VALID + VALIDATE pattern "
            "which is Postgres-only. SQLite migration no-ops the constraint "
            "creation (spec §12). Production + CI run Postgres."
        ),
    ),
]


@pytest.fixture
def upc() -> UserPersonalContext:
    return UserPersonalContext.objects.create(user_id=uuid.uuid4())


def _now():
    return timezone.now()


class TestCheck1InferredNullness:
    """spec §4 Constraint 1 — source/last_inferred_at invariant.

    - `source='explicit'` → `last_inferred_at` MUST be NULL
    - `source IN ('inferred', 'signal')` → `last_inferred_at` MUST be NOT NULL
    """

    def test_explicit_with_last_inferred_at_set_raises(self, upc):
        """Test 14.9 — explicit + non-null last_inferred_at → CHECK 1 raises."""
        with transaction.atomic(), pytest.raises(IntegrityError):
            MemoryEntry.objects.create(
                user_id=upc.user_id,
                personal_context=upc,
                sensitivity_zone="green",
                source="explicit",
                provenance="user_stated",  # CHECK 5 (DRF-1263)
                last_inferred_at=_now(),  # forbidden for explicit
                content={"k": "v"},
            )

    def test_inferred_with_null_last_inferred_at_raises(self, upc):
        """Test 14.10 — inferred + null last_inferred_at → CHECK 1 raises."""
        with transaction.atomic(), pytest.raises(IntegrityError):
            MemoryEntry.objects.create(
                user_id=upc.user_id,
                personal_context=upc,
                sensitivity_zone="green",
                source="inferred",
                last_inferred_at=None,  # forbidden for inferred
                content={"k": "v"},
            )

    def test_signal_with_null_last_inferred_at_raises(self, upc):
        """Test 14.10 (signal variant) — signal + null last_inferred_at raises."""
        with transaction.atomic(), pytest.raises(IntegrityError):
            MemoryEntry.objects.create(
                user_id=upc.user_id,
                personal_context=upc,
                sensitivity_zone="green",
                source="signal",
                last_inferred_at=None,
                content={"k": "v"},
            )

    def test_explicit_with_null_last_inferred_at_allowed(self, upc):
        """Happy path — explicit without last_inferred_at OK."""
        entry = MemoryEntry.objects.create(
            user_id=upc.user_id,
            personal_context=upc,
            sensitivity_zone="green",
            source="explicit",
            provenance="user_stated",  # CHECK 5 (DRF-1263)
            last_inferred_at=None,
            content={"k": "v"},
        )
        assert entry.pk is not None

    def test_inferred_with_last_inferred_at_set_allowed(self, upc):
        """Happy path — inferred with timestamp OK."""
        entry = MemoryEntry.objects.create(
            user_id=upc.user_id,
            personal_context=upc,
            sensitivity_zone="green",
            source="inferred",
            last_inferred_at=_now(),
            content={"k": "v"},
        )
        assert entry.pk is not None


class TestCheck2YellowRedRequiresConsent:
    """spec §4 Constraint 2 — yellow/red MUST have consent_at OR soft-delete tombstone.

    Green entries: no consent requirement.
    Yellow/red live entries: `consent_at IS NOT NULL`.
    Yellow/red soft-deleted entries: `soft_deleted_at IS NOT NULL` exemption
    (per spec §4 withdrawal flow — `consent_at` is never cleared on a live row).
    """

    def test_yellow_without_consent_or_tombstone_raises(self, upc):
        """Test 14.11 — yellow + consent_at NULL + soft_deleted_at NULL → CHECK 2 raises."""
        with transaction.atomic(), pytest.raises(IntegrityError):
            MemoryEntry.objects.create(
                user_id=upc.user_id,
                personal_context=upc,
                sensitivity_zone="yellow",
                source="explicit",
                provenance="user_stated",  # CHECK 5 (DRF-1263)
                consent_at=None,
                soft_deleted_at=None,
                content={"k": "v"},
            )

    def test_red_without_consent_or_tombstone_raises(self, upc):
        """Same as 14.11 but red zone."""
        with transaction.atomic(), pytest.raises(IntegrityError):
            MemoryEntry.objects.create(
                user_id=upc.user_id,
                personal_context=upc,
                sensitivity_zone="red",
                source="explicit",
                provenance="user_stated",  # CHECK 5 (DRF-1263)
                consent_at=None,
                soft_deleted_at=None,
                content={"k": "v"},
            )

    def test_yellow_with_consent_allowed(self, upc):
        """Happy path — yellow with consent_at OK."""
        entry = MemoryEntry.objects.create(
            user_id=upc.user_id,
            personal_context=upc,
            sensitivity_zone="yellow",
            source="explicit",
            provenance="user_stated",  # CHECK 5 (DRF-1263)
            consent_at=_now(),
            content={"k": "v"},
        )
        assert entry.pk is not None

    def test_yellow_with_soft_delete_tombstone_allowed(self, upc):
        """Test 14.12 — yellow + consent_at NULL + soft_deleted_at NOT NULL
        → CHECK 2 allows. This is the withdrawal flow per spec §4."""
        # Withdrawal flow: soft-delete + delete_requested_at + deletion_reason
        # set in one transaction. consent_at can be NULL because the
        # tombstone exempts the row from the consent requirement.
        now = _now()
        entry = MemoryEntry.objects.create(
            user_id=upc.user_id,
            personal_context=upc,
            sensitivity_zone="yellow",
            source="explicit",
            provenance="user_stated",  # CHECK 5 (DRF-1263)
            consent_at=None,  # cleared during withdrawal
            soft_deleted_at=now,
            delete_requested_at=now,
            deletion_reason="withdrawal",
            content={"k": "v"},
        )
        assert entry.pk is not None

    def test_green_without_consent_allowed(self, upc):
        """Green entries: no consent requirement at all."""
        entry = MemoryEntry.objects.create(
            user_id=upc.user_id,
            personal_context=upc,
            sensitivity_zone="green",
            source="explicit",
            provenance="user_stated",  # CHECK 5 (DRF-1263)
            consent_at=None,
            content={"k": "v"},
        )
        assert entry.pk is not None


class TestCheck3DeletionReasonNullness:
    """spec §4 Constraint 3 — deletion_reason nullness invariant.

    Live row (delete_requested_at + soft_deleted_at + deletion_reason all NULL).
    Deleted row (deletion_reason NOT NULL AND at least one timestamp NOT NULL).
    """

    def test_live_row_with_deletion_reason_set_raises(self, upc):
        """Test 14.13 — live row + deletion_reason NOT NULL → CHECK 3 raises."""
        with transaction.atomic(), pytest.raises(IntegrityError):
            MemoryEntry.objects.create(
                user_id=upc.user_id,
                personal_context=upc,
                sensitivity_zone="green",
                source="explicit",
                provenance="user_stated",  # CHECK 5 (DRF-1263)
                # No delete timestamps set:
                delete_requested_at=None,
                soft_deleted_at=None,
                # But deletion_reason set — forbidden on a live row:
                deletion_reason="user_delete",
                content={"k": "v"},
            )

    def test_soft_deleted_row_with_null_reason_raises(self, upc):
        """Test 14.14 — soft-deleted row + deletion_reason NULL → CHECK 3 raises."""
        with transaction.atomic(), pytest.raises(IntegrityError):
            MemoryEntry.objects.create(
                user_id=upc.user_id,
                personal_context=upc,
                sensitivity_zone="green",
                source="explicit",
                provenance="user_stated",  # CHECK 5 (DRF-1263)
                soft_deleted_at=_now(),  # tombstone present
                delete_requested_at=_now(),
                deletion_reason=None,  # but reason missing — forbidden
                content={"k": "v"},
            )

    def test_live_row_with_all_delete_fields_null_allowed(self, upc):
        """Happy path — live row, all 3 delete fields NULL."""
        entry = MemoryEntry.objects.create(
            user_id=upc.user_id,
            personal_context=upc,
            sensitivity_zone="green",
            source="explicit",
            provenance="user_stated",  # CHECK 5 (DRF-1263)
            delete_requested_at=None,
            soft_deleted_at=None,
            deletion_reason=None,
            content={"k": "v"},
        )
        assert entry.pk is not None
        assert entry.deletion_reason is None

    def test_soft_deleted_with_reason_allowed(self, upc):
        """Happy path — soft-deleted row with deletion_reason."""
        now = _now()
        entry = MemoryEntry.objects.create(
            user_id=upc.user_id,
            personal_context=upc,
            sensitivity_zone="green",
            source="explicit",
            provenance="user_stated",  # CHECK 5 (DRF-1263)
            delete_requested_at=now,
            soft_deleted_at=now,
            deletion_reason="user_delete",
            content={"k": "v"},
        )
        assert entry.pk is not None


class TestCheck4DeletedStatusMatchesTombstone:
    """DRF-1263 / CHECK 4 — `status='deleted'` ⇔ `soft_deleted_at IS NOT NULL`.

    Deliberately narrowed: `status IS NULL` is exempt. `status` is nullable
    until the Step-4 writer stamps every row (inferred/signal rows are
    unstamped by design, MDC §3.1), and migration 0016 exists precisely to
    fill NULLs — a constraint forbidding NULL here would make the state that
    0016 migrates FROM unrepresentable.
    """

    def test_active_status_with_tombstone_raises(self, upc):
        """The exact row every deletion minted before DRF-1263."""
        with transaction.atomic(), pytest.raises(IntegrityError):
            MemoryEntry.objects.create(
                user_id=upc.user_id,
                personal_context=upc,
                sensitivity_zone="green",
                source="explicit",
                provenance="user_stated",
                status="active",
                soft_deleted_at=_now(),
                delete_requested_at=_now(),
                deletion_reason="user_delete",
                content={"k": "v"},
            )

    def test_deleted_status_without_tombstone_raises(self, upc):
        """The mirror lie — «deleted» with nothing actually deleted."""
        with transaction.atomic(), pytest.raises(IntegrityError):
            MemoryEntry.objects.create(
                user_id=upc.user_id,
                personal_context=upc,
                sensitivity_zone="green",
                source="explicit",
                provenance="user_stated",
                status="deleted",
                content={"k": "v"},
            )

    def test_deleted_status_with_tombstone_allowed(self, upc):
        now = _now()
        entry = MemoryEntry.objects.create(
            user_id=upc.user_id,
            personal_context=upc,
            sensitivity_zone="green",
            source="explicit",
            provenance="user_stated",
            status="deleted",
            soft_deleted_at=now,
            delete_requested_at=now,
            deletion_reason="user_delete",
            content={"k": "v"},
        )
        assert entry.pk is not None

    def test_deletion_pending_without_tombstone_allowed(self, upc):
        """Delete requested, sweep not yet run — legal interim state."""
        entry = MemoryEntry.objects.create(
            user_id=upc.user_id,
            personal_context=upc,
            sensitivity_zone="green",
            source="explicit",
            provenance="user_stated",
            status="deletion_pending",
            delete_requested_at=_now(),
            deletion_reason="user_delete",
            content={"k": "v"},
        )
        assert entry.pk is not None

    def test_null_status_is_exempt(self, upc):
        """Unstamped legacy row — pre-0016 shape must stay representable."""
        now = _now()
        entry = MemoryEntry.objects.create(
            user_id=upc.user_id,
            personal_context=upc,
            sensitivity_zone="green",
            source="inferred",
            last_inferred_at=now,
            status=None,
            soft_deleted_at=now,
            delete_requested_at=now,
            deletion_reason="user_delete",
            content={"k": "v"},
        )
        assert entry.pk is not None


class TestCheck5ExplicitRequiresProvenance:
    """DRF-1263 / CHECK 5 — `source='explicit'` → `provenance IS NOT NULL`.

    MDC §3.1 allows exactly two provenance values. Today the invariant lives
    in ONE `if` in memory_writer, so any other writer (admin, shell, a future
    importer) can mint `source='explicit', provenance=NULL` — making NULL a
    de-facto third value the contract does not know.

    inferred/signal rows keep `provenance=NULL` on purpose: promotion to
    `user_confirmed_inference` requires the proposal flow (OR-MEM-3).
    """

    def test_explicit_without_provenance_raises(self, upc):
        with transaction.atomic(), pytest.raises(IntegrityError):
            MemoryEntry.objects.create(
                user_id=upc.user_id,
                personal_context=upc,
                sensitivity_zone="green",
                source="explicit",
                provenance=None,
                content={"k": "v"},
            )

    def test_explicit_with_provenance_allowed(self, upc):
        entry = MemoryEntry.objects.create(
            user_id=upc.user_id,
            personal_context=upc,
            sensitivity_zone="green",
            source="explicit",
            provenance="user_stated",
            content={"k": "v"},
        )
        assert entry.pk is not None

    def test_inferred_without_provenance_allowed(self, upc):
        entry = MemoryEntry.objects.create(
            user_id=upc.user_id,
            personal_context=upc,
            sensitivity_zone="green",
            source="inferred",
            last_inferred_at=_now(),
            provenance=None,
            content={"k": "v"},
        )
        assert entry.pk is not None
