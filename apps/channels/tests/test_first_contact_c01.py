"""Первый контакт глобального (клиентского) бота — v2 (DRF-2120, §50-К, 19.09).

Решение владельца 19.09: главный вход — свободный текст (DRF-1179/1272),
кнопки лишь помогают начать и не выглядят каталогом функций. Отсюда состав
экранов, пришпиленный здесь:

* **Тексты владельца — дословно** (S1, первый экран, возврат); согласие и
  S3 — как утверждены, без изменений.
* **S3 доходит.** До DRF-2120 глобальный путь подменял весь ``reply_text``
  WelcomeSkill, а S3 в нём склеен — S3 терялся. Теперь он читается по
  ``meta.s3_shown`` и стоит перед первым экраном.
* **Первый экран — три фразы C01 (§38) + «Найти услугу»**, потолок пять
  (DRF-1200). Ни «Выбрать цель», ни «+ стакан воды», ни «Просто
  посмотреть», ни «Записаться», ни «Дневник питания» — дневник в главном
  меню (решение 07.09 о дневнике на первом экране вытеснено).
* **Тап по фразе и тот же текст руками дают один и тот же ответ** — блок
  ВАЖНО макета C01 («нет отдельных команд и сценариев»); доказывается двумя
  прогонами настоящего входа рядом.
* **Возврат — кнопки по состоянию**: «Подобрать услугу» всегда, «Моя
  запись» при ближайшей записи, «Записать еду» при включённом питании (с
  воротами согласия дневника на ответе), «Меню». «Продолжить» не строится
  (DRF-1198 нет; владелец: отложить).
* **Ни одна выложенная кнопка не уходит в модель сырым payload'ом** — таблица
  читается из самих клавиатур, а не переписывается сюда.
* Состояния макета: AI недоступна + «Повторить», Transient.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from apps.channels.max import handler as max_handler
from apps.channels.max.global_onboarding import (
    CALLBACK_LOG_FOOD,
    GLOBAL_RETURNING_TEXT,
    GLOBAL_S5_TEXT,
    GLOBAL_WELCOME_TEXT,
    RETURNING_LABEL_DISCOVER,
    RETURNING_LABEL_LOG_FOOD,
    RETURNING_LABEL_MENU,
    RETURNING_LABEL_MY_BOOKING,
    START_BUTTON_LABEL,
    _to_discovery_reply,
    first_contact_action_data,
    first_contact_buttons,
    first_contact_text,
    needs_onboarding,
    resolve_welcome_tap,
    returning_buttons,
    run_onboarding_turn,
)
from apps.channels.max.quick_actions import (
    AI_UNAVAILABLE_TEXT,
    FIRST_CONTACT_QUICK_ACTIONS,
    FIRST_SCREEN_SLUGS,
    MAX_FIRST_CONTACT_BUTTONS,
    RETRY_CALLBACK,
    RETRY_LABEL,
    SECONDARY_ACTION,
    STALE_TAP_TEXT,
    first_screen_actions,
    is_retry_callback,
    quick_action_callback,
    resolve_tap_text,
    retry_turn_id,
)
from apps.conversations.services import resolve_active_global_conversation
from apps.identity.services.resolver import resolve_or_create_global_bot_user
from apps.orchestrator.memory import short_term
from apps.skills.menu.marketplace import DISCOVER_TAP_TEXT, marketplace_menu_buttons
from apps.skills.welcome.skill import (
    FOOD_PROMPT,
    S2_CONSENT_TEXT,
    S3_POSITIONING_TEXT,
)

pytestmark = pytest.mark.django_db


# --------------------------------------------------------------------------- #
# Fixtures                                                                     #
# --------------------------------------------------------------------------- #
@pytest.fixture(autouse=True)
def _onboarding_on(settings):
    settings.GLOBAL_BOT_ONBOARDING = True


@pytest.fixture(autouse=True)
def _no_chat_actions(monkeypatch):
    """Индикатор набора — сетевой вызов; в тестах он молчит.

    Отдельно проверяется в :class:`TestTransientTypingIndicator`.
    """
    monkeypatch.setattr(
        "apps.channels.max.outbound.send_chat_action",
        lambda **kwargs: {"ok": True},
    )


@pytest.fixture(autouse=True)
def no_upcoming(monkeypatch):
    """Каталог записей — сеть; по умолчанию «записей нет». Возвращает
    переключатель, чтобы узел мог положить человеку ближайшую запись."""
    from apps.booking.services.records import VisitsResult

    state = {"status": "empty"}
    monkeypatch.setattr(
        "apps.booking.services.records.list_upcoming",
        lambda **kwargs: VisitsResult(status=state["status"]),
    )

    def _set(status: str) -> None:
        state["status"] = status

    return _set


@pytest.fixture
def sent(monkeypatch):
    calls: list[dict] = []

    def fake_send(*, chat_id, text, attachments=None, timeout=10.0):
        calls.append({"chat_id": chat_id, "text": text, "attachments": attachments})
        return {"ok": True}

    monkeypatch.setattr(max_handler, "send_message", fake_send)
    return calls


@pytest.fixture
def fake_redis(monkeypatch):
    from apps.orchestrator.memory.tests.test_short_term import _FakeRedis

    fake = _FakeRedis()
    monkeypatch.setattr(short_term, "_redis_client", lambda: fake)
    return fake


@pytest.fixture
def concierge(monkeypatch):
    """Шпион на месте модели: запоминает, ЧТО ей досталось."""
    from apps.orchestrator.discovery import DiscoveryReply

    spy = MagicMock(return_value=DiscoveryReply(text="Расскажи чуть подробнее?", persisted=False))
    monkeypatch.setattr("apps.orchestrator.concierge.generate_concierge_reply", spy)
    return spy


def _msg(*, text: str, user_id: int, chat_id: int = 8899, mid: str = "m-1") -> dict:
    return {
        "update_type": "message_created",
        "timestamp": 1731320000000,
        "message": {
            "sender": {"user_id": user_id, "name": "Ирина"},
            "recipient": {"chat_id": chat_id, "chat_type": "dialog"},
            "body": {"mid": mid, "seq": 1, "text": text, "attachments": []},
        },
    }


def _tap(*, payload: str, user_id: int, chat_id: int = 8899, callback_id: str = "c-1") -> dict:
    return {
        "update_type": "message_callback",
        "timestamp": 1731320000000,
        "callback": {
            "callback_id": callback_id,
            "user": {"user_id": user_id, "name": "Ирина"},
            "payload": payload,
        },
        "message": {"recipient": {"chat_id": chat_id, "chat_type": "dialog"}},
    }


def _welcomed_user(user_id: int):
    """Пользователь, который приветствие и 152-ФЗ уже прошёл.

    Согласие ставится ЖУРНАЛОМ, а не столбцом ``consent_at``: экран читает
    активный грант (см. ``global_onboarding._consent_captured``), и тест,
    ставящий только столбец, доказывал бы не то, что происходит на пилоте.
    """
    from django.utils import timezone

    from apps.consent.services import record_global_consent

    bot_user = resolve_or_create_global_bot_user(
        channel="max", channel_user_id=str(user_id), chat_id="8899"
    )
    bot_user.welcomed_at = timezone.now()
    bot_user.save(update_fields=["welcomed_at"])
    record_global_consent(
        bot_user,
        consent_type="personal_data",
        source="test:first_contact",
        document_version="welcome-s2-v1",
    )
    bot_user.refresh_from_db()
    return bot_user, resolve_active_global_conversation(bot_user)


def _buttons(attachments) -> list[dict]:
    """Плоский список кнопок из MAX-вложения (или пустой)."""
    if not attachments:
        return []
    rows = attachments[0].get("payload", {}).get("buttons", [])
    return [b for row in rows for b in row]


def _user_messages(conversation) -> list[str]:
    from apps.conversations.models import Message

    return list(
        Message.all_tenants.filter(conversation_id=conversation.id, role="user")
        .order_by("created_at")
        .values_list("content", flat=True)
    )


def _labels(buttons) -> list[str]:
    return [b["label"] for b in buttons]


def _callbacks(buttons) -> list[str]:
    return [b["callback"] for b in buttons]


def _welcome_result(kind: str, **meta):
    return SimpleNamespace(
        reply_text="ignored", action_data=None, meta={"reply_kind": kind, **meta}
    )


# --------------------------------------------------------------------------- #
# Тексты владельца — буквой                                                    #
# --------------------------------------------------------------------------- #
class TestOwnerTextsVerbatim:
    """Тексты — константы, тест держит их дословно (лист: «тесты — дословно»).

    Литералы здесь намеренно НЕ импортированы из модуля: тест, читающий
    ожидание из проверяемого, не может провалиться.
    """

    def test_s1(self):
        assert GLOBAL_WELCOME_TEXT == (
            "Привет! Я Ayla 👋\n"
            "Помогу разобраться, что может подойти, найти услугу и записаться. "
            "Можно просто рассказать, чего хочется или что сейчас беспокоит."
        )
        assert START_BUTTON_LABEL == "Начать"

    def test_consent_is_the_approved_text_unchanged(self):
        assert S2_CONSENT_TEXT == (
            "Прежде чем начать — короткое слово.\n"
            "Я буду помнить о тебе только то, что поможет рекомендовать точнее. "
            "Хранится безопасно. Удалить можно в любой момент.\n\n"
            "Продолжим?"
        )

    def test_s3_as_is(self):
        assert S3_POSITIONING_TEXT == (
            "Если коротко — я не календарь и не ещё одна программа правильного "
            "питания. Я помогу разобраться с собой каждый день — еда, вода, "
            "ближайшая запись, самочувствие. Без оценок."
        )

    def test_first_screen(self):
        assert GLOBAL_S5_TEXT == (
            "С чего начнём?\n"
            "Напиши своими словами, чего хочется или что сейчас беспокоит. "
            "Не обязательно знать название услуги."
        )

    def test_returning(self):
        assert GLOBAL_RETURNING_TEXT == (
            "С возвращением! Что хочешь сделать сегодня? Можно написать своими словами."
        )

    def test_first_screen_button_labels(self):
        assert _labels(first_contact_buttons()) == [
            "Хочу выглядеть свежее",
            "Хочу снять напряжение",
            "Последнее время сильно устаю",
            "Найти услугу",
        ]

    def test_returning_button_labels(self):
        assert (
            RETURNING_LABEL_DISCOVER,
            RETURNING_LABEL_MY_BOOKING,
            RETURNING_LABEL_LOG_FOOD,
            RETURNING_LABEL_MENU,
        ) == ("Подобрать услугу", "Моя запись", "Записать еду", "Меню")


# --------------------------------------------------------------------------- #
# Чипы не называются услугами                                                  #
# --------------------------------------------------------------------------- #
class TestChipsAreNotServiceNames:
    """Ограничение владельца 24.08, проверенное разбором, а не глазами.

    «Goal-like quick actions не должны называться услугами.» Причина
    маршрутная: чип-потребность уходит консьержу сам собой, и дописывать
    быстрой ветке отдельный отказ не приходится. Если кто-то назовёт чип
    «Лимфодренаж», ход заберут карточки мастеров — правильный ответ на
    название услуги и неправильный на потребность, — и падёт этот тест, а
    не пилот. Проверяется вся таблица §38, а не только три на экране:
    старая клавиатура с шестью живёт в истории чата.
    """

    @pytest.mark.parametrize("action", FIRST_CONTACT_QUICK_ACTIONS, ids=lambda a: a.slug)
    def test_chip_is_not_claimed_by_the_fast_path(self, action):
        from apps.orchestrator.fast_path import claims_direct_show_masters

        assert claims_direct_show_masters(action.text) is False

    @pytest.mark.parametrize("action", FIRST_CONTACT_QUICK_ACTIONS, ids=lambda a: a.slug)
    def test_chip_does_not_name_a_service(self, action):
        from apps.skills.menu.matching import mentions_service

        assert mentions_service(action.text) is False

    def test_secondary_entry_reaches_the_catalog_not_the_fast_path(self):
        """«Найти услугу» — выход из C01, а не запрос мастера по услуге."""
        from apps.orchestrator.fast_path import claims_direct_show_masters

        assert claims_direct_show_masters(SECONDARY_ACTION.text) is False


# --------------------------------------------------------------------------- #
# Первый экран                                                                 #
# --------------------------------------------------------------------------- #
class TestFirstScreen:
    def test_s3_reaches_the_person_on_the_direct_path(self):
        """Дефект до DRF-2120: S3 терялся — путь подменял весь reply_text."""
        reply = _to_discovery_reply(_welcome_result("welcome_s5_first_action", s3_shown=True), None)

        assert reply.text == f"{S3_POSITIONING_TEXT}\n\n{GLOBAL_S5_TEXT}"

    def test_s3_is_skipped_after_the_s2a_fold_as_welcome_decides(self):
        reply = _to_discovery_reply(
            _welcome_result("welcome_s5_first_action", s3_shown=False), None
        )

        assert reply.text == GLOBAL_S5_TEXT

    def test_first_contact_text_is_the_only_composer(self):
        assert first_contact_text(s3_shown=True).endswith(GLOBAL_S5_TEXT)
        assert first_contact_text(s3_shown=False) == GLOBAL_S5_TEXT

    def test_the_screen_is_three_c01_phrases_and_find_service(self):
        reply = _to_discovery_reply(_welcome_result("welcome_s5_first_action"), None)

        callbacks = _callbacks(reply.action_data["buttons"])
        assert callbacks == [quick_action_callback(a) for a in first_screen_actions()] + [
            quick_action_callback(SECONDARY_ACTION)
        ]
        assert reply.action_data["button_columns"] == 1

    def test_the_three_are_a_subset_of_the_c01_table(self):
        """«Три из C01» — подмножество таблицы §38, не своя копия: обновится
        C01 — обновятся и они."""
        table = {a.slug: a for a in FIRST_CONTACT_QUICK_ACTIONS}
        assert len(FIRST_SCREEN_SLUGS) == 3
        assert [a.slug for a in first_screen_actions()] == list(FIRST_SCREEN_SLUGS)
        for action in first_screen_actions():
            assert table[action.slug] is action

    def test_ceiling_is_five_and_the_screen_is_four(self):
        """Потолок — DRF-1200; РАВЕНСТВО, а не «≤»: пятая кнопка появится
        решением, а не по ходу правки."""
        assert MAX_FIRST_CONTACT_BUTTONS == 5
        assert len(first_contact_buttons()) == 4

    @pytest.mark.parametrize(
        "forbidden", ["Выбрать цель", "стакан воды", "Просто посмотреть", "Записаться", "Дневник"]
    )
    def test_nothing_from_the_menu_or_the_wellness_grid(self, forbidden, settings):
        """Владелец: на первом экране этого нет. Присутствие — рядом: экран
        не пуст и дневник ЖИВ в главном меню (он не удалён, а не здесь)."""
        settings.NUTRITION_ENABLED = True
        settings.MAX_BOT_WEB_APP = "aylabot"
        person = SimpleNamespace(id="stub")
        with patch("apps.consent.nutrition.diary_or_health_granted", lambda _u: True):
            in_menu = _labels(marketplace_menu_buttons(bot_user=person))
            on_screen = _labels(first_contact_action_data(person)["buttons"])

        assert on_screen == _labels(first_contact_buttons())
        assert [label for label in in_menu if label.endswith("Дневник питания")]
        assert not [label for label in on_screen if forbidden.lower() in label.lower()]

    def test_no_button_opens_the_mini_app_from_the_first_screen(self):
        buttons = first_contact_buttons()
        assert buttons
        assert all(b["callback"].startswith("cb:qa:") for b in buttons)
        assert not any("web_app" in b or "url" in b for b in buttons)

    def test_live_path_s1_consent_s3_first_screen(self):
        """Тем же входом, что и живой бот: /start → S1 → согласие → S3 + экран."""
        bot_user = resolve_or_create_global_bot_user(
            channel="max", channel_user_id="65001", chat_id="8899"
        )
        conv = resolve_active_global_conversation(bot_user)

        s1 = run_onboarding_turn(conv, bot_user, "/start")
        assert s1.text == GLOBAL_WELCOME_TEXT
        assert s1.action_data["buttons"] == [
            {"label": START_BUTTON_LABEL, "callback": "cb:welcome:start_s2"}
        ]

        s2 = run_onboarding_turn(conv, bot_user, "cb:welcome:start_s2")
        assert s2.text == S2_CONSENT_TEXT

        s5 = run_onboarding_turn(conv, bot_user, "cb:welcome:consent_yes")
        assert s5.text == f"{S3_POSITIONING_TEXT}\n\n{GLOBAL_S5_TEXT}"
        assert _labels(s5.action_data["buttons"]) == _labels(first_contact_buttons())


# --------------------------------------------------------------------------- #
# Возврат к диалогу — кнопки по состоянию                                      #
# --------------------------------------------------------------------------- #
class TestReturnToDialog:
    """Четыре состояния возврата: без согласия; с согласием и ничем; с
    ближайшей записью; с включённым питанием."""

    def test_unconsented_returning_user_still_gets_the_consent_entry(self):
        """«Начать» здесь единственный вход в 152-ФЗ. Забрать его
        нельзя даже ради красивого экрана."""
        bot_user = resolve_or_create_global_bot_user(
            channel="max", channel_user_id="64002", chat_id="8899"
        )
        reply = _to_discovery_reply(_welcome_result("welcome_returning"), bot_user)

        assert reply.text == GLOBAL_WELCOME_TEXT
        assert reply.action_data["buttons"][0]["callback"] == "cb:welcome:start_s2"

    def test_consented_bare_state_is_discover_and_menu(self, settings):
        settings.NUTRITION_ENABLED = False
        bot_user, _ = _welcomed_user(64001)
        reply = _to_discovery_reply(_welcome_result("welcome_returning"), bot_user)

        assert reply.text == GLOBAL_RETURNING_TEXT
        assert _labels(reply.action_data["buttons"]) == [
            RETURNING_LABEL_DISCOVER,
            RETURNING_LABEL_MENU,
        ]

    def test_upcoming_booking_adds_my_booking_second(self, settings, no_upcoming):
        settings.NUTRITION_ENABLED = False
        no_upcoming("ok")
        bot_user, _ = _welcomed_user(64003)

        assert _labels(returning_buttons(bot_user)) == [
            RETURNING_LABEL_DISCOVER,
            RETURNING_LABEL_MY_BOOKING,
            RETURNING_LABEL_MENU,
        ]

    def test_nutrition_on_adds_log_food_before_menu(self, settings):
        settings.NUTRITION_ENABLED = True
        bot_user, _ = _welcomed_user(64004)

        assert _labels(returning_buttons(bot_user)) == [
            RETURNING_LABEL_DISCOVER,
            RETURNING_LABEL_LOG_FOOD,
            RETURNING_LABEL_MENU,
        ]

    def test_full_state_fits_the_ceiling(self, settings, no_upcoming):
        settings.NUTRITION_ENABLED = True
        no_upcoming("ok")
        bot_user, _ = _welcomed_user(64005)
        buttons = returning_buttons(bot_user)

        assert len(buttons) == 4 <= MAX_FIRST_CONTACT_BUTTONS

    def test_catalog_down_hides_my_booking_and_keeps_the_greeting(self, settings, no_upcoming):
        """Fail-closed: записи, которую нечем показать, кнопка не обещает."""
        settings.NUTRITION_ENABLED = False
        no_upcoming("backend_unavailable")
        bot_user, _ = _welcomed_user(64006)
        reply = _to_discovery_reply(_welcome_result("welcome_returning"), bot_user)

        assert reply.text == GLOBAL_RETURNING_TEXT
        assert RETURNING_LABEL_MY_BOOKING not in _labels(reply.action_data["buttons"])

    def test_active_task_does_not_promise_to_continue(self, settings):
        """«Продолжить» не строится (DRF-1198 нет; владелец: отложить) —
        незаконченная задача отвечает тем же возвратом."""
        settings.NUTRITION_ENABLED = False
        bot_user, _ = _welcomed_user(64007)
        reply = _to_discovery_reply(_welcome_result("welcome_active_task"), bot_user)

        assert reply.text == GLOBAL_RETURNING_TEXT
        assert "продолжим" not in reply.text.lower()

    def test_every_return_button_has_a_branch(self, settings, no_upcoming):
        """Куда ведёт каждый тап — не на слово: фраза (в тот же конвейер),
        перевод тапа меню в фразу, или ветка приветствия (``cb:welcome:``)."""
        settings.NUTRITION_ENABLED = True
        no_upcoming("ok")
        bot_user, conv = _welcomed_user(64008)
        callbacks = _callbacks(returning_buttons(bot_user))
        assert len(callbacks) == 4

        assert callbacks[0] == DISCOVER_TAP_TEXT and not DISCOVER_TAP_TEXT.startswith("cb:")
        assert resolve_tap_text(callbacks[1]) == "Покажи мои записи"
        assert callbacks[2] == CALLBACK_LOG_FOOD and needs_onboarding(bot_user, callbacks[2], conv)
        assert resolve_tap_text(callbacks[3]) == "Что ты умеешь?"


# --------------------------------------------------------------------------- #
# «Записать еду» — ворота дневника на ответе                                   #
# --------------------------------------------------------------------------- #
class TestLogFoodTap:
    @pytest.fixture
    def person(self, settings):
        settings.NUTRITION_ENABLED = True
        settings.MAX_BOT_WEB_APP = "aylabot"
        settings.MAX_MINIAPP_URL = ""
        bot_user, conv = _welcomed_user(66001)
        return bot_user, conv

    def test_with_diary_consent_the_tap_invites_food(self, person):
        bot_user, conv = person
        with patch("apps.consent.nutrition.diary_is_granted", lambda _u: True):
            reply = run_onboarding_turn(conv, bot_user, CALLBACK_LOG_FOOD)

        assert reply.text == FOOD_PROMPT

    def test_without_diary_consent_the_tap_explains_and_leads_to_consent(self, person):
        """DRF-2096: объяснение + «Открыть и разрешить», а не приглашение
        прислать еду, чтобы отказать следующим ходом."""
        from apps.skills.food_clarify.text_entry import (
            DIARY_CONSENT_REQUIRED_WITH_BUTTON_TEXT,
        )

        bot_user, conv = person
        with patch("apps.consent.nutrition.diary_is_granted", lambda _u: False):
            reply = run_onboarding_turn(conv, bot_user, CALLBACK_LOG_FOOD)

        assert reply.text == DIARY_CONSENT_REQUIRED_WITH_BUTTON_TEXT
        assert reply.text != FOOD_PROMPT
        assert _labels(reply.action_data["buttons"]) == ["Открыть и разрешить"]

    def test_without_personal_data_consent_the_tap_offers_it(self, settings):
        """Старая клавиатура у отозвавшего согласие: ворота 152-ФЗ первыми."""
        from apps.skills.food_clarify.text_entry import CONSENT_TEXT

        settings.NUTRITION_ENABLED = True
        bot_user = resolve_or_create_global_bot_user(
            channel="max", channel_user_id="66002", chat_id="8899"
        )
        conv = resolve_active_global_conversation(bot_user)
        reply = run_onboarding_turn(conv, bot_user, CALLBACK_LOG_FOOD)

        assert reply.text == CONSENT_TEXT

    def test_the_tap_lands_in_history_as_the_owners_label(self):
        assert resolve_welcome_tap(CALLBACK_LOG_FOOD).history_text == RETURNING_LABEL_LOG_FOOD
        assert resolve_welcome_tap("cb:welcome:start_s2").history_text == START_BUTTON_LABEL


# --------------------------------------------------------------------------- #
# Главное доказательство: тап == набранный текст                               #
# --------------------------------------------------------------------------- #
class TestTapIsTheSameMessageAsTyping:
    """Макет, блок ВАЖНО: «Нет отдельных команд и сценариев».

    Два прогона настоящего входа рядом — единственный способ это показать.
    """

    def test_chip_tap_and_typed_text_reach_the_model_identically(self, sent, fake_redis, concierge):
        chip = first_screen_actions()[1]  # «Хочу снять напряжение»

        _welcomed_user(60001)
        max_handler.handle_global_max_event(
            _tap(payload=quick_action_callback(chip), user_id=60001, callback_id="tap-1")
        )
        _welcomed_user(60002)
        max_handler.handle_global_max_event(_msg(text=chip.text, user_id=60002, mid="typed-1"))

        assert concierge.call_count == 2
        tapped_text = concierge.call_args_list[0].args[0]
        typed_text = concierge.call_args_list[1].args[0]
        assert tapped_text == typed_text == chip.text
        assert sent[0]["text"] == sent[1]["text"]

    def test_the_chip_lands_in_history_as_the_phrase_not_as_a_payload(
        self, sent, fake_redis, concierge
    ):
        """DRF-990 класс: сырой «cb:…» в истории — то, что модель охотно
        толкует. Подстановка стоит выше персистенса, поэтому в истории
        оказывается фраза."""
        chip = first_screen_actions()[0]
        _, conversation = _welcomed_user(60003)

        max_handler.handle_global_max_event(
            _tap(payload=quick_action_callback(chip), user_id=60003, callback_id="tap-2")
        )

        assert _user_messages(conversation) == [chip.text]

    def test_secondary_entry_asks_the_catalog_in_words(self, sent, fake_redis, concierge):
        _welcomed_user(60004)

        max_handler.handle_global_max_event(
            _tap(
                payload=quick_action_callback(SECONDARY_ACTION),
                user_id=60004,
                callback_id="tap-3",
            )
        )

        assert concierge.call_args.args[0] == SECONDARY_ACTION.text

    def test_a_chip_from_the_old_six_button_keyboard_still_speaks(
        self, sent, fake_redis, concierge
    ):
        """Старая клавиатура в истории чата живёт дольше правки: снятый с
        экрана чип §38 по-прежнему переводится в свою фразу."""
        retired_from_screen = [
            a for a in FIRST_CONTACT_QUICK_ACTIONS if a.slug not in FIRST_SCREEN_SLUGS
        ]
        assert retired_from_screen
        chip = retired_from_screen[0]
        _welcomed_user(60005)

        max_handler.handle_global_max_event(
            _tap(payload=quick_action_callback(chip), user_id=60005, callback_id="tap-4")
        )

        assert concierge.call_args.args[0] == chip.text


# --------------------------------------------------------------------------- #
# DRF-1051 — ни одной кнопки, уходящей в модель                                #
# --------------------------------------------------------------------------- #
class TestNoShippedButtonReachesTheModelRaw:
    """Таблица не переписывается сюда: она читается из самих клавиатур плюс
    главное меню. Кнопка, добавленная через месяц без обработчика, падает
    здесь, а не у человека в чате.
    """

    def _shipped_callbacks(self) -> list[str]:
        from apps.skills.menu.matching import main_menu_buttons

        return (
            _callbacks(first_contact_buttons()) + _callbacks(main_menu_buttons()) + [RETRY_CALLBACK]
        )

    @pytest.mark.parametrize(
        "callback",
        [
            *_callbacks(first_contact_buttons()),
            "cb:menu:book",
            "cb:menu:my_bookings",
            "cb:menu:reschedule",
            "cb:menu:cancel",
            "cb:menu:help",
        ],
    )
    def test_every_shipped_callback_resolves_to_a_phrase(self, callback):
        resolved = resolve_tap_text(callback)

        assert resolved, f"{callback} не переводится в фразу — тап уедет в модель"
        assert not resolved.startswith("cb:")

    def test_the_roster_is_the_real_keyboard(self):
        """Сторож сторожа: если клавиатура опустела, параметризация выше
        стала бы зелёной ни о чём."""
        assert len(self._shipped_callbacks()) >= 4

    def test_my_bookings_tap_reaches_the_real_bookings(self, sent, fake_redis, concierge):
        """DRF-1051 дословно: тап по «📋 Мои записи» уходил в LLM."""
        _welcomed_user(61001)
        route = MagicMock(
            return_value=__import__(
                "apps.orchestrator.discovery", fromlist=["DiscoveryReply"]
            ).DiscoveryReply(text="Ваши записи:")
        )
        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(max_handler, "route_visits", route)
            max_handler.handle_global_max_event(
                _tap(payload="cb:menu:my_bookings", user_id=61001, callback_id="menu-1")
            )

        assert route.called
        assert concierge.called is False
        assert sent[-1]["text"] == "Ваши записи:"

    def test_book_tap_reaches_the_model_as_a_phrase(self, sent, fake_redis, concierge):
        _welcomed_user(61002)

        max_handler.handle_global_max_event(
            _tap(payload="cb:menu:book", user_id=61002, callback_id="menu-2")
        )

        assert concierge.call_args.args[0] == "Хочу записаться"

    def test_retired_menu_slug_never_reaches_the_model_raw(self, sent, fake_redis, concierge):
        """DRF-1491 — снятый слаг отвечает МЕНЮ, а не прозой модели."""
        _welcomed_user(61003)

        max_handler.handle_global_max_event(
            _tap(payload="cb:menu:retired_button", user_id=61003, callback_id="menu-3")
        )

        # Стража: ход не потерян и клавиатура доехала.
        assert sent, "снятый слаг остался без ответа"
        assert sent[-1]["attachments"], sent[-1]
        # И только теперь отрицание: сырого payload'а человек не видел…
        assert "cb:" not in sent[-1]["text"], sent[-1]["text"]
        # …и модель его тоже не видела.
        assert concierge.called is False

    def test_typed_lookalike_is_not_treated_as_a_tap(self, sent, fake_redis, concierge):
        """Проверка формы, а не префикса: человек может НАБРАТЬ «cb:qa:…»."""
        typed = "cb:qa: мой телефон +79001234567"
        _welcomed_user(61004)

        max_handler.handle_global_max_event(_msg(text=typed, user_id=61004, mid="typed-2"))

        assert concierge.call_args.args[0] == typed

    def test_stale_tap_screen_carries_the_first_screen_keyboard(self, sent, fake_redis, concierge):
        """Экран «кнопка устарела» — те же три фразы и «Найти услугу»."""
        _welcomed_user(61005)

        max_handler.handle_global_max_event(
            _tap(payload="cb:qa:no_such_slug_anymore", user_id=61005, callback_id="stale-1")
        )

        assert sent[-1]["text"] == STALE_TAP_TEXT
        assert [b["text"] for b in _buttons(sent[-1]["attachments"])] == _labels(
            first_contact_buttons()
        )
        assert concierge.called is False


# --------------------------------------------------------------------------- #
# AI недоступна + Повторить                                                    #
# --------------------------------------------------------------------------- #
class TestAiUnavailable:
    """Макет, ДОПОЛНИТЕЛЬНЫЕ СОСТОЯНИЯ — «AI недоступна» с «Повторить».

    Отрицательное доказательство брифа: при недоступной модели должен быть
    экран, а не молчание и не потерянный ход.
    """

    @pytest.fixture
    def broken_model(self, monkeypatch):
        from apps.orchestrator.discovery import DiscoveryReply

        monkeypatch.setattr(
            "apps.orchestrator.concierge.generate_concierge_reply",
            MagicMock(
                return_value=DiscoveryReply(
                    text="Извини, у меня сейчас короткий технический сбой — отвечу через минуту.",
                    outage=True,
                )
            ),
        )

    def test_outage_shows_the_screen_with_a_retry_button(self, sent, fake_redis, broken_model):
        _welcomed_user(62001)

        max_handler.handle_global_max_event(
            _msg(text="Хочу снять напряжение", user_id=62001, mid="out-1")
        )

        assert sent[-1]["text"] == AI_UNAVAILABLE_TEXT
        buttons = _buttons(sent[-1]["attachments"])
        assert [b["text"] for b in buttons] == [RETRY_LABEL]
        # DRF-1762 — кнопка привязана к строке хода, а не «последнее что было».
        assert is_retry_callback(buttons[0]["payload"])
        assert retry_turn_id(buttons[0]["payload"]) is not None

    def test_retry_resends_the_persons_own_words(self, sent, fake_redis, broken_model, monkeypatch):
        _welcomed_user(62002)
        max_handler.handle_global_max_event(
            _msg(text="Беспокоят отёки", user_id=62002, mid="out-2")
        )

        from apps.orchestrator.discovery import DiscoveryReply

        recovered = MagicMock(return_value=DiscoveryReply(text="Расскажи чуть подробнее?"))
        monkeypatch.setattr("apps.orchestrator.concierge.generate_concierge_reply", recovered)
        max_handler.handle_global_max_event(
            _tap(payload=RETRY_CALLBACK, user_id=62002, callback_id="retry-1")
        )

        assert recovered.call_args.args[0] == "Беспокоят отёки"

    def test_retry_falls_back_to_the_message_table_when_short_term_expired(
        self, sent, fake_redis, concierge
    ):
        """У короткой памяти TTL, а сбой — ровно та причина, по которой
        человек подождал и вернулся к кнопке позже. Тогда «Повторить»
        обязано брать реплику из таблицы сообщений, а не сдаваться."""
        from apps.conversations.services import record_global_message

        _, conversation = _welcomed_user(62005)
        record_global_message(conversation, role="user", content="Хочу выглядеть свежее")
        record_global_message(conversation, role="assistant", content=AI_UNAVAILABLE_TEXT)
        # В короткую память эти строки НЕ кладутся (``record_global_message``
        # её не трогает) — то есть путь через таблицу здесь настоящий, а не
        # смоделированный.

        max_handler.handle_global_max_event(
            _tap(payload=RETRY_CALLBACK, user_id=62005, callback_id="retry-3")
        )

        assert concierge.call_args.args[0] == "Хочу выглядеть свежее"

    def test_retry_without_history_says_so_instead_of_repeating_nothing(
        self, sent, fake_redis, concierge
    ):
        _welcomed_user(62003)

        max_handler.handle_global_max_event(
            _tap(payload=RETRY_CALLBACK, user_id=62003, callback_id="retry-2")
        )

        assert sent[-1]["text"] == STALE_TAP_TEXT
        assert concierge.called is False

    def test_outage_turn_is_not_charged_to_the_intent_resolver(
        self, sent, fake_redis, broken_model, monkeypatch
    ):
        """Разбор намерения — ещё один вызов той же недоступной модели."""
        resolver = MagicMock()
        monkeypatch.setattr(max_handler, "resolve_and_log_turn_intent", resolver)
        _welcomed_user(62004)

        max_handler.handle_global_max_event(_msg(text="что-нибудь", user_id=62004, mid="out-3"))

        assert resolver.called is False


# --------------------------------------------------------------------------- #
# Transient                                                                    #
# --------------------------------------------------------------------------- #
class TestTransientTypingIndicator:
    """Макет C01.4 — «Composer виден, отправка временно заблокирована».

    Заблокировать отправку в чужом клиенте бот не может; что он может —
    показать «прочитано / печатает…». На арендаторском пути эти две строки
    стоят с мая, на глобальном их не было никогда.
    """

    def test_global_path_marks_seen_and_types(self, sent, fake_redis, concierge, monkeypatch):
        actions: list[str] = []
        monkeypatch.setattr(
            "apps.channels.max.outbound.send_chat_action",
            lambda **kwargs: actions.append(kwargs.get("action")) or {"ok": True},
        )
        _welcomed_user(63001)

        max_handler.handle_global_max_event(_msg(text="Хочу выглядеть свежее", user_id=63001))

        assert actions == ["mark_seen", "typing_on"]
