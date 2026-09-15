"""DRF-1927 — «Сегодня» в Mini App не читает дневник без согласия.

Правило то же, что у чата (``personal_surface.render_diary``) и у записи
воды (DRF-1919/1926): без согласия на обработку личных данных
(``PERSONAL_DATA``, fail-closed) дневник не читается. Ответ — 200 без ключей
дневника и с ``consent_required``; цель к дневнику не относится и приходит как
раньше. Решение главного окна 15.09.

Обе половины на одном входе: без согласия чтений Ayla по дневнику нет, с
согласием — есть. Отказ без положительной стражи неотличим от «ручка ничего
не читает никогда».
"""

from __future__ import annotations

from unittest.mock import AsyncMock, Mock, patch

import pytest
from django.test import Client
from django.urls import reverse

from apps.identity.models import BotUser
from apps.miniapp_api.tests.test_wellness_today import (
    _FakeProfile,
    _FakeSummary,
    _FakeWater,
    _goal_doc,
    _init_data_header,
    _no_goal_doc,
)
from apps.tenancy.models import Tenant

pytestmark = pytest.mark.django_db


@pytest.fixture
def tenant(db, settings) -> Tenant:
    t = Tenant.objects.create(slug="wellness-1927", name="Wellness 1927", timezone="Europe/Moscow")
    settings.MAX_BOT_TENANT_SLUG = "wellness-1927"
    return t


@pytest.fixture
def bot_user(tenant: Tenant) -> BotUser:
    return BotUser.all_tenants.create(
        tenant=tenant,
        channel="max",
        channel_user_id="91927",
        display_name="Анна",
        client_name="Анна К.",
    )


CONSENT = "apps.orchestrator.personal_surface.personal_records_consent_open"
DIARY_KEYS = {
    "calories_eaten",
    "calories_target",
    "pfc",
    "entries",
    "nutrition_numbers_hidden",
    "water_glasses_eaten",
    "water_glasses_target",
    "coach_observation",
}


@pytest.fixture(autouse=True)
def _bot_token(settings):
    from apps.miniapp_api.tests.test_wellness_today import BOT_TOKEN

    settings.MAX_BOT_TOKEN = BOT_TOKEN


@pytest.fixture
def goals():
    with patch("apps.integrations.ayla.goals_client.fetch_decision_context") as m:
        m.return_value = _goal_doc(goal_text="Высыпаться")
        yield m


@pytest.fixture
def nutrition():
    client = Mock()
    client.daily_summary = AsyncMock(return_value=_FakeSummary(entries=[]))
    client.get_water_today = AsyncMock(return_value=_FakeWater())
    client.get_profile = AsyncMock(return_value=_FakeProfile())
    with patch("apps.integrations.ayla.get_nutrition_client", return_value=client):
        yield client


def _get(client: Client, bot_user: BotUser, *, surface: str | None = None):
    url = reverse("miniapp_api:customer_wellness_today")
    if surface:
        url = f"{url}?surface={surface}"
    return client.get(url, HTTP_AUTHORIZATION=_init_data_header(bot_user.channel_user_id))


class TestNoConsentNoDiaryRead:
    @pytest.mark.parametrize("surface", [None, "diary"])
    def test_no_consent_reads_nothing_of_the_diary_and_says_why(
        self, client: Client, bot_user: BotUser, nutrition, goals, surface
    ):
        with patch(CONSENT, return_value=False):
            response = _get(client, bot_user, surface=surface)

        assert response.status_code == 200
        body = response.json()
        assert body["consent_required"] is True
        assert not DIARY_KEYS & set(body), f"ключи дневника без согласия: {DIARY_KEYS & set(body)}"
        nutrition.daily_summary.assert_not_called()
        nutrition.get_water_today.assert_not_called()
        nutrition.get_profile.assert_not_called()

    def test_the_goal_still_comes(self, client: Client, bot_user: BotUser, nutrition, goals):
        with patch(CONSENT, return_value=False):
            body = _get(client, bot_user).json()

        assert goals.called
        assert len(body["active_goals"]) == 1
        assert "Высыпаться" in str(body["active_goals"][0])
        assert body["display_name"] == "Анна К."

    def test_a_goal_outage_omits_the_goal_key_here_too(
        self, client: Client, bot_user: BotUser, nutrition, goals
    ):
        from apps.integrations.ayla.goals_client import GoalsUnavailable

        goals.side_effect = GoalsUnavailable("down")
        with patch(CONSENT, return_value=False):
            body = _get(client, bot_user).json()

        assert body["consent_required"] is True
        assert "active_goals" not in body

    def test_a_consent_read_that_raises_reads_as_no_consent(
        self, client: Client, bot_user: BotUser, nutrition, goals
    ):
        with patch("apps.consent.services.has_global_consent", side_effect=RuntimeError("down")):
            body = _get(client, bot_user).json()

        assert body["consent_required"] is True
        nutrition.daily_summary.assert_not_called()


class TestConsentKeepsTheDiary:
    def test_with_consent_the_diary_is_read_and_consent_required_is_absent(
        self, client: Client, bot_user: BotUser, nutrition, goals
    ):
        with patch(CONSENT, return_value=True):
            body = _get(client, bot_user).json()

        assert "consent_required" not in body
        assert body["calories_eaten"] == 1240
        assert "water_glasses_eaten" in body
        assert body["entries"] == []
        nutrition.daily_summary.assert_called_once()
        nutrition.get_water_today.assert_called_once()

    def test_the_gate_is_the_chat_predicate(
        self, client: Client, bot_user: BotUser, nutrition, goals
    ):
        """Одно правило на чат и Mini App: ворота зовут тот же предикат."""
        seen: list[object] = []

        def _predicate(user):
            seen.append(user)
            return False

        with patch(CONSENT, side_effect=_predicate):
            _get(client, bot_user)

        assert len(seen) == 1
        assert getattr(seen[0], "pk", None) == bot_user.pk


def test_no_goal_doc_is_the_default_shape_elsewhere():
    """Страховка импорта: общие фикстуры файла-соседа на месте."""
    assert _no_goal_doc()["known"]["goal"] is None
