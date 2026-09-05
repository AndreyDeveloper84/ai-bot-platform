"""Sweep просрочек действительно ЗАПУСКАЕТСЯ (DRF-1488).

Отдельно от логики: механизм, который никто не вызывает, неотличим от
отсутствующего — а именно так пилот и прожил месяц с задачами, которые
никто не подбирал. Здесь проверяется связка «beat-запись ↔ имя задачи ↔
функция», то есть ровно те три места, где опечатка молча выключает всё.
"""

from __future__ import annotations

import pytest

from apps.handoff.tasks import sweep_unclaimed_handoff_tasks

pytestmark = pytest.mark.django_db

_BEAT_KEY = "handoff_sweep_unclaimed_tasks"
_TASK_NAME = "handoff.sweep_unclaimed_tasks"


class TestBeatSchedule:
    def test_beat_entry_registered_with_the_canonical_task_name(self, settings):
        schedule = settings.CELERY_BEAT_SCHEDULE

        assert _BEAT_KEY in schedule
        assert schedule[_BEAT_KEY]["task"] == _TASK_NAME

    def test_the_registered_name_is_the_name_the_task_answers_to(self):
        """Запись в расписании и имя shared_task обязаны совпадать буквально."""

        assert sweep_unclaimed_handoff_tasks.name == _TASK_NAME

    def test_cadence_is_tighter_than_the_sla_it_guards(self, settings):
        """Тик реже SLA означал бы, что часть срока уходит на ожидание тика."""

        entry = settings.CELERY_BEAT_SCHEDULE[_BEAT_KEY]
        minute_spec = entry["schedule"]._orig_minute
        assert minute_spec == "*/5"
        assert int(settings.HANDOFF_PICKUP_SLA_MINUTES) > 5


class TestTaskReturnsWhatItDid:
    def test_task_reports_zero_on_an_empty_queue(self, settings):
        settings.HANDOFF_PICKUP_SLA_MINUTES = 15

        assert sweep_unclaimed_handoff_tasks() == {"escalated": 0}
