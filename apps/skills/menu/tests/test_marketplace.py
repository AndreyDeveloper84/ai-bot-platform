"""Состав меню витрины — построители в чистом виде (DRF-1491).

Здесь проверяется то, что можно проверить без канала: КАКИЕ пункты
попадают в клавиатуру при каждой комбинации настроек и согласия, и
совпадает ли перечень в тексте с тем, что человек увидит кнопками.
Маршрутизация тапа проверяется отдельно и через настоящий вход —
``apps/channels/tests/test_marketplace_menu_drf1491.py``.

Правило контура (DRF-1411): каждое отрицание («пищевых пунктов нет»)
стоит рядом с положительным утверждением на ТЕХ ЖЕ данных («ботовые
пункты есть»). Иначе тест зеленел бы на пустой клавиатуре — то есть на
поломке, ради которой он и написан.
"""

from __future__ import annotations

import pytest

from apps.skills.menu import marketplace
from apps.skills.menu.marketplace import (
    BOT_ITEMS,
    CALLBACK_HEALTH_DECLINE,
    CALLBACK_HEALTH_NEED_PREFIX,
    MINIAPP_ITEMS,
    NUTRITION_ITEMS,
    health_need_surface,
    health_request_action_data,
    marketplace_fallback_reply,
    marketplace_menu_reply,
    matches_menu_request,
)


class _StubUser:
    """Достаточно для построителей: они читают только согласие."""

    id = "stub-bot-user"


@pytest.fixture
def bot_user() -> _StubUser:
    return _StubUser()


@pytest.fixture
def consent(monkeypatch):
    """Переключатель согласия ``HEALTH`` без похода в базу.

    Подменяется ``apps.consent.health.is_granted`` — ровно тот предикат,
    который зовёт ``marketplace.health_granted`` ленивым импортом, — а не
    сама ``health_granted``: иначе тест проверял бы собственную заглушку
    вместо связи меню со сторожем согласия.
    """

    def _set(granted: bool) -> None:
        monkeypatch.setattr("apps.consent.health.is_granted", lambda _bot_user: granted)

    _set(False)
    return _set


@pytest.fixture
def miniapp(settings):
    settings.MAX_BOT_WEB_APP = "aylabot"
    settings.MAX_MINIAPP_URL = ""
    return settings


def _payloads(buttons: list[dict]) -> list[str]:
    """Только ботовые payload'ы — у экранных кнопок ``callback`` это слаг."""
    return [b["callback"] for b in buttons if "web_app" not in b and "url" not in b]


def _labels(buttons: list[dict]) -> list[str]:
    return [b["label"] for b in buttons]


# --------------------------------------------------------------------------- #
# 1. Смешанный состав: где отвечает каждый пункт                               #
# --------------------------------------------------------------------------- #
class TestMixedComposition:
    """Разговорное — в боте, экранное — в мини-приложении (§25)."""

    @pytest.mark.django_db
    def test_bot_items_ship_regardless_of_config(self, bot_user, consent, settings):
        settings.MAX_BOT_WEB_APP = ""
        settings.MAX_MINIAPP_URL = ""
        _, data = marketplace_menu_reply(bot_user=bot_user)

        payloads = _payloads(data["buttons"])
        for item in BOT_ITEMS:
            assert item.callback in payloads, payloads

    @pytest.mark.django_db
    def test_screen_items_are_open_app_when_web_app_is_set(self, bot_user, consent, miniapp):
        _, data = marketplace_menu_reply(bot_user=bot_user)

        screen = {b["label"]: b for b in data["buttons"] if b.get("web_app")}
        # Положительная стража на тех же данных: экранные кнопки вообще есть.
        assert screen, data["buttons"]
        for item in MINIAPP_ITEMS:
            assert screen[item.label]["callback"] == item.callback
            assert screen[item.label]["web_app"] == "aylabot"

    @pytest.mark.django_db
    def test_screen_items_degrade_to_links_without_web_app(self, bot_user, consent, settings):
        settings.MAX_BOT_WEB_APP = ""
        settings.MAX_MINIAPP_URL = "https://miniapp.example"
        _, data = marketplace_menu_reply(bot_user=bot_user)

        links = {b["label"]: b["url"] for b in data["buttons"] if b.get("url")}
        assert links, data["buttons"]
        assert links["👤 Профиль"] == "https://miniapp.example/customer/profile"
        assert links["🗓 История визитов"] == "https://miniapp.example/customer/records"


# --------------------------------------------------------------------------- #
# 2. Вырождение: без мини-приложения меню остаётся рабочим                     #
# --------------------------------------------------------------------------- #
class TestZeroConfigDegradation:
    """Нет ``web_app`` и нет ``miniapp_url`` — ботовая часть живёт."""

    @pytest.mark.django_db
    def test_no_screen_buttons_but_bot_half_still_works(self, bot_user, consent, settings):
        settings.MAX_BOT_WEB_APP = ""
        settings.MAX_MINIAPP_URL = ""
        settings.NUTRITION_ENABLED = True
        text, data = marketplace_menu_reply(bot_user=bot_user)

        buttons = data["buttons"]
        # Положительная стража: клавиатура не пуста и это ботовые пункты.
        assert len(buttons) == len(BOT_ITEMS), buttons
        assert _payloads(buttons) == [item.callback for item in BOT_ITEMS]
        assert all("web_app" not in b and "url" not in b for b in buttons), buttons
        # Положительная стража на ТЕКСТЕ, прежде чем что-то в нём отрицать:
        # перечень ботовой половины собран и в тексте стоит.
        assert "Вот что можно прямо здесь" in text
        assert BOT_ITEMS[0].line in text
        # И только теперь отрицание: экранов, которых не открыть, не обещано.
        assert "открывается отдельным экраном" not in text
        assert "профиль" not in text.lower()

    @pytest.mark.django_db
    def test_text_promises_screens_only_when_they_are_drawn(self, bot_user, consent, miniapp):
        text, data = marketplace_menu_reply(bot_user=bot_user)

        assert any(b.get("web_app") for b in data["buttons"]), data["buttons"]
        assert "открывается отдельным экраном" in text
        for item in MINIAPP_ITEMS:
            assert item.line in text, item.line


# --------------------------------------------------------------------------- #
# 3. Питание: двое ворот                                                       #
# --------------------------------------------------------------------------- #
class TestNutritionGates:
    """Таблица §25 п.6 — построчно."""

    @pytest.mark.django_db
    def test_flag_unset_hides_nutrition_entirely(self, bot_user, consent, miniapp, settings):
        settings.NUTRITION_ENABLED = False
        text, data = marketplace_menu_reply(bot_user=bot_user)

        labels = _labels(data["buttons"])
        # Стража: меню построено и экранные пункты в нём есть.
        assert "👤 Профиль" in labels, labels
        for item in NUTRITION_ITEMS:
            assert item.label not in labels, labels
            assert item.line not in text

    @pytest.mark.django_db
    def test_flag_set_without_consent_shows_items_pointing_at_the_request(
        self, bot_user, consent, miniapp, settings
    ):
        settings.NUTRITION_ENABLED = True
        consent(False)
        _, data = marketplace_menu_reply(bot_user=bot_user)

        by_label = {b["label"]: b for b in data["buttons"]}
        for item in NUTRITION_ITEMS:
            button = by_label[item.label]
            assert button["callback"].startswith(CALLBACK_HEALTH_NEED_PREFIX), button
            # Мёртвой кнопки нет и в поверхность тап не ведёт.
            assert "web_app" not in button and "url" not in button, button

    @pytest.mark.django_db
    def test_flag_set_with_consent_opens_the_surface(self, bot_user, consent, miniapp, settings):
        settings.NUTRITION_ENABLED = True
        consent(True)
        _, data = marketplace_menu_reply(bot_user=bot_user)

        by_label = {b["label"]: b for b in data["buttons"]}
        for item in NUTRITION_ITEMS:
            button = by_label[item.label]
            assert button["web_app"] == "aylabot", button
            assert button["callback"] == item.callback, button

    @pytest.mark.django_db
    def test_consent_read_failure_is_fail_closed(self, bot_user, monkeypatch, miniapp, settings):
        """Сбой чтения согласия — это «согласия нет», а не «есть»."""
        settings.NUTRITION_ENABLED = True

        def _boom(_bot_user):
            raise RuntimeError("база отвалилась")

        monkeypatch.setattr("apps.consent.health.is_granted", _boom)
        _, data = marketplace_menu_reply(bot_user=bot_user)

        by_label = {b["label"]: b for b in data["buttons"]}
        assert "📸 Сканер еды" in by_label, list(by_label)
        assert by_label["📸 Сканер еды"]["callback"].startswith(CALLBACK_HEALTH_NEED_PREFIX)


# --------------------------------------------------------------------------- #
# 4. Экран запроса согласия                                                    #
# --------------------------------------------------------------------------- #
class TestHealthConsentRequestScreen:
    @pytest.mark.django_db
    def test_offers_the_place_where_consent_is_granted_and_a_way_out(self, miniapp):
        data = health_request_action_data()

        labels = _labels(data["buttons"])
        assert labels, data
        grant = [b for b in data["buttons"] if b.get("web_app")]
        assert grant and grant[0]["callback"] == "open_profile", data["buttons"]
        assert CALLBACK_HEALTH_DECLINE in _payloads(data["buttons"])

    @pytest.mark.django_db
    def test_way_out_survives_a_missing_miniapp(self, settings):
        settings.MAX_BOT_WEB_APP = ""
        settings.MAX_MINIAPP_URL = ""
        data = health_request_action_data()

        # Стража: клавиатура вообще есть — и в ней ровно выход.
        assert len(data["buttons"]) == 1, data["buttons"]
        assert data["buttons"][0]["callback"] == CALLBACK_HEALTH_DECLINE

    def test_unknown_surface_slug_is_not_a_consent_request(self):
        assert health_need_surface(f"{CALLBACK_HEALTH_NEED_PREFIX}food_scan") == "food_scan"
        assert health_need_surface(f"{CALLBACK_HEALTH_NEED_PREFIX}снятый_пункт") is None
        assert health_need_surface("cb:menu:book") is None


# --------------------------------------------------------------------------- #
# 5. Распознавание просьбы показать меню                                       #
# --------------------------------------------------------------------------- #
class TestMenuRequestMatcher:
    @pytest.mark.parametrize(
        "text",
        [
            "что ты умеешь?",
            "Что ты умеешь",
            "Что ты умеешь!!!",
            "помощь",
            "Помощь.",
            "меню",
            "/help",
            "/menu",
            "чем можешь помочь",
        ],
    )
    def test_claims_whole_message_requests(self, text):
        assert matches_menu_request(text) is True

    @pytest.mark.parametrize(
        "text",
        [
            "помоги выбрать массаж",
            "а ещё что ты умеешь по маникюру?",
            "нужна помощь с записью",
            "хочу записаться",
            "",
            "   ",
        ],
    )
    def test_leaves_everything_else_to_the_concierge(self, text):
        assert matches_menu_request(text) is False


# --------------------------------------------------------------------------- #
# 6. Ветка «не поняла» — та же клавиатура, другая рамка                        #
# --------------------------------------------------------------------------- #
class TestFallbackScreen:
    @pytest.mark.django_db
    def test_same_keyboard_different_opening_line(self, bot_user, consent, miniapp):
        menu_text, menu_data = marketplace_menu_reply(bot_user=bot_user)
        fb_text, fb_data = marketplace_fallback_reply(bot_user=bot_user)

        assert fb_data["buttons"] == menu_data["buttons"], fb_data["buttons"]
        assert fb_text != menu_text
        assert fb_text.startswith("Я пока не поняла")

    @pytest.mark.django_db
    def test_never_echoes_the_user(self, bot_user, consent, miniapp):
        fb_text, _ = marketplace_fallback_reply(bot_user=bot_user)
        assert "ааааа" not in fb_text


# --------------------------------------------------------------------------- #
# 7. Своя копия, а не салонная                                                 #
# --------------------------------------------------------------------------- #
class TestMarketplaceCopyIsItsOwn:
    """§25 п.1: витрине заводим СВОЁ меню, салонное не переименовываем."""

    @pytest.mark.django_db
    def test_does_not_name_the_pilot_salon(self, bot_user, consent, miniapp):
        from apps.skills.menu.replies import FALLBACK_TEXT, HELP_TEXT

        menu_text, _ = marketplace_menu_reply(bot_user=bot_user)
        # Стража: салонная копия на месте и по-прежнему про салон.
        assert "Формула тела" in HELP_TEXT
        assert menu_text != HELP_TEXT
        assert menu_text != FALLBACK_TEXT
        assert "Формула тела" not in menu_text

    def test_every_item_line_is_covered_by_a_button(self):
        """Строка перечня и кнопка живут одной записью — вместе или никак."""
        for item in BOT_ITEMS + MINIAPP_ITEMS + NUTRITION_ITEMS:
            assert item.label.strip(), item
            assert item.line.strip(), item
            assert item.where in {"bot", "miniapp"}, item

    def test_nutrition_surface_slugs_are_max_payload_safe(self):
        """MAX отвечает 400 на payload вне ``[A-Za-z0-9_-]``."""
        from apps.channels.max.outbound import OPEN_APP_PAYLOAD_RE

        for item in MINIAPP_ITEMS + NUTRITION_ITEMS:
            assert OPEN_APP_PAYLOAD_RE.fullmatch(item.callback), item

    def test_screen_slugs_are_declared_routes(self):
        """Слаг без маршрута — дедлинк в SPA-заглушку, а не кнопка."""
        from apps.skills.welcome.skill import MINIAPP_ROUTES

        for item in MINIAPP_ITEMS + NUTRITION_ITEMS:
            assert item.callback in MINIAPP_ROUTES, item.callback


def test_module_exposes_the_action_types_the_handler_records():
    """Метки хода — часть контракта с аналитикой, а не украшение."""
    assert marketplace.MENU_ACTION_TYPE == "marketplace_menu"
    assert marketplace.FALLBACK_ACTION_TYPE == "marketplace_fallback"
    # ``Message.action_type`` — CharField(max_length=32).
    for value in (
        marketplace.MENU_ACTION_TYPE,
        marketplace.FALLBACK_ACTION_TYPE,
        marketplace.HEALTH_DECLINE_ACTION_TYPE,
        marketplace.HEALTH_REQUEST_ACTION_TYPE,
    ):
        assert len(value) <= 32, value
