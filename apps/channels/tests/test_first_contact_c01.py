"""C01 First Contact на глобальном пути — чипы, состояния, живые кнопки.

DRF-1348 + DRF-1051, один PR. Источник истины по составу экрана —
утверждённый макет ``ТЗ Дизайнеру/Клиент/Спецификация UX UI Первый контакт
Ayla.png`` (v1.0, APPROVED).

Что здесь пришпилено, и почему именно это:

* **Тап по чипу и тот же текст руками дают один и тот же ответ.** Главное
  требование макета («один pipeline», блок ВАЖНО повторяет его дважды) и
  единственное, которое нельзя доказать чтением кода: доказывается двумя
  прогонами настоящего входа рядом.
* **Ни одна выложенная кнопка не уходит в модель сырым payload'ом.** Таблица
  читается из самой клавиатуры, а не переписывается сюда, поэтому пятая
  кнопка, добавленная через месяц, не может проехать мимо проверки. Это и
  есть причина, по которой DRF-1051 в одном PR с DRF-1348.
* **Чипы не называются услугами** (ограничение владельца 24.08) — проверяется
  разбором быстрой ветки, а не глазами: каждый чип обязан уйти консьержу.
* Четыре состояния макета: Transient, AI недоступна + «Повторить»,
  No Quick Actions, Возврат к диалогу.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from apps.channels.max import handler as max_handler
from apps.channels.max.global_onboarding import (
    GLOBAL_S5_TEXT,
    GLOBAL_WELCOME_TEXT,
    _to_discovery_reply,
)
from apps.channels.max.quick_actions import (
    AI_UNAVAILABLE_TEXT,
    FIRST_CONTACT_QUICK_ACTIONS,
    MAX_FIRST_CONTACT_BUTTONS,
    QUICK_ACTIONS_HINT,
    RETRY_CALLBACK,
    RETRY_LABEL,
    SECONDARY_ACTION,
    STALE_TAP_TEXT,
    first_contact_buttons,
    quick_action_callback,
    render_first_contact,
    resolve_tap_text,
)
from apps.conversations.services import resolve_active_global_conversation
from apps.identity.services.resolver import resolve_or_create_global_bot_user
from apps.orchestrator.memory import short_term

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


# --------------------------------------------------------------------------- #
# Копия                                                                        #
# --------------------------------------------------------------------------- #
class TestCopyIsNeedFirst:
    """Решение владельца 24.08: need/outcome-first, без города и каталога.

    Обе константы, а не только та, к которой добавляются кнопки: навесить
    чипы поверх каталожной копии значило бы оставить Ayla каталогом с
    кнопками.
    """

    def test_welcome_asks_what_bothers_you(self):
        assert "чего тебе хочется или что сейчас беспокоит" in GLOBAL_WELCOME_TEXT

    def test_s5_asks_what_bothers_you(self):
        assert "чего тебе хочется или что сейчас беспокоит" in GLOBAL_S5_TEXT

    @pytest.mark.parametrize("catalog_word", ["маникюр", "массаж", "стрижк", "по всей стране"])
    def test_no_catalog_pitch(self, catalog_word):
        """Список услуг на первом экране макет запрещает прямо (НЕ ДЕЛАЕМ)."""
        assert catalog_word not in GLOBAL_WELCOME_TEXT.lower()
        assert catalog_word not in GLOBAL_S5_TEXT.lower()

    @pytest.mark.parametrize("city_word", ["пенз", "город"])
    def test_no_city_on_the_first_screen(self, city_word):
        """Город спрашивается тогда, когда нужен для поиска исполнителя."""
        assert city_word not in GLOBAL_WELCOME_TEXT.lower()
        assert city_word not in GLOBAL_S5_TEXT.lower()

    # Про «Не знаю, с чего начать» здесь НЕТ проверки, и это решение, а не
    # пропуск. Владелец 24.08 отложил вопрос: «не использовать и не
    # запрещать» — реестр и DRF-1179 противоречат макету, надо сверять.
    # Тест, падающий на этой формулировке, был бы запретом, то есть
    # решением за владельца. Сегодня она просто не используется.


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
    не пилот.

    ``_SERVICE_QUALIFIER_STEMS`` при этом не тронут: чинится формулировка
    чипа, а не поведение ветки.
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
        """«Найти услугу →» — выход из C01, а не запрос мастера по услуге."""
        from apps.orchestrator.fast_path import claims_direct_show_masters

        assert claims_direct_show_masters(SECONDARY_ACTION.text) is False


# --------------------------------------------------------------------------- #
# Экран C01                                                                    #
# --------------------------------------------------------------------------- #
class TestFirstScreen:
    def test_s5_ships_chips_and_the_secondary_entry(self):
        result = SimpleNamespace(
            reply_text="ignored",
            action_data=None,
            meta={"reply_kind": "welcome_s5_first_action"},
        )
        reply = _to_discovery_reply(result, None)

        assert reply.text.startswith(GLOBAL_S5_TEXT)
        assert QUICK_ACTIONS_HINT in reply.text
        labels = [b["label"] for b in reply.action_data["buttons"]]
        assert labels == [a.label for a in FIRST_CONTACT_QUICK_ACTIONS] + [SECONDARY_ACTION.label]

    def test_button_ceiling_is_not_breached(self):
        """BOT-001 AC-4.2 / DRF-1200 — не больше пяти кнопок на первом экране."""
        assert len(first_contact_buttons()) <= MAX_FIRST_CONTACT_BUTTONS

    def test_no_quick_actions_state_drops_the_hint_with_the_chips(self):
        """Макет, ДОПОЛНИТЕЛЬНЫЕ СОСТОЯНИЯ: «Показываем, если нет
        релевантных примеров в контексте». Подсказка «выбрать пример»
        без примеров — обещание, которого экран не выполняет."""
        text, action_data = render_first_contact(GLOBAL_S5_TEXT, ())

        assert text == GLOBAL_S5_TEXT
        assert QUICK_ACTIONS_HINT not in text
        labels = [b["label"] for b in (action_data or {}).get("buttons", [])]
        assert labels == [SECONDARY_ACTION.label]

    def test_no_quick_actions_without_secondary_ships_no_keyboard(self):
        text, action_data = render_first_contact(GLOBAL_S5_TEXT, (), with_secondary=False)

        assert text == GLOBAL_S5_TEXT
        assert action_data is None


# --------------------------------------------------------------------------- #
# Возврат к диалогу                                                            #
# --------------------------------------------------------------------------- #
class TestReturnToDialog:
    """Макет, ДОПОЛНИТЕЛЬНЫЕ СОСТОЯНИЯ — «Возврат к диалогу».

    До DRF-1348 этот путь выбрасывал оба состояния возврата DRF-1202 и
    здоровался с вернувшимся как с новым, предлагая согласие, которое у
    него уже есть.
    """

    def _returning(self):
        return SimpleNamespace(
            reply_text="С возвращением! 👋\n\nС чем помочь сегодня?",
            action_data={"buttons": [{"label": "📅 Записаться", "callback": "cb:menu:book"}]},
            meta={"reply_kind": "welcome_returning"},
        )

    def test_consented_returning_user_continues_the_dialog(self):
        bot_user, _ = _welcomed_user(64001)
        reply = _to_discovery_reply(self._returning(), bot_user)

        assert reply.text.startswith("С возвращением!")
        assert reply.text != GLOBAL_WELCOME_TEXT
        callbacks = [b["callback"] for b in reply.action_data["buttons"]]
        assert callbacks == [quick_action_callback(a) for a in FIRST_CONTACT_QUICK_ACTIONS] + [
            quick_action_callback(SECONDARY_ACTION)
        ]

    def test_unconsented_returning_user_still_gets_the_consent_entry(self):
        """«▶️ Начать» здесь единственный вход в 152-ФЗ. Забрать его
        нельзя даже ради красивого экрана."""
        bot_user = resolve_or_create_global_bot_user(
            channel="max", channel_user_id="64002", chat_id="8899"
        )
        reply = _to_discovery_reply(self._returning(), bot_user)

        assert reply.text == GLOBAL_WELCOME_TEXT
        assert reply.action_data["buttons"][0]["callback"] == "cb:welcome:start_s2"


# --------------------------------------------------------------------------- #
# Главное доказательство: тап == набранный текст                               #
# --------------------------------------------------------------------------- #
class TestTapIsTheSameMessageAsTyping:
    """Макет, блок ВАЖНО: «Нет отдельных команд и сценариев».

    Два прогона настоящего входа рядом — единственный способ это показать.
    """

    def test_chip_tap_and_typed_text_reach_the_model_identically(self, sent, fake_redis, concierge):
        chip = FIRST_CONTACT_QUICK_ACTIONS[2]  # «Хочу снять напряжение»

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
        chip = FIRST_CONTACT_QUICK_ACTIONS[0]
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


# --------------------------------------------------------------------------- #
# DRF-1051 — ни одной кнопки, уходящей в модель                                #
# --------------------------------------------------------------------------- #
class TestNoShippedButtonReachesTheModelRaw:
    """Причина, по которой PR один.

    Таблица не переписывается сюда: она читается из самой клавиатуры плюс
    главное меню. Кнопка, добавленная через месяц без обработчика, падает
    здесь, а не у человека в чате.
    """

    def _shipped_callbacks(self) -> list[str]:
        from apps.skills.menu.matching import main_menu_buttons

        return (
            [b["callback"] for b in first_contact_buttons()]
            + [b["callback"] for b in main_menu_buttons()]
            + [RETRY_CALLBACK]
        )

    @pytest.mark.parametrize(
        "callback",
        [
            *[b["callback"] for b in first_contact_buttons()],
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
        _welcomed_user(61003)

        max_handler.handle_global_max_event(
            _tap(payload="cb:menu:retired_button", user_id=61003, callback_id="menu-3")
        )

        assert concierge.called
        assert not concierge.call_args.args[0].startswith("cb:")

    def test_typed_lookalike_is_not_treated_as_a_tap(self, sent, fake_redis, concierge):
        """Проверка формы, а не префикса: человек может НАБРАТЬ «cb:qa:…».

        Подстановка формы не совпадает — текст идёт как обычный, и в лог
        не попадает содержимое (правило #842).
        """
        typed = "cb:qa: мой телефон +79001234567"
        _welcomed_user(61004)

        max_handler.handle_global_max_event(_msg(text=typed, user_id=61004, mid="typed-2"))

        assert concierge.call_args.args[0] == typed


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
        assert buttons[0]["payload"] == RETRY_CALLBACK

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
