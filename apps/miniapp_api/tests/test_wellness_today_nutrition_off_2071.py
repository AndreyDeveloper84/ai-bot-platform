"""DRF-2071 — «Сегодня» в Mini App не читает дневник при выключенном контуре.

Перепись входов к пробе T-E2E-13 (17.09): записи дневника и воды из Mini App
закрывались ``_diary_entry_gate`` (404 ``nutrition_disabled``, DRF-1838/1919),
а ЧТЕНИЕ — ``GET wellness/today`` — флаг ``NUTRITION_ENABLED`` не читало: при
OFF экран рисовал дневник и воду из Ayla как при ON. Решение владельца 17.09:
при OFF закрыты UI, команда, callback, deep link и API — чтение тоже API.

Форма отказа — как у ветки «нет согласия» (DRF-1927), а не 404 (решение
главного окна 19.09, отклонение от листа): 200 с ``nutrition_disabled: true``,
``display_name`` и ``active_goals`` — имя и цель к дневнику не относятся, и
дашборд рисует по ним приветствие и тройку кнопки цели (DRF-1476). Питательной
половины в ответе нет ни ключом, ни чтением: ни одного вызова nutrition-клиента
Ayla. Флаг раньше согласия — «выключено» важнее «согласия нет» (как в чате,
``personal_surface.render_diary``). Положительный контроль на том же входе при
ON — иначе отказ неотличим от «ручка не читает никогда».
"""

# ruff: noqa: F811 — фикстуры стенда 1927 импортируются по имени и приходят
# параметрами узлов; для ruff это «переопределение», для pytest — механизм.
from __future__ import annotations

from unittest.mock import patch

import pytest
from django.test import Client

from apps.identity.models import BotUser
from apps.miniapp_api.tests.test_wellness_today import BOT_TOKEN
from apps.miniapp_api.tests.test_wellness_today_consent_1927 import (  # noqa: F401 — фикстуры
    CONSENT,
    DIARY_KEYS,
    _get,
    bot_user,
    goals,
    nutrition,
    tenant,
)

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _bot_token(settings):
    settings.MAX_BOT_TOKEN = BOT_TOKEN


@pytest.fixture
def nutrition_off(settings):
    settings.NUTRITION_ENABLED = False


@pytest.fixture
def nutrition_on(settings):
    settings.NUTRITION_ENABLED = True


class TestOffTheDiaryIsNeitherReadNorSent:
    @pytest.mark.parametrize("surface", [None, "diary"])
    def test_200_with_the_marker_and_no_diary_key_no_ayla_read(
        self, client: Client, bot_user: BotUser, nutrition, goals, nutrition_off, surface
    ):
        with patch(CONSENT, return_value=True):
            response = _get(client, bot_user, surface=surface)

        assert response.status_code == 200
        body = response.json()
        # Наличие раньше отсутствия: маркер и имя есть — и только потом
        # «ни одного ключа дневника/воды».
        assert body["nutrition_disabled"] is True
        assert body["display_name"] == "Анна К."
        leaked = DIARY_KEYS & set(body)
        assert not leaked, f"ключи дневника при выключенном контуре: {sorted(leaked)}"
        assert "consent_required" not in body
        nutrition.daily_summary.assert_not_called()
        nutrition.get_water_today.assert_not_called()
        nutrition.get_profile.assert_not_called()

    def test_the_goal_still_comes(
        self, client: Client, bot_user: BotUser, nutrition, goals, nutrition_off
    ):
        """Цель к дневнику не относится (DRF-1927) — тройка кнопки цели не деградирует."""
        with patch(CONSENT, return_value=True):
            body = _get(client, bot_user).json()

        assert goals.called
        assert len(body["active_goals"]) == 1
        assert "Высыпаться" in str(body["active_goals"][0])

    def test_a_goal_outage_omits_the_goal_key_here_too(
        self, client: Client, bot_user: BotUser, nutrition, goals, nutrition_off
    ):
        from apps.integrations.ayla.goals_client import GoalsUnavailable

        goals.side_effect = GoalsUnavailable("down")
        with patch(CONSENT, return_value=True):
            body = _get(client, bot_user).json()

        assert body["nutrition_disabled"] is True
        assert body["display_name"] == "Анна К."
        assert "active_goals" not in body

    def test_the_default_is_off(
        self, client: Client, bot_user: BotUser, nutrition, goals, settings
    ):
        """Флага нет в настройках вовсе — контур закрыт (fail-closed, как у ворот записи)."""
        del settings.NUTRITION_ENABLED
        with patch(CONSENT, return_value=True):
            body = _get(client, bot_user).json()

        assert body["nutrition_disabled"] is True
        assert body["display_name"] == "Анна К."
        assert not (DIARY_KEYS & set(body)), sorted(DIARY_KEYS & set(body))
        nutrition.daily_summary.assert_not_called()

    def test_the_flag_comes_before_consent(
        self, client: Client, bot_user: BotUser, nutrition, goals, nutrition_off
    ):
        """Без согласия при ON — ``consent_required``; при OFF — ``nutrition_disabled``: выключено важнее."""
        with patch(CONSENT, return_value=False) as consent:
            body = _get(client, bot_user).json()

        assert body["nutrition_disabled"] is True
        assert body["display_name"] == "Анна К."
        assert "consent_required" not in body
        consent.assert_not_called()


class TestPositiveControlOnTheReadIsWhatItWas:
    def test_200_with_the_diary_keys_and_no_marker(
        self, client: Client, bot_user: BotUser, nutrition, goals, nutrition_on
    ):
        with patch(CONSENT, return_value=True):
            response = _get(client, bot_user)

        assert response.status_code == 200
        body = response.json()
        # Наличие раньше отсутствия: ключи, которые при удачном чтении есть
        # всегда (цели/БЖУ — по наличию; ``coach_observation`` — только с
        # ``surface=diary``), и только потом — что маркеров отказа нет.
        expected = {"calories_eaten", "entries", "water_glasses_eaten"}
        assert expected <= DIARY_KEYS
        for key in sorted(expected):
            assert key in body, f"ключа дневника {key!r} нет при включённом контуре: {sorted(body)}"
        assert "nutrition_disabled" not in body
        assert "consent_required" not in body
        nutrition.daily_summary.assert_called_once()
        nutrition.get_water_today.assert_called_once()

    def test_on_without_consent_is_still_the_1927_answer(
        self, client: Client, bot_user: BotUser, nutrition, goals, nutrition_on
    ):
        with patch(CONSENT, return_value=False):
            body = _get(client, bot_user).json()

        assert body["consent_required"] is True
        assert body["display_name"] == "Анна К."
        assert "nutrition_disabled" not in body
