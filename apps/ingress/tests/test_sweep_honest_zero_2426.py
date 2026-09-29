"""DRF-2426 — ноль часового обхода журнала обязан называть свой охват.

Замер 24.09 (живой контур): обход отдавал ``{'payloads': 0, 'rows': 0}``
правдиво — он смотрит только кромку последних :data:`SWEEP_LOOKBACK` суток, — а
за окном лежали 581 тело старше объявленного срока. Величина верна, предмет
другой: каждый час честный ноль подтверждал чистоту, которой нет.

Здесь проверяется ровно это свойство отчёта, а не чистка:

* за окном есть просроченное → вердикт ``backlog_outside_window`` и отдельное
  предупреждение с числами; ``clean`` — ложь;
* пустой журнал → ``scope_empty``: ноль верен и ничего не доказывает;
* непустая корзина, взятая целиком, и ничего за окном → ``clean`` (положительный
  контроль: иначе вердикт мог бы не уметь быть зелёным вовсе);
* корзина непуста, а взято меньше → ``incomplete``: без счёта корзины этот
  случай выглядел бы тем же нулём;
* строки за окном обход НЕ трогает — только измеряет (граница листа: удаление
  накопленного остаётся команде и слову владельца).
"""

from __future__ import annotations

import logging
from datetime import timedelta

import pytest
from django.utils import timezone

from apps.ingress.models import WebhookJournal
from apps.ingress.retention import SWEEP_LOOKBACK, beyond_window, sweep_expired

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("ingress_streams_empty")]

W_HOURS = 72
ROW_DAYS = 90


@pytest.fixture(autouse=True)
def _terms(settings):
    settings.INGRESS_RAW_RETENTION_HOURS = W_HOURS
    settings.WEBHOOK_JOURNAL_ROW_RETENTION_DAYS = ROW_DAYS


def _row(tag: str, *, age: timedelta, body: dict | None = None) -> WebhookJournal:
    row = WebhookJournal.objects.create(
        channel="max",
        external_event_id=f"ev-2426-{tag}",
        raw_payload={"update_type": "message_created", "tag": tag} if body is None else body,
    )
    WebhookJournal.objects.filter(pk=row.pk).update(received_at=timezone.now() - age)
    row.refresh_from_db()
    return row


# ── ноль за окном ────────────────────────────────────────────────────


class TestZeroNamesItsCoverage:
    def test_a_body_past_the_window_makes_the_zero_not_clean(self, caplog) -> None:
        # Тело старше срока НАСТОЛЬКО, что кромка до него не достаёт.
        old = _row("outside", age=timedelta(hours=W_HOURS) + SWEEP_LOOKBACK + timedelta(days=3))

        with caplog.at_level(logging.INFO, logger="apps.ingress.retention"):
            result = sweep_expired()

        # положительно: обход честно ничего не взял — предмет спора не в этом
        assert (result.payloads, result.rows) == (0, 0)
        assert result.in_window_payloads == 0 and result.in_window_rows == 0
        # …а вот охват этого нуля теперь назван
        assert result.beyond_window_payloads == 1
        assert result.verdict == "backlog_outside_window"
        assert result.clean is False
        text = caplog.text
        assert "beyond_window=1/0" in text
        assert "verdict=backlog_outside_window" in text
        assert "ingress.retention.backlog_outside_window" in text

        # и строку за окном обход не тронул — это дело команды и слова владельца
        old.refresh_from_db()
        assert old.raw_payload["tag"] == "outside"

    def test_an_empty_journal_is_named_empty_not_clean(self, caplog) -> None:
        assert WebhookJournal.objects.count() == 0  # охват пуст — это и проверяем

        with caplog.at_level(logging.INFO, logger="apps.ingress.retention"):
            result = sweep_expired()

        assert result.verdict == "scope_empty"
        assert result.clean is False
        assert "scanned=0" in caplog.text
        assert "verdict=scope_empty" in caplog.text

    def test_a_full_basket_taken_whole_reads_clean(self, caplog) -> None:
        # Положительный контроль: вердикт умеет быть зелёным на непустом охвате.
        inside = _row("inside", age=timedelta(hours=W_HOURS + 5))
        fresh = _row("fresh", age=timedelta(hours=1))

        with caplog.at_level(logging.INFO, logger="apps.ingress.retention"):
            result = sweep_expired()

        inside.refresh_from_db()
        fresh.refresh_from_db()
        assert inside.raw_payload == {}  # взято
        assert fresh.raw_payload["tag"] == "fresh"  # свежее не тронуто
        assert (result.payloads, result.in_window_payloads) == (1, 1)
        assert result.beyond_window == 0
        assert result.scanned == 2
        assert result.verdict == "clean"
        assert result.clean is True
        assert "verdict=clean" in caplog.text

    def test_a_basket_taken_only_in_part_is_not_a_plain_zero(self) -> None:
        from apps.ingress.retention import JournalPurge

        # Тот самый случай, который без счёта корзины был бы обычным нулём.
        partial = JournalPurge(payloads=0, rows=0, in_window_payloads=7, scanned=7)
        assert partial.took_its_basket is False
        assert partial.verdict == "incomplete"
        assert partial.clean is False

        whole = JournalPurge(payloads=7, rows=0, in_window_payloads=7, scanned=7)
        assert whole.verdict == "clean"  # положительно: та же форма умеет быть чистой


# ── измерение за окном ───────────────────────────────────────────────


class TestBeyondWindowIsMeasuredNotSwept:
    def test_it_counts_bodies_past_the_window_and_rows_past_the_row_term(self) -> None:
        _row("body-out", age=timedelta(hours=W_HOURS) + SWEEP_LOOKBACK + timedelta(days=1))
        _row("body-in", age=timedelta(hours=W_HOURS + 2))  # в кромке, не за окном
        _row("row-out", age=timedelta(days=ROW_DAYS) + SWEEP_LOOKBACK + timedelta(days=2))

        bodies, rows = beyond_window()

        assert bodies == 1  # только то, до чего кромка не достаёт
        assert rows == 1
        # и ничего не изменилось: это замер
        assert WebhookJournal.objects.count() == 3
        assert WebhookJournal.objects.exclude(raw_payload={}).count() == 3

    def test_a_row_past_its_term_is_not_counted_twice(self) -> None:
        # Строка старше срока строки уйдёт целиком — её тело не считается
        # отдельно, иначе один и тот же остаток попал бы в оба числа.
        _row("old-row", age=timedelta(days=ROW_DAYS) + SWEEP_LOOKBACK + timedelta(days=5))

        bodies, rows = beyond_window()

        assert rows == 1
        assert bodies == 0
