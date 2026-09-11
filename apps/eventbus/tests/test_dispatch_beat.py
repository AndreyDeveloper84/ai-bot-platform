"""Beat-обёртка над диспетчером исходящего ящика (DRF-1616, блокер B-7).

`dispatch_pending_events` не звал никто, кроме тестов: ни расписание, ни
сигнал. Тревога о застрявшем ящике (`_emit_dlq_alert`) живёт внутри
диспетчера, и пока его не зовут — она молчит (правила исполнителя §23).
Здесь стережётся, что расписание теперь указывает на обёртку, что имя в нём
резолвится в реестре, и что у обёртки три исхода, различимые по `mode`:

    disabled   рубильник закрыт        → базу не трогает вовсе
    dry_run    открыт, DRY_RUN=True    → считает pending, ничего не помечает
    live       открыт, DRY_RUN=False   → настоящая доставка, строки помечены

`transaction=True` — как у соседних тестов диспетчера: claim идёт через
`select_for_update(skip_locked=True)` внутри `atomic`, и обёрнутая в тестовую
транзакцию база это не воспроизводит.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from celery.schedules import crontab  # type: ignore[import-untyped]
from django.conf import settings

from apps.eventbus import dispatcher, services, vocabulary as V
from apps.eventbus.dispatcher import dispatch_pending_events_beat
from apps.eventbus.models import DomainEvent

pytestmark = pytest.mark.django_db(transaction=True)

SCHEDULE_KEY = "dispatch_pending_events_every_minute"
TASK_NAME = "apps.eventbus.dispatch_pending_events_beat"


def _emit(n: int) -> None:
    for i in range(n):
        services.emit(
            V.BOOKING_CREATED,
            {
                "booking_id": f"beat-b{i}",
                "customer_id": f"beat-c{i}",
                "service_id": "s",
                "slot_start": "2026-09-12T10:00:00Z",
                "booking_source": "ai_direct",
            },
            actor_type="system",
        )


def _pending() -> int:
    return DomainEvent.objects.filter(is_dispatched=False, dead_lettered_at__isnull=True).count()


# ─── расписание ──────────────────────────────────────────────────────────────


def test_schedule_entry_points_at_the_beat_wrapper_every_minute() -> None:
    entry = settings.CELERY_BEAT_SCHEDULE[SCHEDULE_KEY]

    assert entry["task"] == TASK_NAME == dispatch_pending_events_beat.name
    sched = entry["schedule"]
    assert isinstance(sched, crontab)
    assert len(sched.minute) == 60, "ящик — доставка; реже минуты тревога о нём запаздывает"


def test_schedule_task_name_is_a_registered_celery_task() -> None:
    """Опечатка в строке расписания при верном `.name` = beat шлёт в никуда."""
    from config.celery import app

    app.loader.import_default_modules()
    assert settings.CELERY_BEAT_SCHEDULE[SCHEDULE_KEY]["task"] in app.tasks


def test_the_schedule_does_not_bypass_the_wrapper() -> None:
    """Ни одна запись расписания не указывает на голый диспетчер.

    Иначе рубильник стерёг бы обёртку, а beat ходил бы мимо неё.
    """
    bare = [
        k
        for k, v in settings.CELERY_BEAT_SCHEDULE.items()
        if v["task"] == "apps.eventbus.dispatch_pending_events"
    ]
    assert bare == [], bare


def test_switches_are_closed_by_default() -> None:
    """Слияние ничего не включает. Открывает владелец через окружение."""
    assert settings.EVENTBUS_DISPATCH_BEAT_ENABLED is False
    assert settings.EVENTBUS_DISPATCH_BEAT_DRY_RUN is True


# ─── три исхода ──────────────────────────────────────────────────────────────


def test_disabled_does_not_touch_the_database(settings, django_assert_num_queries) -> None:
    settings.EVENTBUS_DISPATCH_BEAT_ENABLED = False

    with django_assert_num_queries(0):
        result = dispatch_pending_events_beat()

    assert result == {"mode": "disabled", "pending": None}


def test_dry_run_counts_pending_and_marks_nothing(settings) -> None:
    settings.EVENTBUS_DISPATCH_BEAT_ENABLED = True
    settings.EVENTBUS_DISPATCH_BEAT_DRY_RUN = True
    _emit(3)
    before = _pending()
    assert before == 3

    with patch.object(dispatcher, "dispatch_pending_events") as live:
        result = dispatch_pending_events_beat()

    live.assert_not_called()
    assert result == {"mode": "dry_run", "pending": 3}
    assert _pending() == before, "сухой прогон пометил строки"
    assert DomainEvent.objects.filter(dispatch_attempts__gt=0).count() == 0


def test_live_dispatches_and_reports_the_dispatcher_counters(settings) -> None:
    settings.EVENTBUS_DISPATCH_BEAT_ENABLED = True
    settings.EVENTBUS_DISPATCH_BEAT_DRY_RUN = False
    _emit(3)

    result = dispatch_pending_events_beat()

    assert result["mode"] == "live"
    assert result["claimed"] == 3
    assert result["dispatched"] == 3
    assert DomainEvent.objects.filter(event_name=V.BOOKING_CREATED, is_dispatched=True).count() == 3
