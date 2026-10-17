"""Физическая очистка надгробий памяти (DRF-2775).

Все пути стирания — «забудь X», «забудь всё», отзыв, срок — ставили только
надгробие: даты и причину, а зашифрованный ``content`` оставался в строке
навсегда. ADR-0011 §5 обещает физическое удаление после удержания (30 дней,
отзыв согласия — 24 часа); до листа его не делал никто.

Узлы на настоящих строках:

* надгробие старше удержания — строки нет; моложе — цела; живая — цела;
* отзыв согласия уходит через сутки, прочие причины — через 30 дней;
* красная — след в журнале переживает строку;
* ссылка ``superseded_by`` на удалённую строку обнуляется, ссылающаяся цела;
* пакет — самые старые надгробия первыми; аудит числами;
* задача инертна, пока выключатель закрыт;
* удержание не короче окна короткой памяти — иначе ``evicted_review``
  вернул бы стёртое.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import pytest
from django.conf import settings
from django.db import connection, transaction
from django.test import override_settings

from apps.audit.models import AuditLog
from apps.identity.models import MemoryEntry, RedZoneAccessLog, UserPersonalContext
from apps.identity.services.memory_deleter import (
    TOMBSTONE_RETENTION,
    WITHDRAWAL_TOMBSTONE_RETENTION,
    purge_expired_tombstones,
)
from apps.identity.tasks import memory_tombstone_purge

pytestmark = pytest.mark.django_db

_PG_ONLY = pytest.mark.skipif(
    connection.vendor != "postgresql", reason="RLS/GUC — механизм Postgres."
)

NOW = datetime(2026, 10, 5, 3, 5, tzinfo=UTC)

GREEN = MemoryEntry.SENSITIVITY_GREEN
YELLOW = MemoryEntry.SENSITIVITY_YELLOW
RED = MemoryEntry.SENSITIVITY_RED


def _upc() -> UserPersonalContext:
    return UserPersonalContext.objects.create(user_id=uuid.uuid4())


def _entry(upc: UserPersonalContext, zone: str) -> MemoryEntry:
    return MemoryEntry.objects.create(
        user_id=upc.user_id,
        personal_context=upc,
        sensitivity_zone=zone,
        source=MemoryEntry.SOURCE_EXPLICIT,
        provenance=MemoryEntry.PROVENANCE_USER_STATED,
        consent_at=None if zone == GREEN else NOW - timedelta(days=200),
        content={"key": "diet", "value": "vegan"},
    )


def _tombstone(
    upc: UserPersonalContext,
    zone: str,
    *,
    hours_ago: float,
    reason: str = "user_delete",
) -> MemoryEntry:
    """Строка с надгробием, поставленным ``hours_ago`` часов назад."""
    entry = _entry(upc, zone)
    when = NOW - timedelta(hours=hours_ago)
    MemoryEntry.objects.filter(pk=entry.pk).update(
        delete_requested_at=when,
        soft_deleted_at=when,
        deletion_reason=reason,
        status="deleted",
    )
    entry.refresh_from_db()
    return entry


def _exists(entry: MemoryEntry) -> bool:
    return MemoryEntry.objects.filter(pk=entry.pk).exists()


def _purge(**kwargs):
    return purge_expired_tombstones(now=NOW, **kwargs)


class TestRetentionThenPhysicalDelete:
    def test_a_tombstone_past_30_days_is_deleted_and_a_younger_one_stays(self) -> None:
        upc = _upc()
        old = _tombstone(upc, GREEN, hours_ago=31 * 24)
        young = _tombstone(upc, GREEN, hours_ago=29 * 24)
        live = _entry(upc, GREEN)

        result = _purge()

        assert _exists(young)
        assert _exists(live)
        assert not _exists(old)
        assert result.purged_green == 1
        assert result.purged == 1

    def test_every_reason_is_purged_not_only_one_path(self) -> None:
        upc = _upc()
        rows = [
            _tombstone(upc, GREEN, hours_ago=31 * 24, reason=reason)
            for reason in ("user_delete", "user_request_miniapp", "forget_all", "ttl_purge")
        ]
        keeper = _tombstone(upc, GREEN, hours_ago=1, reason="forget_all")

        result = _purge()

        assert _exists(keeper)
        assert [_exists(row) for row in rows] == [False, False, False, False]
        assert result.as_summary()["by_reason"] == {
            "forget_all": 1,
            "ttl_purge": 1,
            "user_delete": 1,
            "user_request_miniapp": 1,
        }

    def test_a_withdrawal_goes_after_a_day_other_reasons_wait_30(self) -> None:
        upc = _upc()
        withdrawn = _tombstone(upc, YELLOW, hours_ago=25, reason="withdrawal")
        fresh_withdrawal = _tombstone(upc, YELLOW, hours_ago=23, reason="withdrawal")
        deleted = _tombstone(upc, YELLOW, hours_ago=25, reason="user_delete")

        result = _purge()

        assert _exists(fresh_withdrawal)
        assert _exists(deleted)
        assert not _exists(withdrawn)
        assert result.purged_yellow == 1

    def test_the_retention_is_the_decided_numbers(self) -> None:
        assert TOMBSTONE_RETENTION == timedelta(days=30)
        assert WITHDRAWAL_TOMBSTONE_RETENTION == timedelta(hours=24)


class TestTheRedZoneTrailOutlivesTheRow:
    def test_a_red_row_is_deleted_and_its_log_row_stays(self) -> None:
        upc = _upc()
        red = _tombstone(upc, RED, hours_ago=31 * 24, reason="forget_all")
        red_id = red.id

        result = _purge()

        assert result.purged_red == 1
        assert not MemoryEntry.objects.filter(pk=red_id).exists()
        log = RedZoneAccessLog.objects.get(memory_entry_id=red_id)
        assert log.user_id == upc.user_id
        assert log.access_type == "purge"
        assert log.accessor_role == "system_job"
        assert log.accessor_principal.startswith("memory_tombstone_purge:")
        assert log.purpose == "tombstone_purge — удержание надгробия истекло"

    def test_green_and_yellow_rows_write_no_red_log(self) -> None:
        upc = _upc()
        green = _tombstone(upc, GREEN, hours_ago=31 * 24)
        yellow = _tombstone(upc, YELLOW, hours_ago=31 * 24)

        result = _purge()

        assert result.purged == 2
        assert (
            RedZoneAccessLog.objects.filter(memory_entry_id__in=[green.id, yellow.id]).count() == 0
        )


class TestReferencesToADeletedRow:
    def test_superseded_by_is_nulled_and_the_referring_row_survives(self) -> None:
        upc = _upc()
        replacement = _tombstone(upc, GREEN, hours_ago=31 * 24)
        older = _entry(upc, GREEN)
        MemoryEntry.objects.filter(pk=older.pk).update(superseded_by=replacement)

        _purge()

        assert _exists(older)
        older.refresh_from_db()
        assert older.superseded_by_id is None
        assert not _exists(replacement)


class TestRunShape:
    def test_the_batch_takes_the_oldest_tombstones_first(self) -> None:
        upc = _upc()
        oldest = _tombstone(upc, GREEN, hours_ago=40 * 24)
        middle = _tombstone(upc, GREEN, hours_ago=35 * 24)
        newest = _tombstone(upc, GREEN, hours_ago=31 * 24)

        result = _purge(limit=2)

        assert _exists(newest)
        assert not _exists(oldest)
        assert not _exists(middle)
        assert result.purged == 2

    def test_the_run_is_audited_by_counts(self) -> None:
        _tombstone(_upc(), GREEN, hours_ago=31 * 24)
        _tombstone(_upc(), RED, hours_ago=31 * 24, reason="forget_all")

        _purge()

        row = AuditLog.all_tenants.get(action="memory.tombstones_purged")
        assert row.payload["purged"] == 2
        assert row.payload["purged_green"] == 1
        assert row.payload["purged_red"] == 1
        assert row.payload["users"] == 2
        assert row.payload["by_reason"] == {"forget_all": 1, "user_delete": 1}
        assert set(row.payload) == {
            "purged",
            "purged_green",
            "purged_yellow",
            "purged_red",
            "users",
            "by_reason",
            "request_id",
        }

    def test_an_idle_run_writes_no_audit_row(self) -> None:
        young = _tombstone(_upc(), GREEN, hours_ago=24)

        result = _purge()

        assert _exists(young)
        assert result.purged == 0
        assert AuditLog.all_tenants.filter(action="memory.tombstones_purged").count() == 0


class TestTheSwitch:
    def test_the_task_is_inert_while_the_switch_is_closed(self) -> None:
        old = _tombstone(_upc(), GREEN, hours_ago=400 * 24)

        with override_settings(MEMORY_TOMBSTONE_PURGE_ENABLED=False):
            summary = memory_tombstone_purge()

        assert summary == {"mode": "disabled"}
        assert _exists(old)

    def test_the_switch_is_closed_by_default(self) -> None:
        assert settings.MEMORY_TOMBSTONE_PURGE_ENABLED is False

    def test_the_open_switch_purges(self) -> None:
        upc = _upc()
        old = _tombstone(upc, GREEN, hours_ago=400 * 24)
        young = _tombstone(upc, GREEN, hours_ago=24)

        with override_settings(MEMORY_TOMBSTONE_PURGE_ENABLED=True):
            summary = memory_tombstone_purge()

        assert summary["purged_green"] == 1
        assert _exists(young)
        assert not _exists(old)

    def test_the_task_is_scheduled_nightly(self) -> None:
        entry = settings.CELERY_BEAT_SCHEDULE["identity_memory_tombstone_purge"]
        assert entry["task"] == "apps.identity.tasks.memory_tombstone_purge"
        assert entry["schedule"].hour == {3}
        assert entry["schedule"].minute == {5}


class TestRetentionCoversTheShortTermWindow:
    """``evicted_review`` читает содержимое зелёных надгробий «стёрто по
    просьбе», чтобы не вернуть факт из сообщения, ещё лежащего в короткой
    памяти. Сообщение живёт там не дольше глубина × срок ключа (каждая
    реплика продлевает ключ). Удержание короче — и стёртое вернулось бы."""

    def test_the_window_today_is_twenty_messages_of_a_day(self) -> None:
        assert settings.SHORT_TERM_MEMORY_DEPTH == 20
        assert settings.SHORT_TERM_MEMORY_TTL_SECONDS == 24 * 3600

    def test_retention_is_longer_than_the_longest_life_in_the_window(self) -> None:
        window = timedelta(
            seconds=settings.SHORT_TERM_MEMORY_DEPTH * settings.SHORT_TERM_MEMORY_TTL_SECONDS
        )
        assert TOMBSTONE_RETENTION > window


@_PG_ONLY
class TestThePurgeSeesRedUnderTheAppRole:
    """Под ``ayla_app`` — иначе GUC никто не стережёт (см. DRF-2180).

    Политика ``memory_entry_non_red_visible`` прячет красные строки от SELECT
    без GUC, и WHERE у DELETE ей подчиняется. Суперпользователь сюиты RLS
    обходит, поэтому без этого узла забытый GUC был бы зелёным во всех тестах.
    ``write_audit`` подменён: таблицы аудита нет в грантах роли.
    """

    def test_the_red_tombstone_is_deleted_and_logged_under_ayla_app(self) -> None:
        upc = _upc()
        red = _tombstone(upc, RED, hours_ago=31 * 24, reason="forget_all")
        red_id = red.id

        with (
            patch("apps.identity.services.memory_deleter.write_audit"),
            transaction.atomic(),
        ):
            with connection.cursor() as cur:
                cur.execute("SET LOCAL ROLE ayla_app")
            try:
                result = _purge()
            finally:
                with connection.cursor() as cur:
                    cur.execute("RESET ROLE")

        assert result.purged_red == 1, (
            "красное надгробие не попало в выборку под ролью приложения — "
            "запрос идёт без GUC, и RLS его не пускает"
        )
        assert RedZoneAccessLog.objects.filter(memory_entry_id=red_id).count() == 1
        assert not MemoryEntry.objects.filter(pk=red_id).exists()
