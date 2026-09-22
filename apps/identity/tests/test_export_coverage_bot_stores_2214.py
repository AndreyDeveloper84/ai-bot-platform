"""Выгрузка и её состав называют три хранилища бота и разделы каталога (DRF-2214, PR-3).

Замер показал, что «бот стирает сам» было неправдой в трёх местах: карточка
``Recommendation`` (причины дословно и факты), состояние движка готовности в
Redis (``dre:state:<id>``) и подключ ``BotUser.context["nutrition_proactive"]``
не стирались «забудь всё» и нигде не были объявлены. Здесь — выгрузка:

- ``recommendations`` — раздел выгрузки: что Ayla показала человеку и почему;
- ``nutrition_notification_settings`` — раздел: тумблеры проактивных сообщений
  о питании, которые человек ведёт сам (как ``preferences``);
- наблюдения (сколько выпил) и журнал отправок — объявлены невыгруженными с
  причиной; ``dre:state`` — тоже;
- разделы каталога (``goals`` … ``shown_hints``, beautygo_backend #544) —
  названы как содержимое раздела ``ayla``.

Стирание тех же хранилищ держит матрица (``test_forget_all_matrix.py``).
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING, cast

import pytest

from apps.identity.models import BotUser
from apps.identity.services.privacy import export_personal_data
from apps.identity.tests.test_export_coverage import _NoAyla
from apps.tenancy.models import Tenant

if TYPE_CHECKING:
    from apps.integrations.ayla.personal_context_client import PersonalContextHttpClient

pytestmark = pytest.mark.django_db


def _no_ayla() -> PersonalContextHttpClient:
    return cast("PersonalContextHttpClient", _NoAyla())


@pytest.fixture
def bot_user() -> BotUser:
    tenant = Tenant.objects.create(slug="cov-2214", name="Coverage 2214")
    return BotUser.all_tenants.create(
        tenant=tenant,
        channel="max",
        channel_user_id="2214",
        chat_id="2214",
        ayla_user_id=uuid.uuid4(),
        context={
            "nutrition_proactive": {
                "daily_report_time": "21:00",
                "water_reminders": True,
                "opted_out_at": None,
                "last_report_date": "2026-09-20",
                "water": {
                    "date": "2026-09-20",
                    "sent": 2,
                    "last_total_ml": 1400,
                    "ignored_streak": 1,
                },
                "outbox": [{"surface": "report", "sent_at": "2026-09-20T18:00:00+00:00"}],
            }
        },
    )


def _recommendation(bot_user: BotUser):
    from apps.recommendation.models import Recommendation

    return Recommendation.objects.create(
        bot_user=bot_user,
        goal_id="weight",
        what="Лимфодренажный массаж",
        subline="курс 5 сеансов",
        why=["вы сказали, что хотите к свадьбе сестры"],
        facts={"goal": "вес"},
        alternatives=[{"what": "Прессотерапия", "subline": "курс"}],
        fingerprint=uuid.uuid4().hex,
    )


class TestTheRecommendationsReachTheFile:
    def test_what_was_shown_and_why(self, bot_user) -> None:
        _recommendation(bot_user)

        payload = export_personal_data(bot_user, client=_no_ayla())

        (card,) = payload["recommendations"]
        assert card["what"] == "Лимфодренажный массаж"
        assert card["why"] == ["вы сказали, что хотите к свадьбе сестры"]
        assert card["facts"] == {"goal": "вес"}
        assert card["alternatives"] == [{"what": "Прессотерапия", "subline": "курс"}]

    def test_declared_as_carried(self, bot_user) -> None:
        payload = export_personal_data(bot_user, client=_no_ayla())

        assert "recommendation.Recommendation" in payload["coverage"]["included"]["recommendations"]


class TestTheNutritionSettingsReachTheFile:
    def test_the_toggles_come_back_and_the_observations_do_not(self, bot_user) -> None:
        """Тумблеры — в выгрузку; сколько выпил — нет (объявлено с причиной)."""
        payload = export_personal_data(bot_user, client=_no_ayla())

        (settings_row,) = payload["nutrition_notification_settings"]
        assert settings_row["daily_report_time"] == "21:00"
        assert settings_row["water_reminders"] is True
        assert "last_total_ml" not in repr(settings_row)

    def test_observations_and_journal_are_named_withheld(self, bot_user) -> None:
        payload = export_personal_data(bot_user, client=_no_ayla())
        withheld = {row["field"] for row in payload["coverage"]["withheld"]}

        assert "nutrition_proactive:observations" in withheld
        assert "nutrition_proactive:journal" in withheld
        assert (
            "nutrition_proactive:settings"
            in payload["coverage"]["included"]["nutrition_notification_settings"]
        )


class TestTheRestIsNamed:
    def test_the_readiness_state_is_named_withheld(self, bot_user) -> None:
        payload = export_personal_data(bot_user, client=_no_ayla())
        withheld = {row["field"] for row in payload["coverage"]["withheld"]}

        assert "redis.short_term" in withheld  # наличие: сосед того же вида
        assert "redis.dre_state" in withheld

    def test_the_catalog_sections_are_named_under_ayla(self, bot_user) -> None:
        payload = export_personal_data(bot_user, client=_no_ayla())

        carried = payload["coverage"]["included"]["ayla"]
        for section in ("goals", "wellness_plan", "nutrition_profile", "food_diary", "shown_hints"):
            assert f"catalog.{section}" in carried, section


class TestTheReadinessStateIsCleared:
    def test_clear_drops_the_state_key(self, monkeypatch) -> None:
        from apps.orchestrator.decision_readiness import state as dre_state

        store = {"dre:state:c-1": "{}", "dre:state:c-2": "{}"}

        class _Fake:
            def delete(self, key: str) -> int:
                return 1 if store.pop(key, None) is not None else 0

        monkeypatch.setattr(dre_state, "_redis_client", lambda: _Fake())

        dre_state.clear("c-1")

        assert "dre:state:c-2" in store  # наличие: чужой разговор на месте
        assert "dre:state:c-1" not in store
