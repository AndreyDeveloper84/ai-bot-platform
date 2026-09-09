"""W012 — счётчик неразобранного исходящего ящика.

Предмет проверок — не арифметика, а свойства сторожа, каждое из которых
куплено разбором пилота (`docs/PILOT_MEASUREMENTS.md` §11):

* он кричит, когда диспетчера нет вовсе — иначе он повторил бы
  `_emit_dlq_alert`, живущую внутри того, за чем следит;
* он молчит на молодом ящике — иначе у здорового диспетчера, у которого
  между тиками всегда что-то лежит, он кричал бы непрерывно и его
  отключили бы;
* он говорит ВОЗРАСТ, а не только число — ноль при мёртвом писателе
  выглядит как ноль при работающей доставке;
* он называет согласия отдельно от суммы;
* и он не молчит, когда посчитать не удалось.
"""

from __future__ import annotations

import logging
from datetime import timedelta

import pytest
from django.utils import timezone

from apps.eventbus.models import DomainEvent
from apps.observability.checks import (
    OUTBOX_BACKLOG_CHECK_ID,
    check_outbox_backlog,
    log_outbox_backlog,
)
from apps.observability.outbox_backlog import STALE_AFTER, measure_outbox_backlog

pytestmark = pytest.mark.django_db


def _event(
    *,
    event_id: str,
    name: str = "booking.created",
    age: timedelta = timedelta(0),
    anchor=None,
    dispatched: bool = False,
    dead_lettered: bool = False,
) -> DomainEvent:
    anchor = anchor or timezone.now()
    row = DomainEvent.objects.create(
        event_id=event_id,
        event_name=name,
        event_version="1",
        occurred_at=anchor - age,
        actor={},
        data={},
        metadata={},
        is_dispatched=dispatched,
        dead_lettered_at=anchor if dead_lettered else None,
    )
    # ``created_at`` — auto_now_add, поэтому возраст проставляется после
    # вставки. Через .update(), а не .save(), чтобы auto_now_add не
    # переписал его обратно: иначе тест «старая строка» молча мерил бы
    # ноль секунд и зеленел на любом пороге.
    DomainEvent.objects.filter(pk=row.pk).update(created_at=anchor - age)
    row.refresh_from_db()
    return row


class TestTheGuardScreamsWhenTheSubjectIsAbsent:
    """Главное свойство: сторож не зависит от того, кого сторожит."""

    def test_stale_backlog_is_reported_without_any_dispatcher(self) -> None:
        """Диспетчера в этом тесте нет вовсе — и это точная копия пилота.

        Задача не запускается, `_emit_dlq_alert` не вызывается ни разу,
        попыток ноль. Ровно то состояние, в котором сегодняшняя шина
        молчит. Сторож обязан заговорить именно здесь.
        """
        _event(event_id="01J" + "A" * 23, age=timedelta(days=28))

        warnings = check_outbox_backlog()

        assert len(warnings) == 1
        assert warnings[0].id == OUTBOX_BACKLOG_CHECK_ID
        assert "28 сут" in warnings[0].msg

    def test_dispatched_and_dead_lettered_rows_are_not_backlog(self) -> None:
        """Положительная стража: сторож считает НЕ всё подряд.

        Без неё «кричит на старом ящике» проходило бы и у счётчика,
        считающего каждую строку таблицы, — то есть у сторожа, который
        никогда не замолчит и потому бесполезен.
        """
        _event(event_id="01J" + "B" * 23, age=timedelta(days=28), dispatched=True)
        _event(event_id="01J" + "C" * 23, age=timedelta(days=28), dead_lettered=True)

        backlog = measure_outbox_backlog()

        assert backlog.pending == 0
        assert not backlog.is_stale
        assert check_outbox_backlog() == []


class TestItStaysQuietOnAHealthyBox:
    def test_young_rows_do_not_trigger(self) -> None:
        """У здорового диспетчера ящик непуст постоянно.

        Между тиками в нём лежит то, что он разберёт через минуту. Сторож,
        кричащий на это, будет отключён — и вместе с ним потеряется
        настоящий сигнал.
        """
        _event(event_id="01J" + "D" * 23, age=STALE_AFTER / 2)

        backlog = measure_outbox_backlog()

        assert backlog.pending == 1, "строка должна попасть в счёт"
        assert not backlog.is_stale
        assert check_outbox_backlog() == []

    def test_empty_box_says_nothing(self) -> None:
        assert measure_outbox_backlog().pending == 0
        assert check_outbox_backlog() == []

    def test_the_same_row_is_quiet_young_and_loud_old(self) -> None:
        """Положительная стража ко всем проверкам «сторож молчит».

        Написан после мутационной проверки: если подменить ``is_stale``
        на ``False``, все тесты вида ``check_outbox_backlog() == []``
        остаются ЗЕЛЁНЫМИ — ослепший сторож молчит идеально. Одиночный
        случай «молчит на молодом» не отличает верное молчание от вечного.

        Здесь одна и та же строка проверяется дважды, и различает их
        только возраст: молодая — тишина, состаренная — крик. Ослепший
        сторож проваливает вторую половину.
        """
        row = _event(event_id="01J" + "Q" * 23, age=STALE_AFTER / 2)

        assert check_outbox_backlog() == [], "на молодой строке сторож обязан молчать"

        DomainEvent.objects.filter(pk=row.pk).update(created_at=timezone.now() - (STALE_AFTER * 3))

        warnings = check_outbox_backlog()
        assert len(warnings) == 1, "та же строка, состаренная, обязана поднять сторожа"
        assert warnings[0].id == OUTBOX_BACKLOG_CHECK_ID


class TestAgeIsReportedNotOnlyCount:
    def test_oldest_age_is_measured_against_the_given_now(self) -> None:
        """``now`` параметром: тест мерит возраст, а не расхождение часов."""
        now = timezone.now()
        # Тот же миг и для вставки, и для замера — иначе тест мерил бы
        # не возраст строки, а задержку между двумя вызовами часов.
        _event(event_id="01J" + "E" * 23, age=timedelta(days=3), anchor=now)
        _event(event_id="01J" + "F" * 23, age=timedelta(days=10), anchor=now)

        backlog = measure_outbox_backlog(now=now)

        assert backlog.pending == 2
        # Самая старая, а не последняя и не средняя.
        assert backlog.oldest_age is not None
        assert backlog.oldest_age == timedelta(days=10)
        assert "10 сут" in backlog.describe()


class TestConsentIsNamedApartFromTheSum:
    def test_consent_events_get_their_own_number(self) -> None:
        """Тринадцать разрешений и сорок атрибуций не складываются.

        Читателю нельзя предлагать заметить это самому в общей сумме —
        поэтому у согласий отдельное число, а не только строка в разрезе.
        """
        for i in range(3):
            _event(event_id=f"01JG{i:022d}", name="customer.consent.changed", age=timedelta(days=2))
        for i in range(5):
            _event(
                event_id=f"01JH{i:022d}",
                name="booking.attribution.assigned",
                age=timedelta(days=2),
            )

        backlog = measure_outbox_backlog()

        assert backlog.pending == 8
        assert backlog.consent_pending == 3
        text = backlog.describe()
        assert "о согласиях: 3" in text
        assert "customer.consent.changed=3" in text
        assert "booking.attribution.assigned=5" in text


class TestAFailedCountIsNotSilence:
    def test_measurement_failure_is_logged_with_a_reason(
        self, caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Молчание при отказе сторожа = молчание при пустом ящике.

        Это тот самый дефект, против которого сторож заведён, только
        этажом выше: на пилоте таблица чужого приложения могла бы ещё не
        существовать (``ready()`` идёт и под ``migrate``), и «ноль»
        сказать было бы нельзя — мы его не измерили.
        """
        monkeypatch.setattr(
            "apps.observability.outbox_backlog.measure_outbox_backlog",
            lambda **kw: (_ for _ in ()).throw(RuntimeError("no such table")),
        )

        with caplog.at_level(logging.INFO, logger="apps.observability.checks"):
            log_outbox_backlog()

        records = [r for r in caplog.records if "outbox_backlog" in r.message]
        assert records, "отказ обязан оставить строку, а не тишину"
        assert records[0].levelno == logging.WARNING, (
            "отказ и рутинное «пусто» обязаны различаться УРОВНЕМ: "
            "иначе фильтр по WARNING потеряет именно отказ"
        )
        assert "not_measured" in records[0].message
        assert "no such table" in records[0].message, "причина обязательна"

    def test_the_system_check_survives_a_missing_table(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Системные проверки идут и там, где таблицы законно нет.

        ``manage.py check`` и ``migrate`` — до применения миграций. Без
        этого сторожа проверка роняла бы обе команды трассировкой
        ``OperationalError: no such table``, то есть сторож исходящего
        ящика ломал бы выкладку — ровно то, от чего он должен защищать.

        Молчит он здесь не из осторожности: «таблицы нет» — факт о
        немигрированном приложении, а не о ящике. Имя отказу даёт
        :func:`log_outbox_backlog` в работающем процессе, где недоступная
        БД действительно новость.
        """
        from django.db import OperationalError

        monkeypatch.setattr(
            "apps.observability.outbox_backlog.measure_outbox_backlog",
            lambda **kw: (_ for _ in ()).throw(OperationalError("no such table")),
        )

        assert check_outbox_backlog() == []

    def test_a_reporter_failure_never_breaks_boot(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Сторож, способный уронить загрузку, — авария, которую он же
        и должен был предотвращать."""
        monkeypatch.setattr(
            "apps.observability.outbox_backlog.measure_outbox_backlog",
            lambda **kw: (_ for _ in ()).throw(RuntimeError("boom")),
        )

        log_outbox_backlog()  # не должно бросить

    def test_healthy_box_and_failure_differ_in_level(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Положительная стража к предыдущему: рутинный путь — INFO."""
        with caplog.at_level(logging.INFO, logger="apps.observability.checks"):
            log_outbox_backlog()

        records = [r for r in caplog.records if "outbox_backlog" in r.message]
        assert records, "здоровый путь тоже обязан оставлять след"
        assert records[0].levelno == logging.INFO
        assert "ok" in records[0].message
