"""Отзыв согласия Ф4 стирает производную память — и только её (DRF-2783).

Решение владельца 05.10: «при отзыве согласия Ф4 — сразу прекратить новые
выводы и их использование, удалить производную память по процедуре». Ф4
добровольна отдельно, поэтому её отзыв узкий:

* зелёные выводы (``source='inferred'``) — неподтверждённые, подтверждённые и
  замещённые исправлением — уходят в надгробие с причиной ``withdrawal``;
  физически их удалит очистка DRF-2775 через сутки;
* явные факты и сигнальные строки, жёлтая и красная зона, чужие люди — целы.

Обратное направление — отзыв хранения данных стирает и выводы — держит
``apps/identity/services/tests/test_privacy.py::TestDelete::test_full_cascade``
(сеет вывод и требует ноль живой памяти).
"""

from __future__ import annotations

import uuid
from datetime import timedelta

import pytest
from django.utils import timezone

from apps.audit.models import AuditLog
from apps.identity.models import MemoryEntry, UserPersonalContext
from apps.identity.services.memory_deleter import (
    TOMBSTONE_RETENTION,
    WITHDRAWAL_TOMBSTONE_RETENTION,
    soft_delete_inferences_for_withdrawal,
)

pytestmark = pytest.mark.django_db

CONFIRMED = "user_confirmed_inference"


def _upc() -> UserPersonalContext:
    return UserPersonalContext.objects.create(user_id=uuid.uuid4())


def _row(
    upc: UserPersonalContext,
    *,
    source: str = MemoryEntry.SOURCE_INFERRED,
    zone: str = MemoryEntry.SENSITIVITY_GREEN,
    provenance: str | None = None,
    status: str = "active",
) -> MemoryEntry:
    explicit = source == MemoryEntry.SOURCE_EXPLICIT
    entry = MemoryEntry.objects.create(
        user_id=upc.user_id,
        personal_context=upc,
        sensitivity_zone=zone,
        source=source,
        provenance=MemoryEntry.PROVENANCE_USER_STATED if explicit else None,
        last_inferred_at=None if explicit else timezone.now() - timedelta(days=2),
        consent_at=None if zone == MemoryEntry.SENSITIVITY_GREEN else timezone.now(),
        content={"key": "preferred_time_slots", "value": "evening"},
    )
    if not explicit:
        MemoryEntry.objects.filter(pk=entry.pk).update(provenance=provenance, status=status)
    entry.refresh_from_db()
    return entry


def _reload(entry: MemoryEntry) -> MemoryEntry:
    entry.refresh_from_db()
    return entry


class TestOnlyTheDerivedMemoryGoes:
    def test_every_kind_of_inference_is_tombstoned_as_a_withdrawal(self) -> None:
        upc = _upc()
        proposal = _row(upc)
        confirmed = _row(upc, provenance=CONFIRMED)
        corrected = _row(upc, status="superseded")
        expired = _row(upc, provenance=CONFIRMED, status="expired")

        count = soft_delete_inferences_for_withdrawal(upc.user_id)

        assert count == 4
        for entry in (proposal, confirmed, corrected, expired):
            row = _reload(entry)
            assert row.deletion_reason == "withdrawal"
            assert row.status == "deleted"
            assert row.soft_deleted_at is not None
            assert row.delete_requested_at == row.soft_deleted_at

    def test_stated_facts_and_signals_stay(self) -> None:
        upc = _upc()
        stated = _row(upc, source=MemoryEntry.SOURCE_EXPLICIT)
        signal = _row(upc, source=MemoryEntry.SOURCE_SIGNAL)
        inferred = _row(upc)

        soft_delete_inferences_for_withdrawal(upc.user_id)

        assert _reload(inferred).soft_deleted_at is not None
        assert _reload(stated).soft_deleted_at is None
        assert _reload(signal).soft_deleted_at is None

    def test_yellow_and_red_inferences_are_not_this_consent(self) -> None:
        upc = _upc()
        yellow = _row(upc, zone=MemoryEntry.SENSITIVITY_YELLOW)
        red = _row(upc, zone=MemoryEntry.SENSITIVITY_RED)
        green = _row(upc)

        soft_delete_inferences_for_withdrawal(upc.user_id)

        assert _reload(green).soft_deleted_at is not None
        assert _reload(yellow).soft_deleted_at is None
        assert _reload(red).soft_deleted_at is None

    def test_another_persons_inferences_stay(self) -> None:
        mine, theirs = _upc(), _upc()
        own = _row(mine)
        other = _row(theirs)

        soft_delete_inferences_for_withdrawal(mine.user_id)

        assert _reload(own).soft_deleted_at is not None
        assert _reload(other).soft_deleted_at is None


class TestTombstoneShape:
    def test_an_already_deleted_inference_keeps_its_reason(self) -> None:
        upc = _upc()
        gone = _row(upc)
        earlier = timezone.now() - timedelta(days=3)
        MemoryEntry.objects.filter(pk=gone.pk).update(
            delete_requested_at=earlier,
            soft_deleted_at=earlier,
            deletion_reason="user_request_miniapp",
            status="deleted",
        )
        live = _row(upc)

        count = soft_delete_inferences_for_withdrawal(upc.user_id)

        assert count == 1
        assert _reload(live).deletion_reason == "withdrawal"
        gone = _reload(gone)
        assert gone.deletion_reason == "user_request_miniapp"
        assert gone.soft_deleted_at == earlier

    def test_a_second_withdrawal_changes_nothing(self) -> None:
        upc = _upc()
        _row(upc)

        first = soft_delete_inferences_for_withdrawal(upc.user_id)
        second = soft_delete_inferences_for_withdrawal(upc.user_id)

        assert first == 1
        assert second == 0

    def test_the_withdrawal_reason_buys_the_short_physical_purge(self) -> None:
        """ADR-0011 §5: отзыв — «physical purge within 24h», прочие — 30 дней.
        Причина ``withdrawal`` и есть то, что даёт сутки, а не месяц."""
        assert WITHDRAWAL_TOMBSTONE_RETENTION == timedelta(hours=24)
        assert TOMBSTONE_RETENTION == timedelta(days=30)


class TestAudit:
    def test_the_withdrawal_is_audited_by_counts(self) -> None:
        upc = _upc()
        _row(upc)
        _row(upc, provenance=CONFIRMED)

        soft_delete_inferences_for_withdrawal(upc.user_id)

        row = AuditLog.all_tenants.get(action="memory.inferences_withdrawn")
        assert row.payload == {
            "user_id": str(upc.user_id),
            "count": 2,
            "reason": "withdrawal",
        }

    def test_nothing_to_withdraw_writes_no_audit_row(self) -> None:
        upc = _upc()
        stated = _row(upc, source=MemoryEntry.SOURCE_EXPLICIT)

        count = soft_delete_inferences_for_withdrawal(upc.user_id)

        assert _reload(stated).soft_deleted_at is None
        assert count == 0
        assert AuditLog.all_tenants.filter(action="memory.inferences_withdrawn").count() == 0
