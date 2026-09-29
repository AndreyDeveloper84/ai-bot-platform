"""DRF-2267 срез 4 — проактивные сообщения без тупиков.

Вечерний отчёт и напоминание о воде приходят САМИ. До сих пор под ними была
одна кнопка — «Не присылать» (R6, DRF-1468): единственное, что человек мог
сделать одним тапом, — отписаться. Отчёт «Сегодня записей не было — считать
нечего» тем более оставлял ни с чем: он сообщает, что записей нет, и не даёт
их сделать.

Рамки владельца (решение главного окна 22.09, CD §76 №34):

* проактив пишет первым — кнопки зовут только в ЗАПИСЬ и в свой дневник
  («Записать еду», «Записать стакан воды», «Мой дневник»), но не к записи
  к мастеру: за тем человек не обращался;
* «Меню» здесь НЕ ставится: оно перечисляет и запись к мастеру, то есть тот
  самый зов, которого в проактиве быть не должно;
* «Не присылать» остаётся и остаётся последней — отписка не должна прятаться
  за новыми кнопками;
* контекста сверх цели, счёта плана и даты ближайшей записи кнопки не несут —
  они статические, о дневнике и здоровье не говорят.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from apps.nutrition_proactive import tasks
from apps.nutrition_proactive.tests.test_antinag import (
    NOON,
    button_payloads,
    make_user,
    summary_reader,
    water_reader,
)
from apps.tenancy.models import Tenant

pytestmark = pytest.mark.django_db


@pytest.fixture
def tenant(db) -> Tenant:
    """Свой арендатор: фикстуры соседнего файла не импортируются по имени."""
    return Tenant.objects.create(slug="dead-ends-2267", name="Salon", timezone="Europe/Moscow")


LOG_FOOD = "cb:welcome:food"
WATER = "стакан воды"
DIARY = "что я ел сегодня"


class TestProactiveOutboundOffersTheStepItTalksAbout:
    def test_the_water_nudge_offers_to_log_a_glass(self, tenant: Tenant, settings) -> None:
        make_user(tenant)
        settings.NUTRITION_PROACTIVE_ENABLED = True
        settings.NUTRITION_PROACTIVE_DRY_RUN = False
        with (
            patch("apps.nutrition_proactive.tasks.send_message") as send,
            patch("apps.nutrition_proactive.tasks._fetch_water", side_effect=water_reader(0)),
            patch("apps.nutrition_proactive.tasks.dj_timezone.now", return_value=NOON),
        ):
            result = tasks.send_water_reminders()

        assert result["sent"] == 1  # положительная пара: напоминание ушло
        assert button_payloads(send.call_args.kwargs["attachments"]) == [
            WATER,
            "cb:nutri:stop:water",
        ]

    def test_the_evening_report_offers_both_entries_and_the_diary(
        self, tenant: Tenant, settings
    ) -> None:
        make_user(tenant, report="19:00")
        settings.NUTRITION_PROACTIVE_ENABLED = True
        settings.NUTRITION_PROACTIVE_DRY_RUN = False
        with (
            patch("apps.nutrition_proactive.tasks.send_message") as send,
            patch("apps.nutrition_proactive.tasks._fetch_daily", side_effect=summary_reader()),
            patch(
                "apps.nutrition_proactive.tasks.dj_timezone.now",
                return_value=NOON.replace(hour=16),  # 19:00 MSK
            ),
        ):
            result = tasks.send_daily_reports()

        assert result["sent"] == 1
        assert button_payloads(send.call_args.kwargs["attachments"]) == [
            LOG_FOOD,
            WATER,
            "cb:nutri:stop:report",
        ]

    def test_no_button_calls_the_person_to_book(self, tenant: Tenant, settings) -> None:
        """Проактив не зовёт к мастеру и не открывает меню, где этот зов есть."""
        make_user(tenant, report="19:00")
        settings.NUTRITION_PROACTIVE_ENABLED = True
        settings.NUTRITION_PROACTIVE_DRY_RUN = False
        with (
            patch("apps.nutrition_proactive.tasks.send_message") as send,
            patch("apps.nutrition_proactive.tasks._fetch_daily", side_effect=summary_reader()),
            patch(
                "apps.nutrition_proactive.tasks.dj_timezone.now",
                return_value=NOON.replace(hour=16),
            ),
        ):
            tasks.send_daily_reports()

        payloads = button_payloads(send.call_args.kwargs["attachments"])
        assert payloads, "положительная пара: кнопки под отчётом есть"
        forbidden = ("cb:menu:", "cb:catalog:", "cb:discover:", "Помоги подобрать")
        assert [p for p in payloads if p.startswith(forbidden)] == []

    def test_the_unsubscribe_stays_last(self, tenant: Tenant, settings) -> None:
        """Отписка не прячется за новыми кнопками — она последняя (R6)."""
        make_user(tenant)
        settings.NUTRITION_PROACTIVE_ENABLED = True
        settings.NUTRITION_PROACTIVE_DRY_RUN = False
        with (
            patch("apps.nutrition_proactive.tasks.send_message") as send,
            patch("apps.nutrition_proactive.tasks._fetch_water", side_effect=water_reader(0)),
            patch("apps.nutrition_proactive.tasks.dj_timezone.now", return_value=NOON),
        ):
            tasks.send_water_reminders()

        assert button_payloads(send.call_args.kwargs["attachments"])[-1] == "cb:nutri:stop:water"
