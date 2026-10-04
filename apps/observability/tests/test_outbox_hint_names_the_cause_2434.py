"""Подсказка W012 называет фактическую причину, а не строку от 09.09 (DRF-2434).

До правки подсказка говорила: «задача объявлена, но её нет в
CELERY_BEAT_SCHEDULE». С 11.09 (DRF-1616) запись в расписании есть — за
рубильником. Оператор по старой подсказке нашёл бы запись на месте, и
следующим очевидным ходом открыл бы рубильник — то есть сделал бы ровно
необратимое, если подписчика ещё нет.

Подсказка теперь читает флаги, и у каждого из четырёх исходов обёртки
`dispatch_pending_events_beat` своя причина. Стенд 04.10 стоит в первой
ветке: рубильник закрыт, сухой прогон, реестр только из Noop.

Порядок починки обязан звучать в каждой ветке: ради него подсказка и есть.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.conf import settings as django_settings
from django.utils import timezone

from apps.eventbus.models import DomainEvent
from apps.observability.checks import check_outbox_backlog, outbox_dispatch_cause

NOOP = "apps.eventbus.dispatcher.NoopSubscriber"
REAL = "apps.eventbus.subscribers.AuditSubscriber"
ORDER = "счётчик, затем подписчик, затем расписание"


def _flags(settings, *, enabled: bool, dry_run: bool, subscribers: list[str]) -> None:
    settings.EVENTBUS_DISPATCH_BEAT_ENABLED = enabled
    settings.EVENTBUS_DISPATCH_BEAT_DRY_RUN = dry_run
    settings.DOMAIN_EVENT_SUBSCRIBERS = subscribers


CASES = [
    pytest.param(
        dict(enabled=False, dry_run=True, subscribers=[NOOP]), "ENABLED закрыт", id="disabled"
    ),
    pytest.param(
        dict(enabled=True, dry_run=True, subscribers=[NOOP]), "DRY_RUN включён", id="dry-run"
    ),
    pytest.param(
        dict(enabled=True, dry_run=False, subscribers=[NOOP]), "refused_noop_only", id="noop-only"
    ),
    pytest.param(
        dict(enabled=True, dry_run=False, subscribers=[]), "refused_noop_only", id="empty"
    ),
    pytest.param(
        dict(enabled=True, dry_run=False, subscribers=[NOOP, REAL]), "celery-beat", id="live"
    ),
]


@pytest.mark.parametrize(("flags", "marker"), CASES)
def test_each_outcome_names_its_own_cause_and_the_order(settings, flags, marker) -> None:
    _flags(settings, **flags)

    hint = outbox_dispatch_cause()

    assert marker in hint
    assert ORDER in hint
    other_markers = {str(c.values[1]) for c in CASES} - {marker}
    assert not [m for m in other_markers if m in hint], hint


def test_the_hint_no_longer_says_the_task_is_missing_from_the_schedule(settings) -> None:
    """Запись в расписании есть с 11.09 — подсказка не вправе это отрицать."""
    _flags(settings, enabled=False, dry_run=True, subscribers=[NOOP])

    hint = outbox_dispatch_cause()

    assert "dispatch_pending_events_every_minute" in hint
    assert "её нет в CELERY_BEAT_SCHEDULE" not in hint
    assert "dispatch_pending_events_every_minute" in django_settings.CELERY_BEAT_SCHEDULE


def test_the_repo_defaults_land_on_the_closed_switch() -> None:
    """Стенд 04.10: ни один из трёх флагов не задан — наследуется base."""
    assert "ENABLED закрыт" in outbox_dispatch_cause()


@pytest.mark.django_db
def test_the_system_check_carries_the_live_cause(settings) -> None:
    """Связь: сам W012 отдаёт эту подсказку, а не прежнюю строку."""
    _flags(settings, enabled=True, dry_run=True, subscribers=[NOOP])
    row = DomainEvent.objects.create(
        event_id="01J" + "H" * 23,
        event_name="customer.consent.changed",
        event_version="1",
        occurred_at=timezone.now(),
        actor={},
        data={},
        metadata={},
    )
    DomainEvent.objects.filter(pk=row.pk).update(created_at=timezone.now() - timedelta(days=53))

    (warning,) = check_outbox_backlog()

    assert warning.hint == outbox_dispatch_cause()
    assert "DRY_RUN включён" in warning.hint
