"""152-ФЗ green memory deletion tests (M-B4 / #1113)."""

from __future__ import annotations

import uuid

import pytest

from apps.audit.models import AuditLog
from apps.identity.models import MemoryEntry, UserPersonalContext
from apps.identity.services.memory_deleter import (
    request_forget_all,
    soft_delete_green_entries,
)

pytestmark = pytest.mark.django_db(transaction=True)


def _upc():
    return UserPersonalContext.objects.create(user_id=uuid.uuid4())


def _green(upc, **overrides):
    kwargs = dict(
        user_id=upc.user_id,
        personal_context=upc,
        sensitivity_zone=MemoryEntry.SENSITIVITY_GREEN,
        source=MemoryEntry.SOURCE_EXPLICIT,
        provenance=MemoryEntry.PROVENANCE_USER_STATED,  # CHECK 5 (DRF-1263)
        kind="lifestyle",
        content={"key": "diet", "value": "vegan"},
    )
    kwargs.update(overrides)
    return MemoryEntry.objects.create(**kwargs)


class TestSoftDeleteGreenEntries:
    def test_soft_deletes_and_stamps_all_three_fields(self):
        upc = _upc()
        e = _green(upc)
        n = soft_delete_green_entries(upc.user_id, [e.id])
        assert n == 1
        e.refresh_from_db()
        assert e.soft_deleted_at is not None
        assert e.delete_requested_at is not None
        assert e.deletion_reason == MemoryEntry.DELETION_REASON_USER_DELETE

    def test_audits_deletion(self):
        upc = _upc()
        e = _green(upc)
        soft_delete_green_entries(upc.user_id, [e.id])
        assert AuditLog.all_tenants.filter(action="memory.forget_entry").exists()

    def test_ignores_other_users_entry(self):
        upc_a, upc_b = _upc(), _upc()
        e_b = _green(upc_b)
        n = soft_delete_green_entries(upc_a.user_id, [e_b.id])
        assert n == 0
        e_b.refresh_from_db()
        assert e_b.soft_deleted_at is None

    def test_ignores_non_green_entry(self):
        from django.utils import timezone

        upc = _upc()
        yellow = _green(
            upc,
            sensitivity_zone=MemoryEntry.SENSITIVITY_YELLOW,
            consent_at=timezone.now(),
            content={"key": "x"},
        )
        n = soft_delete_green_entries(upc.user_id, [yellow.id])
        assert n == 0  # green-only path never touches yellow/red

    def test_empty_ids_is_noop(self):
        upc = _upc()
        assert soft_delete_green_entries(upc.user_id, []) == 0

    def test_idempotent_on_already_deleted(self):
        upc = _upc()
        e = _green(upc)
        assert soft_delete_green_entries(upc.user_id, [e.id]) == 1
        assert soft_delete_green_entries(upc.user_id, [e.id]) == 0


class TestRequestForgetAll:
    def test_sets_forget_all_and_audits(self):
        upc = _upc()
        assert request_forget_all(upc.user_id) is True
        upc.refresh_from_db()
        assert upc.forget_all_requested_at is not None
        assert AuditLog.all_tenants.filter(action="memory.forget_all_requested").exists()

    def test_idempotent_second_call_returns_false(self):
        upc = _upc()
        assert request_forget_all(upc.user_id) is True
        assert request_forget_all(upc.user_id) is False

    def test_creates_upc_when_absent(self):
        uid = uuid.uuid4()
        assert request_forget_all(uid) is True
        assert UserPersonalContext.objects.get(user_id=uid).forget_all_requested_at is not None


class TestDeletionStampsStatus:
    """DRF-1263 — a soft-deleted entry must not stay ``status='active'``.

    Migration 0016 backfilled `status`; the deleter never wrote it, so every
    deletion after 0016 minted a row in a state the contract forbids
    (`status='active' AND soft_deleted_at IS NOT NULL`). The cost surfaces the
    moment any read path filters by `status` — Migration Plan step 5.
    """

    def test_deleted_entry_is_not_returned_by_a_status_active_filter(self):
        """THE regression: the row must disappear from a status-based read.

        This is the query Step 5 will ship. Written against the read, not
        against the field, because «status is set» is not the promise —
        «the человек asked to forget it and it stays forgotten» is.
        """
        upc = _upc()
        e = _green(upc, status=MemoryEntry.STATUS_ACTIVE)

        soft_delete_green_entries(upc.user_id, [e.id])

        live = MemoryEntry.objects.filter(user_id=upc.user_id, status=MemoryEntry.STATUS_ACTIVE)
        assert list(live) == [], (
            "A soft-deleted entry is still returned by a status='active' "
            "filter — the deleted fact comes back into the выдача."
        )

    def test_soft_delete_sets_status_deleted(self):
        upc = _upc()
        e = _green(upc, status=MemoryEntry.STATUS_ACTIVE)
        soft_delete_green_entries(upc.user_id, [e.id])
        e.refresh_from_db()
        assert e.status == MemoryEntry.STATUS_DELETED

    def test_status_is_stamped_in_the_same_update_as_the_timestamps(self):
        """No window in which soft_deleted_at is set and status is not."""
        upc = _upc()
        e = _green(upc, status=MemoryEntry.STATUS_ACTIVE)
        soft_delete_green_entries(upc.user_id, [e.id])
        e.refresh_from_db()
        assert (e.status == MemoryEntry.STATUS_DELETED) == (e.soft_deleted_at is not None)

    def test_unstamped_legacy_row_also_gets_status(self):
        """A row written before the step-3.5 stamp (status NULL) still lands
        in a contract-legal state after deletion."""
        upc = _upc()
        e = _green(upc, status=None)
        soft_delete_green_entries(upc.user_id, [e.id])
        e.refresh_from_db()
        assert e.status == MemoryEntry.STATUS_DELETED
