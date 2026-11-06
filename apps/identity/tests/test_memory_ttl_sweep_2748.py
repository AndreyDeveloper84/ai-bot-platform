"""Свип срока хранения жёлтой и красной памяти (DRF-2748, умная память Ф0).

До листа срок был только схемой: ``ttl_days``, ``expires_at``,
``DELETION_REASON_TTL_PURGE`` и зарезервированное имя ``ttl_purge_sweep`` в
условиях сторожа DRF-2542 — и ни одной задачи, которая снимала бы истёкшее.
Жёлтая строка с ``ttl_days=365`` жила бы вечно.

Узлы держат все ветви на настоящих строках, а не на пустой базе:

* истёкшая жёлтая и красная — сняты, причина ``ttl_purge``, у красной есть
  строка журнала;
* свежая — цела; зелёная — цела даже с прошедшей датой; строка без срока —
  цела;
* без ``expires_at`` при заданном сроке — дата проставляется по правилу 0016,
  и только потом о строке судят;
* человек с «забудь всё» или с живой заявкой на удаление — не трогается;
* факт, которым пользовались или на который недавно дали согласие, — цел
  (скользящее окно спеки §5);
* уже снятая строка не переписывается; повторный прогон ничего не меняет.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import pytest
from django.conf import settings
from django.db import connection, transaction

from apps.audit.models import AuditLog
from apps.identity.models import MemoryEntry, RedZoneAccessLog, UserPersonalContext
from apps.identity.services.memory_deleter import soft_delete_expired_entries
from apps.identity.tasks import memory_ttl_sweep

pytestmark = pytest.mark.django_db

_PG_ONLY = pytest.mark.skipif(
    connection.vendor != "postgresql", reason="RLS/GUC — механизм Postgres."
)

NOW = datetime(2026, 10, 5, 2, 35, tzinfo=UTC)

GREEN = MemoryEntry.SENSITIVITY_GREEN
YELLOW = MemoryEntry.SENSITIVITY_YELLOW
RED = MemoryEntry.SENSITIVITY_RED


def _upc(*, forgotten: bool = False, deletion_requested: bool = False) -> UserPersonalContext:
    return UserPersonalContext.objects.create(
        user_id=uuid.uuid4(),
        forget_all_requested_at=NOW - timedelta(days=1) if forgotten else None,
        deletion_requested_at=NOW - timedelta(days=1) if deletion_requested else None,
    )


def _entry(
    upc: UserPersonalContext,
    zone: str,
    *,
    age_days: int,
    ttl_days: int | None,
    expiry: str = "computed",
    used_days_ago: int | None = None,
    consent_days_ago: int | None = None,
) -> MemoryEntry:
    """Живая строка, будто записанная ``age_days`` дней назад.

    ``created_at`` и ``last_used_at`` — ``auto_now_add``, поэтому возраст
    ставится вторым UPDATE. ``expiry``: ``computed`` — запись + срок, как у
    писателя; ``missing`` — даты нет; ``past`` — дата вчера при любом сроке.
    """
    created = NOW - timedelta(days=age_days)
    entry = MemoryEntry.objects.create(
        user_id=upc.user_id,
        personal_context=upc,
        sensitivity_zone=zone,
        source=MemoryEntry.SOURCE_EXPLICIT,
        provenance=MemoryEntry.PROVENANCE_USER_STATED,
        consent_at=None if zone == GREEN else created,
        ttl_days=ttl_days,
        content={"key": "k", "value": "v"},
    )
    if expiry == "computed":
        expires_at = created + timedelta(days=ttl_days) if ttl_days is not None else None
    elif expiry == "past":
        expires_at = NOW - timedelta(days=1)
    else:
        expires_at = None
    MemoryEntry.objects.filter(pk=entry.pk).update(
        created_at=created,
        last_used_at=(
            NOW - timedelta(days=used_days_ago) if used_days_ago is not None else created
        ),
        consent_at=(
            NOW - timedelta(days=consent_days_ago)
            if consent_days_ago is not None
            else (None if zone == GREEN else created)
        ),
        expires_at=expires_at,
    )
    entry.refresh_from_db()
    return entry


def _sweep(**kwargs):
    return soft_delete_expired_entries(now=NOW, **kwargs)


def _purged(entry: MemoryEntry) -> bool:
    entry.refresh_from_db()
    return entry.soft_deleted_at is not None


class TestExpiredRowsAreTombstoned:
    def test_expired_yellow_is_tombstoned_with_the_ttl_reason(self) -> None:
        yellow = _entry(_upc(), YELLOW, age_days=400, ttl_days=365)

        result = _sweep()

        yellow.refresh_from_db()
        assert yellow.soft_deleted_at == NOW
        assert yellow.delete_requested_at == NOW
        assert yellow.deletion_reason == "ttl_purge"
        assert yellow.status == "deleted"
        assert yellow.updated_at == NOW
        assert result.purged_yellow == 1
        assert result.purged_red == 0

    def test_expired_red_is_tombstoned_and_logged(self) -> None:
        upc = _upc()
        red = _entry(upc, RED, age_days=100, ttl_days=90)

        result = _sweep()

        red.refresh_from_db()
        assert red.deletion_reason == "ttl_purge"
        assert red.soft_deleted_at == NOW
        assert result.purged_red == 1
        log = RedZoneAccessLog.objects.get(memory_entry_id=red.id)
        assert log.user_id == upc.user_id
        assert log.access_type == "purge"
        assert log.accessor_role == "system_job"
        assert log.accessor_principal.startswith("memory_ttl_sweep:")
        assert log.purpose == "ttl_sweep — срок хранения красной зоны истёк"

    def test_control_fresh_rows_of_both_zones_stay_live(self) -> None:
        upc = _upc()
        yellow = _entry(upc, YELLOW, age_days=100, ttl_days=365)
        red = _entry(upc, RED, age_days=30, ttl_days=90)
        expired = _entry(upc, RED, age_days=100, ttl_days=90)

        result = _sweep()

        assert _purged(expired)
        assert not _purged(yellow)
        assert not _purged(red)
        assert result.purged == 1
        assert RedZoneAccessLog.objects.filter(memory_entry_id=red.id).count() == 0


class TestWhatTheSweepNeverTouches:
    def test_green_survives_even_with_a_past_expiry(self) -> None:
        upc = _upc()
        green_with_term = _entry(upc, GREEN, age_days=400, ttl_days=90, expiry="past")
        green_plain = _entry(upc, GREEN, age_days=400, ttl_days=None)
        yellow = _entry(upc, YELLOW, age_days=400, ttl_days=365)

        _sweep()

        assert _purged(yellow)
        assert not _purged(green_with_term)
        assert not _purged(green_plain)

    def test_a_row_without_a_term_survives_even_with_a_past_expiry(self) -> None:
        upc = _upc()
        no_term = _entry(upc, YELLOW, age_days=400, ttl_days=None, expiry="past")
        with_term = _entry(upc, YELLOW, age_days=400, ttl_days=365)

        _sweep()

        assert _purged(with_term)
        assert not _purged(no_term)

    def test_forget_all_and_live_deletion_request_are_left_to_their_own_paths(self) -> None:
        forgotten = _entry(_upc(forgotten=True), RED, age_days=100, ttl_days=90)
        requested = _entry(_upc(deletion_requested=True), YELLOW, age_days=400, ttl_days=365)
        ordinary = _entry(_upc(), RED, age_days=100, ttl_days=90)

        result = _sweep()

        assert _purged(ordinary)
        assert not _purged(forgotten)
        assert not _purged(requested)
        assert result.purged == 1
        assert RedZoneAccessLog.objects.filter(memory_entry_id=forgotten.id).count() == 0

    def test_an_already_deleted_row_keeps_its_reason(self) -> None:
        upc = _upc()
        deleted = _entry(upc, YELLOW, age_days=400, ttl_days=365)
        earlier = NOW - timedelta(days=3)
        MemoryEntry.objects.filter(pk=deleted.pk).update(
            delete_requested_at=earlier,
            soft_deleted_at=earlier,
            deletion_reason="user_delete",
            status="deleted",
        )
        live = _entry(upc, YELLOW, age_days=400, ttl_days=365)

        result = _sweep()

        deleted.refresh_from_db()
        assert _purged(live)
        assert deleted.deletion_reason == "user_delete"
        assert deleted.soft_deleted_at == earlier
        assert result.purged == 1


class TestTheSlidingWindowOfSpec5:
    def test_a_recently_used_fact_survives_a_past_expiry(self) -> None:
        upc = _upc()
        used = _entry(upc, RED, age_days=100, ttl_days=90, used_days_ago=10)
        unused = _entry(upc, RED, age_days=100, ttl_days=90)

        _sweep()

        assert _purged(unused)
        assert not _purged(used)

    def test_a_recent_consent_keeps_the_fact(self) -> None:
        upc = _upc()
        reconsented = _entry(upc, YELLOW, age_days=400, ttl_days=365, consent_days_ago=20)
        stale = _entry(upc, YELLOW, age_days=400, ttl_days=365)

        _sweep()

        assert _purged(stale)
        assert not _purged(reconsented)


class TestMissingExpiryIsBackfilled:
    def test_a_fresh_row_gets_its_date_by_the_0016_rule_and_stays(self) -> None:
        fresh = _entry(_upc(), YELLOW, age_days=10, ttl_days=365, expiry="missing")

        result = _sweep()

        fresh.refresh_from_db()
        assert fresh.expires_at == NOW - timedelta(days=10) + timedelta(days=365)
        assert fresh.soft_deleted_at is None
        assert result.expiry_backfilled == 1
        assert result.purged == 0

    def test_an_old_row_without_a_date_is_dated_and_then_purged(self) -> None:
        old = _entry(_upc(), RED, age_days=100, ttl_days=90, expiry="missing")

        result = _sweep()

        old.refresh_from_db()
        assert old.expires_at == NOW - timedelta(days=100) + timedelta(days=90)
        assert old.deletion_reason == "ttl_purge"
        assert result.expiry_backfilled == 1
        assert result.purged_red == 1

    def test_green_and_termless_rows_are_not_dated(self) -> None:
        upc = _upc()
        green = _entry(upc, GREEN, age_days=10, ttl_days=None, expiry="missing")
        termless = _entry(upc, YELLOW, age_days=10, ttl_days=None, expiry="missing")
        dated = _entry(upc, YELLOW, age_days=10, ttl_days=365, expiry="missing")

        result = _sweep()

        green.refresh_from_db()
        termless.refresh_from_db()
        dated.refresh_from_db()
        assert dated.expires_at is not None
        assert green.expires_at is None
        assert termless.expires_at is None
        assert result.expiry_backfilled == 1


class TestRunShape:
    def test_the_batch_takes_the_oldest_expiry_first(self) -> None:
        upc = _upc()
        oldest = _entry(upc, RED, age_days=120, ttl_days=90)
        middle = _entry(upc, RED, age_days=110, ttl_days=90)
        newest = _entry(upc, RED, age_days=100, ttl_days=90)

        result = _sweep(limit=2)

        assert _purged(oldest)
        assert _purged(middle)
        assert not _purged(newest)
        assert result.purged_red == 2

    def test_a_second_run_changes_nothing(self) -> None:
        upc = _upc()
        _entry(upc, YELLOW, age_days=400, ttl_days=365)
        _entry(upc, RED, age_days=100, ttl_days=90)

        first = _sweep()
        second = _sweep()

        assert first.purged == 2
        assert second.as_summary() == {
            "expiry_backfilled": 0,
            "purged": 0,
            "purged_yellow": 0,
            "purged_red": 0,
            "users": 0,
        }
        assert RedZoneAccessLog.objects.filter(user_id=upc.user_id).count() == 1

    def test_the_run_is_audited_by_counts(self) -> None:
        _entry(_upc(), YELLOW, age_days=400, ttl_days=365)
        _entry(_upc(), RED, age_days=100, ttl_days=90)

        _sweep()

        row = AuditLog.all_tenants.get(action="memory.ttl_purged")
        assert row.payload["purged"] == 2
        assert row.payload["purged_yellow"] == 1
        assert row.payload["purged_red"] == 1
        assert row.payload["users"] == 2
        assert row.payload["reason"] == "ttl_purge"
        assert set(row.payload) == {
            "expiry_backfilled",
            "purged",
            "purged_yellow",
            "purged_red",
            "users",
            "reason",
            "request_id",
        }

    def test_an_idle_run_writes_no_audit_row(self) -> None:
        _entry(_upc(), YELLOW, age_days=10, ttl_days=365)

        result = _sweep()

        assert result.purged == 0
        assert AuditLog.all_tenants.filter(action="memory.ttl_purged").count() == 0


class TestTheBeatTask:
    def test_the_task_runs_the_sweep_and_returns_counts(self) -> None:
        yellow = _entry(_upc(), YELLOW, age_days=800, ttl_days=365)

        summary = memory_ttl_sweep()

        assert _purged(yellow)
        assert summary["purged_yellow"] == 1
        assert summary["purged"] == 1

    def test_the_task_is_scheduled_nightly(self) -> None:
        entry = settings.CELERY_BEAT_SCHEDULE["identity_memory_ttl_sweep"]
        assert entry["task"] == "apps.identity.tasks.memory_ttl_sweep"
        assert entry["schedule"].hour == {2}
        assert entry["schedule"].minute == {35}


@_PG_ONLY
class TestTheSweepSeesRedUnderTheAppRole:
    """Под ``ayla_app`` — иначе GUC никто не стережёт (см. DRF-2180).

    Вся сюита идёт под суперпользователем, который RLS обходит; без GUC свип
    был бы зелёным здесь и слепым к красной зоне в день перехода приложения
    на ``ayla_app``. ``write_audit`` подменён: таблицы аудита нет в грантах
    этой роли — отсутствующий грант предмет миграции §16, а не этого узла.
    """

    def test_the_red_row_is_tombstoned_and_logged_under_ayla_app(self) -> None:
        upc = _upc()
        red = _entry(upc, RED, age_days=100, ttl_days=90)
        undated = _entry(upc, RED, age_days=10, ttl_days=90, expiry="missing")

        with (
            patch("apps.identity.services.memory_deleter.write_audit"),
            transaction.atomic(),
        ):
            with connection.cursor() as cur:
                cur.execute("SET LOCAL ROLE ayla_app")
            try:
                result = _sweep()
            finally:
                with connection.cursor() as cur:
                    cur.execute("RESET ROLE")

        assert result.purged_red == 1, (
            "красная строка не попала в выборку под ролью приложения — "
            "запрос идёт без GUC, и RLS его не пускает"
        )
        assert result.expiry_backfilled == 1
        red.refresh_from_db()
        undated.refresh_from_db()
        assert red.deletion_reason == "ttl_purge"
        assert undated.expires_at is not None
        assert RedZoneAccessLog.objects.filter(memory_entry_id=red.id).count() == 1
