"""Состав меню витрины — построители в чистом виде (DRF-1491, DRF-1547).

Здесь проверяется то, что можно проверить без канала: КАКИЕ пункты
попадают в клавиатуру при каждой комбинации настроек и согласия, и
совпадает ли перечень в тексте с тем, что человек увидит кнопками.
Маршрутизация тапа проверяется отдельно и через настоящий вход —
``apps/channels/tests/test_marketplace_menu_drf1491.py``.

Правило контура (DRF-1411): каждое отрицание («этого пункта в главном
меню нет») стоит рядом с положительным утверждением на ТЕХ ЖЕ данных
(«а вот здесь он есть»). Иначе «починка», отнимающая способность, была
бы зелёной — см. :class:`TestRemovedActionsStayReachable`.
"""

from __future__ import annotations

import pytest

from apps.skills.menu import marketplace
from apps.skills.menu.marketplace import (
    CALLBACK_EXTRA_BACK,
    CALLBACK_EXTRA_HELP,
    CALLBACK_EXTRA_OPEN,
    CALLBACK_HEALTH_DECLINE,
    CALLBACK_HEALTH_NEED_PREFIX,
    DIARY_TAP_TEXT,
    DISCOVER_TAP_TEXT,
    MAIN_ITEMS,
    NUTRITION_ITEMS,
    OPEN_CALLBACK_PREFIX,
    MenuItem,
    health_need_surface,
    health_request_action_data,
    health_request_action_type,
    is_extra_callback,
    is_open_callback,
    marketplace_fallback_reply,
    marketplace_menu_reply,
    matches_menu_request,
    open_callback_slug,
    open_warning_reply,
)

#: Главное меню при ЗАКРЫТЫХ воротах питания — семь пунктов в порядке
#: владельца (§37 + OD-UI-2) и payload'ы, которые они шлют. Порядок
#: load-bearing: раскладка получается из него и ``button_columns=2``, и
#: перестановка переставляет пары.
#:
#: Последней стоит «Помощь»: OD-UI-2 («Ещё убираем, помощь в главное
#: меню») снёс подменю и поставил её на место «Ещё».
_SEVEN_MAIN: tuple[tuple[str, str], ...] = (
    ("Подобрать услугу", DISCOVER_TAP_TEXT),
    ("Найти салон", "cb:catalog:salons"),
    ("📅 Записаться", "cb:menu:book"),
    ("📋 Мои записи", "cb:menu:my_bookings"),
    ("Моя цель", "cb:open:goal_select"),
    ("Профиль", "cb:open:profile"),
    ("Помощь", CALLBACK_EXTRA_HELP),
)

#: Главное меню при ОТКРЫТЫХ воротах питания и БЕЗ согласия: пищевой пункт
#: есть, стоит седьмым и ведёт на ЗАПРОС согласия (вторая строка таблицы
#: §25 п.6). Восемь кнопок вместо семи — единственная разница.
_EIGHT_WITHOUT_CONSENT: tuple[tuple[str, str], ...] = (
    *_SEVEN_MAIN[:6],
    ("🥗 Дневник питания", f"{CALLBACK_HEALTH_NEED_PREFIX}food_diary"),
    ("Помощь", CALLBACK_EXTRA_HELP),
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


@pytest.fixture
def nutrition_on(settings):
    settings.NUTRITION_ENABLED = True
    return settings


def _payloads(buttons: list[dict]) -> list[str]:
    return [b["callback"] for b in buttons]


def _labels(buttons: list[dict]) -> list[str]:
    return [b["label"] for b in buttons]


def _pairs(buttons: list[dict]) -> list[tuple[str, str]]:
    return [(b["label"], b.get("callback", "")) for b in buttons]


# --------------------------------------------------------------------------- #
# 1. Семь кнопок владельца — состав и порядок                                  #
# --------------------------------------------------------------------------- #
class TestSevenButtons:
    """§37: семь пунктов, раскладка 2×3+1, «Ещё» замыкает."""

    def test_composition_and_order_are_the_owner_s(self, bot_user, consent, miniapp):
        _text, data = marketplace_menu_reply(bot_user=bot_user)
        assert _pairs(data["buttons"]) == list(_SEVEN_MAIN)

    def test_two_columns_make_the_2x3_plus_1_layout(self, bot_user, consent, miniapp):
        _text, data = marketplace_menu_reply(bot_user=bot_user)
        assert data["button_columns"] == 2
        assert len(data["buttons"]) == 7, "семь при пределе пять — сознательно (§37)"

    def test_heading_is_the_owner_s_question(self, bot_user, consent, miniapp):
        text, _data = marketplace_menu_reply(bot_user=bot_user)
        assert text.startswith("Что хотите сделать?")

    def test_nothing_in_the_main_menu_opens_the_app_directly(self, bot_user, consent, miniapp):
        """Предупреждение §37 п.6 было бы невозможно, если бы открывало сразу.

        ``open_app`` на MAX открывает приложение мгновенно: сказать
        что-либо «перед» можно только своим ходом, а значит в САМОМ меню
        такой кнопки быть не должно ни одной.
        """
        _text, data = marketplace_menu_reply(bot_user=bot_user)
        assert not [b for b in data["buttons"] if "web_app" in b or "url" in b]


# --------------------------------------------------------------------------- #
# 2. Эмодзи                                                                    #
# --------------------------------------------------------------------------- #
class TestEmojiStayWithinReason:
    """Решение владельца 07.09.2026 — ПЕРЕСМОТР §37 п.7, не починка дефекта.

    §37 п.7 запрещал значки во всём этом меню, и прежняя редакция этого
    класса (``TestLabelsCarryNoEmoji``) это и сторожила. Владелец решение
    пересмотрел («кнопки без эмодзи, выглядит суховато») и выдал новое
    правило, у которого три части:

    * не больше ОДНОГО значка на подпись;
    * только там, где значок РАЗЛИЧАЕТ пункты, а не украшает каждый;
    * подпись читаема без значка — сам по себе он смысла не несёт.

    Класс поэтому переформулирован, а не удалён. Удалить стражу — самый
    дешёвый способ получить зелёный тест, который ничего не проверяет:
    без неё «в разумных пределах» через месяц значило бы значок на каждой
    подписи, и никто бы не заметил.

    Три части правила дают три разные проверки, и ни одна не заменяет
    остальные: «не больше одного» пройдёт на значке у каждого пункта,
    «не у каждого» пройдёт на нуле значков (то есть на прежнем §37 п.7),
    а «читаема без значка» — единственная, которая ловит значок, уехавший
    в реплику человека.
    """

    #: Проверяется КОДОВАЯ ТОЧКА, а не список конкретных значков: список
    #: пропустил бы следующий добавленный.
    @staticmethod
    def _emoji_in(text: str) -> list[str]:
        return [
            ch
            for ch in text
            if 0x1F300 <= ord(ch) <= 0x1FAFF
            or 0x2600 <= ord(ch) <= 0x27BF
            or 0xFE00 <= ord(ch) <= 0xFE0F
        ]

    def _has_emoji(self, text: str) -> bool:
        return bool(self._emoji_in(text))

    def test_the_detector_itself_can_see_an_emoji(self):
        """Стража, которая ничего не ловит, зеленела бы всегда."""
        assert self._emoji_in("📅 Записаться") == ["📅"]
        assert self._emoji_in("Записаться") == []
        assert len(self._emoji_in("📅📋 Записаться")) == 2

    # -- часть 1: не больше одного значка на подпись ----------------------- #

    def test_main_menu_labels_carry_at_most_one_emoji_each(self, bot_user, consent, miniapp):
        _text, data = marketplace_menu_reply(bot_user=bot_user)
        labels = _labels(data["buttons"])
        assert labels, "пустое меню сделало бы проверку значков бессмысленной"
        assert [label for label in labels if len(self._emoji_in(label)) > 1] == []

    def test_the_diary_label_carries_at_most_one_emoji_too(
        self, bot_user, consent, miniapp, nutrition_on
    ):
        """Та же проверка на восьмёрке: пищевой пункт поднялся сюда же."""
        _text, data = marketplace_menu_reply(bot_user=bot_user)
        labels = _labels(data["buttons"])
        assert "🥗 Дневник питания" in labels, labels
        assert [label for label in labels if len(self._emoji_in(label)) > 1] == []

    # -- часть 2: различает, а не украшает каждый -------------------------- #

    def test_main_menu_does_not_decorate_every_label(self, bot_user, consent, miniapp):
        """«Не украшает каждый» — половина правила, и она про БОЛЬШИНСТВО.

        Значок оправдан там, где две подписи иначе не различить. Таких
        мест в семёрке одно (пара «Записаться»/«Мои записи»), поэтому
        подписей БЕЗ значка должно остаться больше, чем со значком.
        """
        _text, data = marketplace_menu_reply(bot_user=bot_user)
        labels = _labels(data["buttons"])
        assert labels, "пустое меню сделало бы проверку значков бессмысленной"
        with_emoji = [label for label in labels if self._has_emoji(label)]
        without = [label for label in labels if not self._has_emoji(label)]
        assert with_emoji, "вторая половина правила: значки владелец ВЕРНУЛ"
        assert len(without) > len(with_emoji)

    def test_the_confusable_pair_is_exactly_where_the_emoji_went(self, bot_user, consent, miniapp):
        """Положительная стража к предыдущей: значки стоят НЕ где попало.

        «Записаться» и «Мои записи» делят корень и стоят соседними
        строками одного столбика — это единственная пара во всём меню,
        которую глаз путает. Если значки однажды переедут на «Профиль» и
        «Моя цель», предыдущий тест этого не заметит, а этот заметит.
        """
        _text, data = marketplace_menu_reply(bot_user=bot_user)
        marked = [label for label in _labels(data["buttons"]) if self._has_emoji(label)]
        assert marked, "значков нет вовсе — это прежний §37 п.7, а не новое решение"
        assert [label.split(" ", 1)[1] for label in marked] == ["Записаться", "Мои записи"]

    def test_the_diary_keeps_its_emoji_after_the_move(
        self, bot_user, consent, miniapp, nutrition_on
    ):
        """Значок переезжает ВМЕСТЕ с пунктом (OD-UI-2).

        Довод значка привязан к первому экрану, а не к подменю, — там
        дневник стоит среди goal-like чипов и рвёт категорию. Подменю
        снесено, довод не тронут, значок на месте.
        """
        _text, data = marketplace_menu_reply(bot_user=bot_user)
        marked = [label for label in _labels(data["buttons"]) if self._has_emoji(label)]
        assert [label.split(" ", 1)[1] for label in marked] == [
            "Записаться",
            "Мои записи",
            "Дневник питания",
        ]

    def test_consent_request_screen_carries_no_emoji(self, miniapp):
        """Экрану согласия различать нечего: там согласие и «Не сейчас».

        Пара противоположных по смыслу подписей — ровно тот случай, где
        значок ничего не добавляет, а «украшает». Проверка отрицательная,
        поэтому рядом стоит присутствие на ТЕХ ЖЕ данных.
        """
        labels = _labels(health_request_action_data()["buttons"])
        assert labels, "экран без кнопок сделал бы проверку значков пустой"
        assert [label for label in labels if self._has_emoji(label)] == []

    # -- часть 3: подпись читаема без значка ------------------------------- #

    def test_no_emoji_reaches_the_dialogue_history_as_a_persons_words(self):
        """Самая дорогая часть правила и единственная не про внешний вид.

        ``cb:health:need:*`` ложится в историю ФРАЗОЙ (``health_tap_text``),
        то есть как реплика человека — и дальше в промпт консьержа. Значок
        там означал бы значок в том, что человек якобы сказал. Устройство
        (``MenuItem.emoji`` отдельным полем) это и обеспечивает; проверка
        здесь на случай, если однажды его сольют обратно в ``label``.
        """
        phrases = list(marketplace.health_tap_text().values())
        assert phrases, "пустая таблица фраз прошла бы любую проверку об их содержимом"
        assert [phrase for phrase in phrases if self._has_emoji(phrase)] == []

    def test_every_marked_label_reads_without_its_emoji(self):
        """«Сам по себе значок смысла не несёт» — проверяемо на полях.

        У пункта со значком ``label`` существует ОТДЕЛЬНО и уже читается:
        не один значок, не значок с пробелом и не пустая строка.
        """
        marked = [item for item in (*MAIN_ITEMS, *NUTRITION_ITEMS) if item.emoji]
        assert marked, "ни одного помеченного пункта — правило нечего проверять"

        plain = [item.label for item in marked]
        assert plain, "подписи помеченных пунктов пусты — отрицание ниже соврало бы"
        assert [label for label in plain if self._has_emoji(label)] == []
        assert [label for label in plain if len(label.strip()) <= 2] == []

        assert [item.button_label for item in marked] == [
            f"{item.emoji} {item.label}" for item in marked
        ]

    def test_the_menu_listing_in_text_carries_no_emoji(self, miniapp):
        """Перечень в тексте строится из ``line``, а не из подписи.

        Значок на кнопке объясняет соседство кнопок; в перечне строк
        соседства нет, и повтор значка был бы тем самым украшением.
        """
        listing = [
            line
            for line in marketplace.marketplace_menu_text().splitlines()
            if line.startswith("•")
        ]
        assert listing, "перечень пуст — проверять в нём нечего"
        assert [line for line in listing if self._has_emoji(line)] == []


# --------------------------------------------------------------------------- #
# 3. Снос подменю «Ещё» — OD-UI-2                                              #
# --------------------------------------------------------------------------- #
class TestExtraMenuIsGone:
    """Владелец дословно: «Ещё убираем, помощь в главное меню».

    Правило DRF-1411 соблюдено буквально: рядом с каждым «этого нет»
    стоит «а вот это есть» на ТЕХ ЖЕ данных. Подменю снесено, но ни одна
    способность из него не пропала — обе поднялись в главное меню.
    """

    def test_the_submenu_builder_is_gone_for_good(self):
        """Экрана нет — значит нет и построителя.

        Оставленный «на всякий случай» построитель это мёртвый код,
        который следующий читатель примет за живую поверхность.
        """
        # Стража НА ТЕХ ЖЕ данных: модуль импортирован и построители в нём
        # есть — иначе «ничего не нашлось» зеленело бы на опечатке в имени
        # модуля, а не доказывало снос подменю.
        assert hasattr(marketplace, "marketplace_menu_reply")
        assert hasattr(marketplace, "marketplace_menu_buttons")
        assert hasattr(marketplace, "main_items")
        # И только теперь отрицания.
        assert not hasattr(marketplace, "marketplace_extra_reply")
        assert not hasattr(marketplace, "marketplace_extra_buttons")
        assert not hasattr(marketplace, "extra_items")

    def test_the_main_menu_has_no_way_in_and_no_way_back(
        self, bot_user, consent, miniapp, nutrition_on
    ):
        payloads = _payloads(marketplace_menu_reply(bot_user=bot_user)[1]["buttons"])
        # Стража ВПЕРЕДИ отрицания: меню построено и полно.
        assert DISCOVER_TAP_TEXT in payloads, payloads
        assert CALLBACK_EXTRA_HELP in payloads, payloads
        # И только теперь отрицания: входить некуда, возвращаться неоткуда.
        assert CALLBACK_EXTRA_OPEN not in payloads, payloads
        assert CALLBACK_EXTRA_BACK not in payloads, payloads

    def test_the_composition_is_the_owner_s_eight(self, bot_user, consent, miniapp, nutrition_on):
        """Восемь при открытых воротах питания, в порядке владельца."""
        _text, data = marketplace_menu_reply(bot_user=bot_user)
        assert _pairs(data["buttons"]) == list(_EIGHT_WITHOUT_CONSENT)

    def test_two_columns_survive_the_merge(self, bot_user, consent, miniapp, nutrition_on):
        """Подменю рисовалось столбиком; главное меню — парами, и осталось."""
        _text, data = marketplace_menu_reply(bot_user=bot_user)
        assert data["button_columns"] == 2

    def test_help_keeps_its_own_payload(self, bot_user, consent, miniapp, nutrition_on):
        """Payload «Помощи» не слился ни с чьим при переезде.

        Он был отдельным от «Назад» ради измеримости «сколько людей просят
        помощи»; «Назад» больше нет, но повод остался: слить «Помощь» с
        любым соседом значит потерять тот же счётчик.
        """
        payloads = _payloads(marketplace_menu_reply(bot_user=bot_user)[1]["buttons"])
        assert payloads.count(CALLBACK_EXTRA_HELP) == 1, payloads

    def test_help_did_not_move_into_the_cb_menu_family(self):
        """Наивный переезд в ``cb:menu:help`` дал бы прозу вместо меню.

        ``resolve_tap_text`` перехватывает ВЕСЬ ``cb:menu:*`` выше
        лестницы обработчика, а ``_global_menu_text`` знает для ``help``
        фразу «Что ты умеешь?» — она уехала бы консьержу. Проверяется не
        строка в константе, а сам перехват: тот же вызов, что стоит на
        живом пути.
        """
        from apps.channels.max.quick_actions import resolve_tap_text
        from apps.skills.menu.matching import CALLBACK_MENU_HELP

        # Стража: перехват РАБОТАЕТ и «cb:menu:help» действительно съеден.
        assert resolve_tap_text(CALLBACK_MENU_HELP) == "Что ты умеешь?"
        # И только теперь — payload «Помощи» мимо него не проходит.
        assert not CALLBACK_EXTRA_HELP.startswith("cb:menu:")
        assert resolve_tap_text(CALLBACK_EXTRA_HELP) is None
        assert is_extra_callback(CALLBACK_EXTRA_HELP)

    def test_the_retired_payloads_are_still_recognised(self):
        """Клавиатуры живут в истории чата дольше кода.

        Кнопок «Ещё» и «Назад» больше не рисуют, но вчерашние остались у
        людей в переписке. Перестань разбирать их форму — и тап уедет
        модели сырым payload'ом (DRF-1051) или утонет в молчании.
        """
        assert is_extra_callback(CALLBACK_EXTRA_OPEN)
        assert is_extra_callback(CALLBACK_EXTRA_BACK)
        assert not is_extra_callback("cb:menu:book")

    def test_a_typed_lookalike_is_not_a_tap(self):
        """Разбор по ФОРМЕ, а не по префиксу — правило C01."""
        assert not is_extra_callback("cb:extra: что это?")
        assert not is_open_callback("cb:open: что это?")


# --------------------------------------------------------------------------- #
# 4. Предупреждение перед открытием приложения                                 #
# --------------------------------------------------------------------------- #
class TestWarningBeforeOpeningTheApp:
    """§37 п.6: предупреждать НЕПОСРЕДСТВЕННО перед открытием."""

    def test_every_screen_item_carries_a_warning(self):
        """Пункт без предупреждения — кнопка, чью природу узнают постфактум."""
        items = marketplace._screen_items()
        assert items
        for item in items:
            assert item.warning, f"{item.label}: экранный пункт без предупреждения"

    def test_the_tap_answers_with_the_warning_and_one_open_button(self, miniapp):
        warning = open_warning_reply("open_profile")
        assert warning is not None
        text, data = warning
        assert text == marketplace.WARN_PROFILE
        assert len(data["buttons"]) == 1
        assert data["buttons"][0]["web_app"] == "aylabot"
        assert data["buttons"][0]["callback"] == "open_profile"

    def test_the_time_wording_is_the_owner_s_verbatim(self):
        assert marketplace.WARN_TIME == "Для выбора времени открою расписание."

    def test_slug_resolution_refuses_a_screen_that_is_not_in_the_menu(self):
        assert open_callback_slug(f"{OPEN_CALLBACK_PREFIX}profile") == "open_profile"
        assert open_callback_slug(f"{OPEN_CALLBACK_PREFIX}food_scan") is None

    def test_no_miniapp_means_no_warning_and_no_screen_item(self, bot_user, consent, settings):
        """Предупреждение, ведущее к кнопке, которой не будет, — тупик."""
        settings.MAX_BOT_WEB_APP = ""
        settings.MAX_MINIAPP_URL = ""
        assert open_warning_reply("open_profile") is None
        _text, data = marketplace_menu_reply(bot_user=bot_user)
        payloads = _payloads(data["buttons"])
        assert not [p for p in payloads if p.startswith(OPEN_CALLBACK_PREFIX)]
        # …а ботовая половина при этом цела — иначе тест зеленел бы на
        # пустой клавиатуре (DRF-1411).
        assert DISCOVER_TAP_TEXT in payloads
        assert "cb:menu:my_bookings" in payloads

    def test_link_degradation_builds_the_declared_route(self, bot_user, consent, settings):
        settings.MAX_BOT_WEB_APP = ""
        settings.MAX_MINIAPP_URL = "https://app.example"
        warning = open_warning_reply("open_profile")
        assert warning is not None
        _text, data = warning
        assert data["buttons"][0]["url"] == "https://app.example/customer/profile"


# --------------------------------------------------------------------------- #
# 5. Убранное осталось достижимым — парная положительная стража (DRF-1411)      #
# --------------------------------------------------------------------------- #
class TestRemovedActionsStayReachable:
    """«Починка», отнимающая способность, обязана краснеть.

    Из главного меню ушли три пункта — «Перенести запись», «Отменить
    запись», «История визитов». Каждое отрицание здесь стоит рядом с
    местом, где действие ЕСТЬ.

    «История визитов» с OD-UI-1 («кнопки сливаем») снята и из подменю
    «Ещё» тоже — то есть отдельной кнопки у неё не осталось нигде. Это
    самый опасный вид переезда для такой стражи: способность легко
    объявить перенесённой и не перенести. Поэтому пара для неё теперь
    доказывает не «пункт есть в другом меню», а всю цепь от кнопки «Мои
    записи» до напечатанного списка прошлых визитов.
    """

    def test_reschedule_and_cancel_left_the_main_menu(self, bot_user, consent, miniapp):
        payloads = _payloads(marketplace_menu_reply(bot_user=bot_user)[1]["buttons"])
        # Стража НА ТЕХ ЖЕ данных: меню построено и «Мои записи» в нём есть
        # — то есть выборка непуста и путь к записям сохранён.
        assert "cb:menu:my_bookings" in payloads, payloads
        # И только теперь отрицание.
        assert "cb:menu:reschedule" not in payloads, payloads
        assert "cb:menu:cancel" not in payloads, payloads

    def test_but_they_live_on_the_booking_card(self):
        """…и живут у КОНКРЕТНОЙ записи (§37 п.1)."""
        from apps.booking.services.records import Visit
        from apps.orchestrator.visits import _card_actions

        visit = Visit(
            appointment_id="11111111-1111-4111-8111-111111111111",
            service_name="Маникюр",
            master_name="Инна",
            start_at="2026-09-20T10:00:00+00:00",
            price=None,
        )
        labels = [b["label"] for b in _card_actions(visit)]
        assert labels == ["Подробнее", "Перенести", "Отменить"]

    def test_visit_history_left_both_menus(self, bot_user, consent, miniapp, nutrition_on):
        """OD-UI-1 — «кнопки сливаем»: пункта нет уже НИГДЕ.

        До этого решения он жил в подменю «Ещё» и вёл в приложение. Оба
        отрицания стоят рядом с положительной стражей на тех же данных:
        экранные пункты в главном меню есть, подменю построено и его
        служебные кнопки на месте, — то есть проверяется состав, а не
        пустая клавиатура.
        """
        main = _payloads(marketplace_menu_reply(bot_user=bot_user)[1]["buttons"])
        extra = _payloads(marketplace_menu_reply(bot_user=bot_user)[1]["buttons"])
        assert "cb:open:profile" in main, main
        assert CALLBACK_EXTRA_HELP in extra, extra
        # И только теперь отрицания.
        assert "cb:open:visits" not in main, main
        assert "cb:open:visits" not in extra, extra

    def test_but_past_visits_are_answered_by_my_bookings_in_the_chat(self, bot_user, consent):
        """…и живут за «Моими записями» — В ЧАТЕ (§62 / OD-UI-1).

        Проверяется ЦЕПЬ, а не намерение, и каждое звено — настоящее:

        1. кнопка меню шлёт ``cb:menu:my_bookings``;
        2. ``resolve_tap_text`` переводит этот payload в каноническую
           фразу — тот самый перевод, которым тап и становится репликой
           (DRF-1051);
        3. фразу забирает детерминированный детектор
           ``is_personal_booking_lookup`` — то есть до модели она не
           доходит;
        4. ветка за детектором (``route_visits``) печатает прошлые
           визиты словами.

        Порвись любое звено — «История визитов» пропала бы молча, а
        именно этого ``TestRemovedActionsStayReachable`` и не допускает.
        """
        from apps.channels.max.quick_actions import resolve_tap_text
        from apps.skills.booking.lookup import is_personal_booking_lookup

        payloads = _payloads(marketplace_menu_reply(bot_user=bot_user)[1]["buttons"])
        assert "cb:menu:my_bookings" in payloads, payloads

        phrase = resolve_tap_text("cb:menu:my_bookings")
        assert phrase is not None
        assert is_personal_booking_lookup(phrase) is True

    def test_and_the_answer_behind_that_phrase_lists_the_past(self, monkeypatch, db):
        """Четвёртое звено той же цепи, на настоящем построителе ответа."""
        from apps.booking.services.records import Visit, VisitsResult
        from apps.orchestrator import visits as visits_mod

        visit = Visit(
            appointment_id="11111111-1111-4111-8111-111111111111",
            service_name="Массаж спины",
            master_name="Инна",
            start_at="2026-08-12T09:30:00+00:00",
            price=None,
        )
        monkeypatch.setattr(visits_mod, "list_upcoming", lambda **_: VisitsResult(status="empty"))
        monkeypatch.setattr(
            visits_mod, "list_visits", lambda **_: VisitsResult(status="ok", visits=(visit,))
        )

        class _User:
            id = "11111111-2222-3333-4444-555555555555"

        reply = visits_mod.route_visits(global_bot_user=_User())

        assert "Ваши последние визиты:" in reply.text
        assert "Массаж спины" in reply.text

    def test_the_catalog_became_a_pick_not_a_removal(self, bot_user, consent, miniapp):
        """«Каталог услуг» не пропал — он стал «Подобрать услугу» (§37 п.3)."""
        buttons = marketplace_menu_reply(bot_user=bot_user)[1]["buttons"]
        assert ("Подобрать услугу", DISCOVER_TAP_TEXT) in _pairs(buttons)
        assert "open_catalog" not in _payloads(buttons)


# --------------------------------------------------------------------------- #
# 6. Ворота питания — таблица §25 п.6 целиком                                   #
# --------------------------------------------------------------------------- #
class TestNutritionGates:
    """Три строки таблицы, и ни в одной нет мёртвой кнопки."""

    def test_flag_unset_hides_the_diary_entirely(self, bot_user, consent, miniapp, settings):
        settings.NUTRITION_ENABLED = False
        consent(True)
        _text, data = marketplace_menu_reply(bot_user=bot_user)
        payloads = _payloads(data["buttons"])
        # Положительная стража ВПЕРЕДИ отрицания: меню живо и полно.
        # «Назад» сюда больше не годится — подменю снесено (OD-UI-2), —
        # а соседи пищевого пункта на месте.
        assert DISCOVER_TAP_TEXT in payloads, payloads
        assert CALLBACK_EXTRA_HELP in payloads, payloads
        # И только теперь отрицание.
        assert DIARY_TAP_TEXT not in payloads, payloads
        assert not [p for p in payloads if p.startswith(CALLBACK_HEALTH_NEED_PREFIX)], payloads

    def test_flag_set_without_consent_points_at_the_request(
        self, bot_user, consent, miniapp, nutrition_on
    ):
        consent(False)
        payloads = _payloads(marketplace_menu_reply(bot_user=bot_user)[1]["buttons"])
        assert f"{CALLBACK_HEALTH_NEED_PREFIX}food_diary" in payloads
        assert DIARY_TAP_TEXT not in payloads

    def test_flag_set_with_consent_answers_in_the_chat(
        self, bot_user, consent, miniapp, nutrition_on
    ):
        """§37 п.5: «Бот принимает запись прямо в чате.»

        Payload — ФРАЗА, а не слаг мини-приложения: дневник ботовый, и
        тап обязан быть неотличим от набранного текста (DRF-1348).
        """
        consent(True)
        payloads = _payloads(marketplace_menu_reply(bot_user=bot_user)[1]["buttons"])
        assert DIARY_TAP_TEXT in payloads
        assert not [p for p in payloads if p.startswith(CALLBACK_HEALTH_NEED_PREFIX)]

    def test_the_diary_phrase_is_one_the_diary_itself_claims(self):
        """Кнопка не может вести туда, куда фраза не доезжает."""
        from apps.orchestrator.personal_surface import PERIOD_TODAY, looks_like_diary_request

        assert looks_like_diary_request(DIARY_TAP_TEXT) == PERIOD_TODAY

    def test_consent_read_failure_is_fail_closed(
        self, bot_user, monkeypatch, miniapp, nutrition_on
    ):
        def _boom(_bot_user):
            raise RuntimeError("db is down")

        monkeypatch.setattr("apps.consent.health.is_granted", _boom)
        payloads = _payloads(marketplace_menu_reply(bot_user=bot_user)[1]["buttons"])
        assert f"{CALLBACK_HEALTH_NEED_PREFIX}food_diary" in payloads

    def test_without_a_miniapp_an_unconsented_diary_is_not_offered(
        self, bot_user, consent, settings, nutrition_on
    ):
        """Согласие выдаётся в профиле приложения — запроса без него нет."""
        settings.MAX_BOT_WEB_APP = ""
        settings.MAX_MINIAPP_URL = ""
        consent(False)
        payloads = _payloads(marketplace_menu_reply(bot_user=bot_user)[1]["buttons"])
        assert not [p for p in payloads if p.startswith(CALLBACK_HEALTH_NEED_PREFIX)]

    def test_but_a_consented_diary_works_without_a_miniapp(
        self, bot_user, consent, settings, nutrition_on
    ):
        """Дневник ботовый — приложение ему не нужно (DRF-1411, пара)."""
        settings.MAX_BOT_WEB_APP = ""
        settings.MAX_MINIAPP_URL = ""
        consent(True)
        payloads = _payloads(marketplace_menu_reply(bot_user=bot_user)[1]["buttons"])
        assert DIARY_TAP_TEXT in payloads


# --------------------------------------------------------------------------- #
# 7. Экран запроса согласия и метка возврата                                    #
# --------------------------------------------------------------------------- #
class TestHealthConsentRequestScreen:
    def test_offers_the_place_where_consent_is_granted_and_a_way_out(self, miniapp):
        data = health_request_action_data()
        assert _labels(data["buttons"]) == ["Открыть профиль", "Не сейчас"]
        assert data["buttons"][0]["callback"] == "open_profile"
        assert data["buttons"][1]["callback"] == CALLBACK_HEALTH_DECLINE

    def test_way_out_survives_a_missing_miniapp(self, settings):
        settings.MAX_BOT_WEB_APP = ""
        settings.MAX_MINIAPP_URL = ""
        data = health_request_action_data()
        assert _labels(data["buttons"]) == ["Не сейчас"]

    def test_the_screen_promises_the_return(self):
        """§37 п.5 обещает возврат — и бот обязан сказать это вслух."""
        assert "вернёмся" in marketplace.HEALTH_REQUEST_TEXT

    def test_the_marker_carries_the_surface_and_fits_the_column(self):
        marker = health_request_action_type("food_diary")
        assert marker.startswith(marketplace.HEALTH_REQUEST_ACTION_TYPE_PREFIX)
        assert marker.endswith("food_diary")
        assert len(marker) <= 32, "Message.action_type — max_length=32"

    def test_unknown_surface_slug_is_not_a_consent_request(self):
        assert health_need_surface(f"{CALLBACK_HEALTH_NEED_PREFIX}wellness") is None
        assert health_need_surface(f"{CALLBACK_HEALTH_NEED_PREFIX}food_diary") == "food_diary"

    def test_a_removed_item_stops_being_a_consent_request(self, monkeypatch):
        monkeypatch.setattr(marketplace, "NUTRITION_ITEMS", ())
        assert health_need_surface(f"{CALLBACK_HEALTH_NEED_PREFIX}food_diary") is None


# --------------------------------------------------------------------------- #
# 8. Ветка «не поняла» и распознавание просьбы о меню                          #
# --------------------------------------------------------------------------- #
class TestMenuRequestMatcher:
    @pytest.mark.parametrize(
        "text",
        [
            "что ты умеешь",
            "Что ты умеешь?",
            "помощь",
            "Меню",
            "/help",
            "/menu",
            "какие у тебя возможности…",
        ],
    )
    def test_claims_whole_message_requests(self, text):
        assert matches_menu_request(text) is True

    @pytest.mark.parametrize(
        "text",
        [
            "",
            "хочу массаж",
            "а ещё что ты умеешь по маникюру?",
            "помоги выбрать массаж",
            "меню на завтра",
        ],
    )
    def test_leaves_everything_else_to_the_concierge(self, text):
        assert matches_menu_request(text) is False


class TestFallbackScreen:
    def test_same_keyboard_different_opening_line(self, bot_user, consent, miniapp):
        _menu_text, menu_data = marketplace_menu_reply(bot_user=bot_user)
        fallback_text, fallback_data = marketplace_fallback_reply(bot_user=bot_user)
        assert fallback_data["buttons"] == menu_data["buttons"]
        assert fallback_text.startswith("Я пока не поняла")

    def test_never_echoes_the_user(self, bot_user, consent, miniapp):
        text, _data = marketplace_fallback_reply(bot_user=bot_user)
        # Стража: экран непустой и это именно рамка промаха.
        assert text.startswith("Я пока не поняла"), text
        # И только теперь отрицание (U-5).
        assert "вы написали" not in text.lower(), text

    def test_fits_the_platform_length_ceiling(self, bot_user, consent, miniapp):
        text, _data = marketplace_fallback_reply(bot_user=bot_user)
        assert len(text) <= 300


# --------------------------------------------------------------------------- #
# 9. Инварианты состава                                                        #
# --------------------------------------------------------------------------- #
class TestCompositionInvariants:
    def test_every_line_is_covered_by_a_button(self, bot_user, consent, miniapp):
        """Перечень и клавиатура собираются из одного списка (§25)."""
        text, data = marketplace_menu_reply(bot_user=bot_user)
        for item in MAIN_ITEMS:
            if item.line:
                assert item.line in text
        assert len(data["buttons"]) == len(MAIN_ITEMS)

    def test_screen_slugs_are_declared_routes(self):
        from apps.skills.welcome.skill import MINIAPP_ROUTES

        for item in marketplace._screen_items():
            assert item.callback in MINIAPP_ROUTES, item.callback

    def test_nutrition_surface_slugs_are_max_payload_safe(self):
        import re

        for item in NUTRITION_ITEMS:
            payload = f"{CALLBACK_HEALTH_NEED_PREFIX}{marketplace._surface_slug(item)}"
            assert re.fullmatch(r"cb:health:[a-z0-9_]+:[a-z0-9_]+", payload), payload

    def test_the_marketplace_copy_does_not_name_the_pilot_salon(self, bot_user, consent, miniapp):
        text, _data = marketplace_menu_reply(bot_user=bot_user)
        assert "Формула тела" not in text

    def test_module_exposes_the_action_types_the_handler_records(self):
        assert marketplace.MENU_ACTION_TYPE
        assert marketplace.OPEN_WARNING_ACTION_TYPE
        assert marketplace.FALLBACK_ACTION_TYPE
        # Метка экрана подменю снята вместе с экраном (OD-UI-2): тапы
        # ``cb:extra:*`` из истории чата метятся как главное меню, потому
        # что им и отвечаются.
        assert not hasattr(marketplace, "EXTRA_ACTION_TYPE")

    def test_a_bot_item_never_carries_a_route_slug(self):
        """Перепутать payload и слаг — значит нарисовать кнопку в никуда."""
        from apps.skills.welcome.skill import MINIAPP_ROUTES

        bot_items: list[MenuItem] = [i for i in (*MAIN_ITEMS, *NUTRITION_ITEMS) if i.where == "bot"]
        assert bot_items
        for item in bot_items:
            assert item.callback not in MINIAPP_ROUTES, item.label
