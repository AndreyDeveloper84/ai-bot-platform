"""Отказ «нет согласия дневника» в чате несёт кнопку на экран согласия (DRF-2096).

До этого листа три пути чата — вода, еда текстом, фото — отказывали одним
предикатом (``apps.consent.diary_gate``, DRF-2093) и одним текстом «открой
Mini App и подтверди», а открыть было нечем. Теперь под текстом одна
``open_app``-кнопка на ``open_food_scan`` — первый кадр сканера без
согласия и есть экран согласия дневника v1 (Z9). Один строитель
(``marketplace.diary_consent_request_action_data``), три вызова; без
настроенного приложения — прежний текст без мёртвой кнопки (§25 п.6).

Согласия — настоящие строки реестра (выдано → отозвано), не подменённый
предикат: узел «кнопка есть» ничего не стоил бы на пути, где отказ вообще
не случается.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from apps.consent.models import ConsentRecord
from apps.consent.nutrition import (
    FOOD_DIARY_CONSENT_DOCUMENT_VERSION,
    diary_is_granted,
    grant_diary,
    withdraw_diary,
)
from apps.consent.services import record_person_consent
from apps.identity.models import BotUser
from apps.skills.base import SkillContext, SkillResult
from apps.skills.food_clarify import text_entry
from apps.skills.menu import marketplace
from apps.tenancy.models import Tenant

WEB_APP = "id583_bot"
SLUG = "open_food_scan"


@pytest.fixture(autouse=True)
def _settings(settings):
    settings.NUTRITION_ENABLED = True
    settings.FOOD_PHOTO_SCAN_ENABLED = True
    settings.MAX_BOT_TENANT_SLUG = "diary-2096"
    settings.MAX_BOT_WEB_APP = WEB_APP
    settings.MAX_MINIAPP_URL = ""


@pytest.fixture
def bot_user(db) -> BotUser:
    tenant = Tenant.objects.create(slug="diary-2096", name="Diary 2096", timezone="Europe/Moscow")
    user = BotUser.all_tenants.create(
        tenant=tenant, channel="max", channel_user_id="92096", display_name="Клиент"
    )
    record_person_consent(
        user, consent_type=ConsentRecord.ConsentType.PERSONAL_DATA.value, source="test:2096"
    )
    # Выдано и отозвано по-настоящему — отказ ниже только про реестр дневника.
    grant_diary(user, document_version=FOOD_DIARY_CONSENT_DOCUMENT_VERSION)
    assert withdraw_diary(user) >= 1
    assert diary_is_granted(user) is False
    return user


def _ctx(bot_user: BotUser, text: str) -> SkillContext:
    return SkillContext(
        conversation=SimpleNamespace(id="conv-2096", skill_state={}, last_photo_bytes=b"jpeg"),  # type: ignore[arg-type]
        bot_user=bot_user,
        message_text=text,
    )


def _water(bot_user: BotUser) -> SkillResult:
    from apps.skills.water.skill import WaterSkill

    with patch("apps.skills.water.skill.get_nutrition_client", return_value=AsyncMock()):
        return WaterSkill().handle(_ctx(bot_user, "стакан воды"))


def _text(bot_user: BotUser) -> SkillResult:
    with patch(
        "apps.skills.food_clarify.text_entry.get_nutrition_client", return_value=AsyncMock()
    ):
        result = text_entry.show_estimate(_ctx(bot_user, "борщ 300г"), "борщ", 300, corrected=False)
    assert result is not None
    return result


def _photo(bot_user: BotUser) -> SkillResult:
    from apps.skills.food_scanner import skill as scanner

    result = scanner._check_gates(_ctx(bot_user, ""), kind="photo", require_photo_scan=False)
    assert result is not None
    return result


PATHS = [
    ("water", _water, "water_diary_consent_required"),
    ("text", _text, "food_text_diary_consent_required"),
    ("photo", _photo, "food_scanner_consent_required"),
]


def _open_app_buttons(result: SkillResult) -> list[dict]:
    buttons = (result.action_data or {}).get("buttons", [])
    return [b for b in buttons if b.get("web_app")]


@pytest.mark.parametrize(("path", "run", "kind"), PATHS, ids=[p[0] for p in PATHS])
class TestTheButtonOnAllThreePaths:
    def test_the_refusal_carries_one_open_app_button_to_the_consent_screen(
        self, bot_user, path, run, kind
    ) -> None:
        result = run(bot_user)
        buttons = _open_app_buttons(result)
        assert len(buttons) == 1, result.action_data
        assert buttons[0]["callback"] == SLUG
        assert buttons[0]["web_app"] == WEB_APP
        assert buttons[0]["label"] == marketplace.DIARY_CONSENT_OPEN_LABEL
        assert result.reply_text == text_entry.DIARY_CONSENT_REQUIRED_WITH_BUTTON_TEXT
        assert "кнопку ниже" in result.reply_text

    def test_the_reply_kind_is_unchanged(self, bot_user, path, run, kind) -> None:
        """M2+ recovery ключуется на reply_kind — он прежний."""

        assert run(bot_user).meta == {"reply_kind": kind}

    def test_no_personal_data_button_rides_along(self, bot_user, path, run, kind) -> None:
        """Кнопка DRF-1968 выдаёт PERSONAL_DATA и вернула бы к тому же отказу."""

        result = run(bot_user)
        callbacks = [b.get("callback", "") for b in (result.action_data or {}).get("buttons", [])]
        assert callbacks, "the refusal carries buttons — see the node above"
        assert not [c for c in callbacks if c.startswith("cb:welcome:consent")]

    def test_without_a_configured_mini_app_the_text_stays_and_no_dead_button(
        self, bot_user, settings, path, run, kind
    ) -> None:
        settings.MAX_BOT_WEB_APP = ""
        settings.MAX_MINIAPP_URL = ""
        result = run(bot_user)
        assert result.action_data is None
        assert result.reply_text == text_entry.DIARY_CONSENT_REQUIRED_TEXT
        assert "кнопку" not in result.reply_text

    def test_the_slug_is_a_registered_mini_app_route(self, bot_user, path, run, kind) -> None:
        """DRF-1167 — бот строит кнопки только по существующим маршрутам."""

        from apps.skills.welcome.skill import MINIAPP_ROUTES

        for button in _open_app_buttons(run(bot_user)):
            assert button["callback"] in MINIAPP_ROUTES
        assert MINIAPP_ROUTES[SLUG] == "customer/food-scanner/capture"


class TestOneBuilder:
    def test_all_three_paths_call_the_same_builder(self, bot_user) -> None:
        with patch.object(
            marketplace,
            "diary_consent_request_action_data",
            wraps=marketplace.diary_consent_request_action_data,
        ) as builder:
            for _, run, _ in PATHS:
                run(bot_user)
        assert builder.call_count == 3

    def test_the_link_fallback_ladder_is_the_menus(self, settings) -> None:
        """Нет web_app, есть URL приложения → внешняя ссылка, как у меню."""

        settings.MAX_BOT_WEB_APP = ""
        settings.MAX_MINIAPP_URL = "https://mini.example"
        data = marketplace.diary_consent_request_action_data()
        assert data is not None
        (button,) = data["buttons"]
        # Ссылка ведёт на МАРШРУТ слага (welcome._miniapp_url резолвит его по
        # MINIAPP_ROUTES), не на сам слаг — тот же экран согласия.
        assert button["url"] == "https://mini.example/customer/food-scanner/capture"
        assert "web_app" not in button
        assert data["kind"] == marketplace.DIARY_CONSENT_REQUEST_KIND

    def test_the_mini_app_route_map_knows_the_slug(self) -> None:
        """Вторая половина реестра — `max-sdk.ts::_ROUTE_MAP` (парность DRF-1167)."""

        from pathlib import Path

        ts = (
            Path(__file__).resolve().parents[4] / "apps" / "miniapp" / "src" / "lib" / "max-sdk.ts"
        ).read_text(encoding="utf-8")
        assert f'{SLUG}: "/customer/food-scanner/capture"' in ts
