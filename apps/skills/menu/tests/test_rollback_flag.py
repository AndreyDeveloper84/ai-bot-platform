"""``PILOT_CONVERSATIONAL_UX`` rollback switch (DRF-963).

The change claims 100% of non-empty text turns on both channels and swaps
the welcome keyboard, so it needs a way back that isn't a redeploy. OFF
must restore the pre-DRF-963 behaviour exactly — including NOT shipping
``cb:menu:*`` buttons that would be dead once MenuSkill stands down.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from apps.skills.base import SkillContext
from apps.skills.menu.help_skill import HelpSkill
from apps.skills.menu.skill import MenuSkill
from apps.skills.welcome.skill import WelcomeSkill


def _ctx(text: str) -> SkillContext:
    conversation = MagicMock()
    conversation.tenant = None
    return SkillContext(
        conversation=conversation,
        bot_user=MagicMock(),
        message_text=text,
    )


@pytest.fixture
def flag_off(settings):
    settings.PILOT_CONVERSATIONAL_UX = False
    return settings


@pytest.fixture
def flag_on(settings):
    settings.PILOT_CONVERSATIONAL_UX = True
    return settings


class TestSkillsStandDown:
    def test_menu_yields_text_back_to_echo(self, flag_off):
        assert MenuSkill().matches(_ctx("Хочу массаж")) is False
        assert MenuSkill().matches(_ctx("ыаывпаып")) is False

    def test_help_yields_back_to_faq(self, flag_off):
        assert HelpSkill().matches(_ctx("что ты умеешь")) is False
        assert HelpSkill().matches(_ctx("cb:menu:help")) is False

    def test_both_claim_again_when_on(self, flag_on):
        assert MenuSkill().matches(_ctx("Хочу массаж")) is True
        assert HelpSkill().matches(_ctx("что ты умеешь")) is True


class TestWelcomeKeyboardReverts:
    def test_off_restores_the_mini_app_trio_and_drops_help(self, flag_off, settings):
        settings.MAX_BOT_WEB_APP = "id583_bot"
        settings.MAX_MINIAPP_URL = ""
        buttons = WelcomeSkill().handle(_ctx("/start")).action_data["buttons"]

        callbacks = [b.get("callback") for b in buttons]
        assert callbacks[:3] == ["open_catalog", "open_visits", "open_profile"]
        assert not any(str(c).startswith("cb:menu:") for c in callbacks)

    def test_off_keyboard_is_frozen_at_its_pre_drf963_shape(self, flag_off, settings):
        """Известное и осознанное исключение из потолка AC-4.2 (DRF-1200).

        DRF-1200 сократил живой первый экран до пяти кнопок, и
        ``TestCanonQuickActionCeilingAC42`` сторожит эту границу. Ветка
        отката её НЕ соблюдает: она отдаёт семь кнопок, потому что её
        работа — вернуть до-DRF-963 клавиатуру буквально, а не «канон
        минус DRF-963». Откат, который отдаёт третью, свою собственную
        клавиатуру — не откат.

        Отсюда следует: ``PILOT_CONVERSATIONAL_UX=False`` в проде
        возвращает и нарушение AC-4.2 тоже. Это аварийный рычаг, а не
        режим работы; здесь состав заморожен поимённо, чтобы ветка не
        могла тихо разрастись сверх того, что она обязана восстановить.
        """
        settings.MAX_BOT_WEB_APP = "id583_bot"
        settings.MAX_MINIAPP_URL = ""
        buttons = WelcomeSkill().handle(_ctx("/start")).action_data["buttons"]
        assert [b["label"] for b in buttons] == [
            "📅 Записаться",
            "📋 Мои визиты",
            "👤 Профиль",
            "🍽 Дневник еды",
            "💧 Вода",
            "❓ Задать вопрос",
            "▶️ Начать",
        ]

    def test_off_never_ships_a_dead_menu_button(self, flag_off, settings):
        """With MenuSkill standing down, a cb:menu:* button would do
        nothing — worse than the bug being rolled back."""
        for web_app, miniapp_url in (("", ""), ("id583_bot", ""), ("", "https://m.example/")):
            settings.MAX_BOT_WEB_APP = web_app
            settings.MAX_MINIAPP_URL = miniapp_url
            buttons = WelcomeSkill().handle(_ctx("/start")).action_data["buttons"]
            assert not any(str(b.get("callback", "")).startswith("cb:menu:") for b in buttons)

    def test_on_ships_the_bot_native_menu(self, flag_on, settings):
        settings.MAX_BOT_WEB_APP = "id583_bot"
        settings.MAX_MINIAPP_URL = ""
        buttons = WelcomeSkill().handle(_ctx("/start")).action_data["buttons"]
        callbacks = {b.get("callback") for b in buttons}
        assert {"cb:menu:book", "cb:menu:my_bookings", "cb:menu:help"} <= callbacks


class TestDefault:
    def test_ships_enabled(self):
        """The feature is meant to be live; the flag exists for rollback."""
        from django.conf import settings as django_settings

        assert getattr(django_settings, "PILOT_CONVERSATIONAL_UX", None) is True
