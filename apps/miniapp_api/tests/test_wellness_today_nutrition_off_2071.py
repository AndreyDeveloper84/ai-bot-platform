"""DRF-2071 — «Сегодня» в Mini App не читает дневник при выключенном контуре.

Перепись входов к пробе T-E2E-13 (17.09): записи дневника и воды из Mini App
закрывались ``_diary_entry_gate`` (404 ``nutrition_disabled``, DRF-1838/1919),
а ЧТЕНИЕ — ``GET wellness/today`` — флаг ``NUTRITION_ENABLED`` не читало: при
OFF экран рисовал дневник и воду из Ayla как при ON. Решение владельца 17.09:
при OFF закрыты UI, команда, callback, deep link и API — чтение тоже API.

Те же ворота, что у записи: 404 ``nutrition_disabled`` с тем же телом, и ни
одного чтения Ayla. Флаг раньше согласия — «выключено» важнее «согласия
нет» (как в чате, ``personal_surface.render_diary``). Положительный контроль
на том же входе при ON — иначе отказ неотличим от «ручка не читает никогда».
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


class TestOffTheReadIsRefusedLikeTheWrites:
    @pytest.mark.parametrize("surface", [None, "diary"])
    def test_404_nutrition_disabled_and_no_ayla_read(
        self, client: Client, bot_user: BotUser, nutrition, goals, nutrition_off, surface
    ):
        with patch(CONSENT, return_value=True):
            response = _get(client, bot_user, surface=surface)

        assert response.status_code == 404
        # То же тело, что у записи воды/еды (``_diary_entry_gate``): экран уже
        # знает этот код (customer-wellness.ts, ``nutrition_disabled``).
        assert response.json() == {
            "error": "nutrition_disabled",
            "detail": "food diary is not enabled",
        }
        nutrition.daily_summary.assert_not_called()
        nutrition.get_water_today.assert_not_called()
        nutrition.get_profile.assert_not_called()
        goals.assert_not_called()

    def test_the_flag_comes_before_consent(
        self, client: Client, bot_user: BotUser, nutrition, goals, nutrition_off
    ):
        """Без согласия при ON — 200 ``consent_required``; при OFF — 404: выключено важнее."""
        with patch(CONSENT, return_value=False) as consent:
            response = _get(client, bot_user)

        assert response.status_code == 404
        assert response.json()["error"] == "nutrition_disabled"
        consent.assert_not_called()


class TestPositiveControlOnTheReadIsWhatItWas:
    def test_200_with_the_diary_keys(
        self, client: Client, bot_user: BotUser, nutrition, goals, nutrition_on
    ):
        with patch(CONSENT, return_value=True):
            response = _get(client, bot_user)

        assert response.status_code == 200
        body = response.json()
        # Наличие раньше отсутствия: ключи, которые при удачном чтении есть
        # всегда (цели/БЖУ — по наличию; ``coach_observation`` — только с
        # ``surface=diary``), и только потом — что согласие не спрашивалось.
        expected = {"calories_eaten", "entries", "water_glasses_eaten"}
        assert expected <= DIARY_KEYS
        for key in sorted(expected):
            assert key in body, f"ключа дневника {key!r} нет при включённом контуре: {sorted(body)}"
        assert "consent_required" not in body
        nutrition.daily_summary.assert_called_once()
        nutrition.get_water_today.assert_called_once()

    def test_on_without_consent_is_still_the_1927_answer(
        self, client: Client, bot_user: BotUser, nutrition, goals, nutrition_on
    ):
        with patch(CONSENT, return_value=False):
            response = _get(client, bot_user)

        assert response.status_code == 200
        assert response.json()["consent_required"] is True
