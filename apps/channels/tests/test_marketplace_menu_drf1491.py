"""Главное меню витрины — через настоящий вход (DRF-1491).

Тесты гоняют ``handle_global_max_event`` — тот же путь, которым идёт
живой клиентский бот, — а не построители в изоляции: дефект, ради
которого заведена задача, лежал именно в проводке. Тексты меню и ветка
«не понял» существовали, но в реестре навыков, который глобальный путь не
диспетчеризует (``SURFACE_GLOBAL``), поэтому человек на пилоте не видел
ни того, ни другого.

Проверяется пять вещей, и каждая — глазами человека, а не кода:

* «что ты умеешь?» отвечает МЕНЮ с клавиатурой, а не прозой консьержа;
* нераспознанный тап отвечает «не поняла» с клавиатурой, а не уезжает в
  модель сырым payload'ом;
* каждый нарисованный ``cb:`` payload кто-то принимает;
* пищевые пункты подчиняются ДВУМ воротам (§25 п.6), и тап без согласия
  ведёт на запрос согласия, а не в поверхность — механизм проверяется на
  подставленных пунктах, потому что DRF-1543 снял их из меню до появления
  ручек ``customer/food/*``, а сам механизм остался;
* отказ уважается: возврат назад и НИ ОДНОГО повторного запроса в том же
  диалоге.

Правило DRF-1411 соблюдено буквально: рядом с каждым «этого нет» стоит
«а вот это есть» на тех же данных. Тест, у которого клавиатура вдруг
станет пустой, обязан краснеть, а не зеленеть.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from apps.channels.max import handler as max_handler
from apps.conversations.services import resolve_active_global_conversation
from apps.identity.services.resolver import resolve_or_create_global_bot_user
from apps.orchestrator.memory import short_term
from apps.skills.menu import marketplace as menu_marketplace
from apps.skills.menu.marketplace import (
    CALLBACK_EXTRA_OPEN,
    CALLBACK_HEALTH_DECLINE,
    CALLBACK_HEALTH_NEED_PREFIX,
    HEALTH_CHECK_FAILED_TEXT,
    HEALTH_DECLINE_ACTION_TYPE,
    HEALTH_DECLINED_EARLIER_TEXT,
    HEALTH_DECLINED_TEXT,
    HEALTH_REQUEST_TEXT,
    MAIN_ITEMS,
    MENU_ACTION_TYPE,
    MenuItem,
    health_tap_text,
)

pytestmark = pytest.mark.django_db

_CHAT_ID = 7711

#: Пищевые пункты для проверки ВОРОТ §25 п.6 — оба, и ботовый, и экранный.
#:
#: Производственный кортеж после DRF-1547 несёт ОДИН пункт — ботовый
#: дневник (§37 п.5). Сканер еды из него по-прежнему снят (§33: за ним нет
#: рабочего экрана, ``guardProd`` бросает ``StubNotWiredError``), но
#: механизм ворот обязан оставаться проверенным и для экранного пункта —
#: иначе он молча разучится работать к тому дню, когда сканер вернётся.
_GATED_ITEMS: tuple[MenuItem, ...] = (
    MenuItem(
        label="Дневник питания",
        callback="дневник питания",
        line="дневник питания — что вы ели и пили",
        where="bot",
        surface="food_diary",
    ),
    MenuItem(
        label="Сканер еды",
        callback="open_food_scan",
        line="сканер еды — снять тарелку и увидеть состав",
        where="miniapp",
        warning="Для снимка тарелки открою сканер.",
    ),
)

#: Семь пунктов главного меню (§37) — подпись и payload поимённо.
#:
#: Парная положительная стража к каждому «этого больше нет» (DRF-1411).
#: Без неё «починка», стирающая меню целиком, была бы зелёной.
_SEVEN_MAIN: tuple[tuple[str, str], ...] = (
    ("Подобрать услугу", "Помоги подобрать услугу"),
    ("Найти салон", "cb:catalog:salons"),
    ("Записаться", "cb:menu:book"),
    ("Мои записи", "cb:menu:my_bookings"),
    ("Моя цель", "cb:open:goal_select"),
    ("Профиль", "cb:open:profile"),
    ("Ещё", CALLBACK_EXTRA_OPEN),
)


# --------------------------------------------------------------------------- #
# Оснастка                                                                     #
# --------------------------------------------------------------------------- #
@pytest.fixture(autouse=True)
def _onboarding_on(settings):
    """Флаг живой, как на пилоте: экран C01 обязан продолжать работать."""
    settings.GLOBAL_BOT_ONBOARDING = True
    settings.MAX_BOT_WEB_APP = "aylabot"
    settings.MAX_MINIAPP_URL = ""


@pytest.fixture(autouse=True)
def _no_chat_actions(monkeypatch):
    monkeypatch.setattr(
        "apps.channels.max.outbound.send_chat_action",
        lambda **kwargs: {"ok": True},
    )


@pytest.fixture(autouse=True)
def _no_intent_llm(monkeypatch):
    monkeypatch.setattr(max_handler, "resolve_and_log_turn_intent", MagicMock(return_value=None))


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
    """Консьерж-шпион: он и есть та проза, которую меню обязано вытеснить."""
    from apps.orchestrator.discovery import DiscoveryReply

    spy = MagicMock(
        return_value=DiscoveryReply(text="Расскажи чуть подробнее, что беспокоит?", persisted=False)
    )
    monkeypatch.setattr("apps.orchestrator.concierge.generate_concierge_reply", spy)
    return spy


@pytest.fixture
def nutrition_on(settings):
    settings.NUTRITION_ENABLED = True
    return settings


@pytest.fixture
def nutrition_items(monkeypatch):
    """Вернуть пищевые пункты в кортеж на время одного теста (DRF-1543).

    Подменяется глобал ``marketplace.NUTRITION_ITEMS`` — тот самый,
    который читают и построитель клавиатуры, и ``health_need_surface`` в
    ``handler._route_health_callback``. Механизм ворот при этом настоящий:
    меняется только состав.
    """
    monkeypatch.setattr(menu_marketplace, "NUTRITION_ITEMS", _GATED_ITEMS)
    return _GATED_ITEMS


@pytest.fixture
def health_consent(monkeypatch):
    def _set(granted: bool) -> None:
        monkeypatch.setattr("apps.consent.health.is_granted", lambda _bot_user: granted)

    _set(False)
    return _set


def _msg(*, text: str, user_id: int, mid: str) -> dict:
    return {
        "update_type": "message_created",
        "timestamp": 1731320000000,
        "message": {
            "sender": {"user_id": user_id, "name": "Ирина"},
            "recipient": {"chat_id": _CHAT_ID, "chat_type": "dialog"},
            "body": {"mid": mid, "seq": 1, "text": text, "attachments": []},
        },
    }


def _tap(*, payload: str, user_id: int, callback_id: str) -> dict:
    return {
        "update_type": "message_callback",
        "timestamp": 1731320000000,
        "callback": {
            "callback_id": callback_id,
            "user": {"user_id": user_id, "name": "Ирина"},
            "payload": payload,
        },
        "message": {"recipient": {"chat_id": _CHAT_ID, "chat_type": "dialog"}},
    }


def _welcomed(user_id: int):
    """Человек, уже прошедший первый контакт: C01 его ход не забирает."""
    from django.utils import timezone

    from apps.consent.services import record_global_consent

    bot_user = resolve_or_create_global_bot_user(
        channel="max", channel_user_id=str(user_id), chat_id=str(_CHAT_ID)
    )
    bot_user.welcomed_at = timezone.now()
    bot_user.save(update_fields=["welcomed_at"])
    record_global_consent(
        bot_user,
        consent_type="personal_data",
        source="test:drf1491",
        document_version="welcome-s2-v1",
    )
    bot_user.refresh_from_db()
    return bot_user, resolve_active_global_conversation(bot_user)


def _keyboard(call: dict) -> list[dict]:
    """Плоский список кнопок MAX из отправленного сообщения."""
    attachments = call["attachments"]
    assert attachments, f"клавиатуры нет вовсе: {call['text']!r}"
    rows = attachments[0]["payload"]["buttons"]
    return [btn for row in rows for btn in row]


def _payloads(call: dict) -> list[str]:
    return [btn.get("payload", "") for btn in _keyboard(call) if btn.get("type") == "callback"]


def _open_app_payloads(call: dict) -> list[str]:
    return [btn.get("payload", "") for btn in _keyboard(call) if btn.get("type") == "open_app"]


def _action_types(conversation) -> list[str]:
    from apps.conversations.models import Message

    return list(
        Message.all_tenants.filter(conversation_id=conversation.id, role="assistant")
        .order_by("created_at")
        .values_list("action_type", flat=True)
    )


def _user_messages(conversation) -> list[str]:
    from apps.conversations.models import Message

    return list(
        Message.all_tenants.filter(conversation_id=conversation.id, role="user")
        .order_by("created_at")
        .values_list("content", flat=True)
    )


# --------------------------------------------------------------------------- #
# 1. «Что ты умеешь?» отвечает меню, а не прозой                               #
# --------------------------------------------------------------------------- #
class TestCapabilitiesQuestionAnswersWithAMenu:
    """§25 п.2 — «отвечаем меню, а не свободной прозой»."""

    @pytest.mark.parametrize("text", ["что ты умеешь?", "Помощь", "меню", "/help"])
    def test_answers_with_a_keyboard_and_never_reaches_the_concierge(
        self, sent, fake_redis, concierge, text
    ):
        _welcomed(70101)

        max_handler.handle_global_max_event(_msg(text=text, user_id=70101, mid=f"m-{text}"))

        assert len(sent) == 1, sent
        payloads = _payloads(sent[0])
        # Положительная стража: клавиатура есть и в ней ботовые пункты меню.
        assert payloads, sent[0]
        for _label, payload in _SEVEN_MAIN:
            assert payload in payloads, payloads
        # И только теперь отрицание имеет смысл: прозы консьержа не было.
        assert concierge.call_count == 0
        assert "Расскажи чуть подробнее" not in sent[0]["text"]

    def test_menu_tap_lands_on_the_same_screen(self, sent, fake_redis, concierge):
        """``cb:menu:help`` переводится в фразу выше лестницы — и доезжает."""
        _welcomed(70102)

        max_handler.handle_global_max_event(
            _tap(payload="cb:menu:help", user_id=70102, callback_id="h-1")
        )

        assert len(sent) == 1, sent
        assert _payloads(sent[0]), sent[0]
        assert "cb:menu" not in sent[0]["text"], sent[0]["text"]
        assert concierge.call_count == 0

    def test_a_real_question_still_goes_to_the_concierge(self, sent, fake_redis, concierge):
        """Стража от жадности: ветка меню не отбирает содержательный ход."""
        _welcomed(70103)

        max_handler.handle_global_max_event(
            _msg(text="помоги выбрать массаж спины", user_id=70103, mid="m-real")
        )

        assert len(sent) == 1, sent
        assert concierge.call_count == 1
        assert "Расскажи чуть подробнее" in sent[0]["text"]

    def test_a_brand_new_person_still_gets_first_contact(self, sent, fake_redis, concierge):
        """C01 не тронут: у новичка «что ты умеешь» забирает приветствие.

        Сверяется с КОПИЕЙ первого экрана целиком, а не с подстрокой.
        «Я Ayla» стоит в обоих экранах — и в приветствии
        (``GLOBAL_WELCOME_TEXT``), и во вступлении меню, — так что
        подстрочная стража была бы зелёной при любом исходе, и всю работу
        делало бы отрицание. Это ровно то, что запрещает DRF-1411.
        """
        from apps.channels.max.global_onboarding import GLOBAL_WELCOME_TEXT

        bot_user = resolve_or_create_global_bot_user(
            channel="max", channel_user_id="70104", chat_id=str(_CHAT_ID)
        )
        conversation = resolve_active_global_conversation(bot_user)

        max_handler.handle_global_max_event(_msg(text="что ты умеешь?", user_id=70104, mid="m-new"))

        # Стража на тех же данных: пришёл ровно экран первого контакта.
        assert len(sent) == 1, sent
        assert sent[0]["text"] == GLOBAL_WELCOME_TEXT, sent[0]["text"]
        # Ход записан — есть чему не быть меню.
        assert _action_types(conversation), "ответ бота в переписку не попал"
        # И только теперь отрицание: меню витрины ход не забрало.
        assert MENU_ACTION_TYPE not in _action_types(conversation), _action_types(conversation)


# --------------------------------------------------------------------------- #
# 2. Ветка «не поняла»                                                         #
# --------------------------------------------------------------------------- #
class TestHonestFallbackOnTheGlobalPath:
    """Тап, у которого ветки нет, больше не уезжает в модель сырым."""

    def test_unclaimed_callback_answers_with_the_menu_keyboard(self, sent, fake_redis, concierge):
        _welcomed(70201)

        max_handler.handle_global_max_event(
            _tap(payload="cb:discover:sort_by_price", user_id=70201, callback_id="u-1")
        )

        assert len(sent) == 1, sent
        payloads = _payloads(sent[0])
        assert payloads, sent[0]
        assert "cb:menu:book" in payloads, payloads
        assert sent[0]["text"].startswith("Я пока не поняла"), sent[0]["text"]
        # Отрицание — после стражи: сырой payload модели не доставался.
        assert concierge.call_count == 0

    def test_the_raw_payload_never_appears_in_the_reply(self, sent, fake_redis, concierge):
        _welcomed(70202)

        max_handler.handle_global_max_event(
            _tap(payload="cb:anketa:no_such_step", user_id=70202, callback_id="u-2")
        )

        assert len(sent) == 1 and sent[0]["text"], sent
        assert "cb:anketa" not in sent[0]["text"], sent[0]["text"]

    def test_an_empty_model_answer_becomes_the_fallback_screen(self, sent, fake_redis, monkeypatch):
        """Пустая реплика — худший тупик: человек видит от бота ничего."""
        from apps.orchestrator.discovery import DiscoveryReply

        monkeypatch.setattr(
            "apps.orchestrator.concierge.generate_concierge_reply",
            MagicMock(return_value=DiscoveryReply(text="", persisted=False)),
        )
        _welcomed(70203)

        max_handler.handle_global_max_event(
            _msg(text="а можно как-нибудь иначе", user_id=70203, mid="m-empty")
        )

        assert len(sent) == 1, sent
        assert sent[0]["text"].strip(), "бот ответил пустотой"
        assert _payloads(sent[0]), sent[0]


# --------------------------------------------------------------------------- #
# 3. Принятое совпадает с рисуемым                                             #
# --------------------------------------------------------------------------- #
class TestEveryDrawnButtonIsAccepted:
    """Каждый нарисованный payload кто-то разбирает.

    «Разбирает» здесь ровно то, что написано, и не больше: payload не
    уезжает в модель сырым. Часть ботовых пунктов после подстановки фразы
    отвечает консьерж — это принятое состояние (DRF-1051, таблица в
    ``quick_actions._global_menu_text``), а не недосмотр.

    После §37 в меню появился ТРЕТИЙ вид payload'а — сама ФРАЗА
    («Подобрать услугу», «Дневник питания»). Это не исключение из правила,
    а его предельный случай: тап и есть обычное сообщение, и разбирает
    его та же лестница, что и набранный текст (DRF-1348).
    """

    def test_every_drawn_payload_has_someone_who_takes_it(
        self, sent, fake_redis, concierge, health_consent
    ):
        from apps.channels.max.quick_actions import resolve_tap_text
        from apps.orchestrator.discovery import CALLBACK_CATALOG_SALONS, execute_catalog_callback
        from apps.skills.menu.marketplace import (
            is_extra_callback,
            is_open_callback,
            open_callback_slug,
        )

        _welcomed(70301)
        max_handler.handle_global_max_event(
            _msg(text="что ты умеешь?", user_id=70301, mid="m-draw")
        )

        payloads = _payloads(sent[0])
        assert payloads, sent[0]
        for payload in payloads:
            if payload == CALLBACK_CATALOG_SALONS:
                assert execute_catalog_callback(payload) is not None, payload
            elif payload.startswith("cb:menu:"):
                assert resolve_tap_text(payload), payload
            elif is_extra_callback(payload):
                continue  # своя ветка лестницы, проверена ниже живым тапом
            elif is_open_callback(payload):
                assert open_callback_slug(payload), payload
            elif payload.startswith("cb:"):
                raise AssertionError(f"нарисован payload, которого никто не ждёт: {payload}")
            else:
                # Фраза. Обязана НЕ выглядеть тапом ни для одного резолвера —
                # иначе ниже по течению её подменят или потеряют.
                assert resolve_tap_text(payload) is None, payload

    def test_a_screen_item_opens_a_declared_route_after_the_warning(
        self, sent, fake_redis, concierge
    ):
        """Слаг проверяется там, где он теперь и живёт, — под предупреждением."""
        from apps.skills.welcome.skill import MINIAPP_ROUTES

        _welcomed(70302)
        max_handler.handle_global_max_event(
            _tap(payload="cb:open:profile", user_id=70302, callback_id="o-1")
        )

        slugs = _open_app_payloads(sent[0])
        assert slugs, _keyboard(sent[0])
        for slug in slugs:
            assert slug in MINIAPP_ROUTES, slug


# --------------------------------------------------------------------------- #
# 4. Питание: двое ворот на живом пути                                         #
# --------------------------------------------------------------------------- #
class TestNutritionGatesOnTheLivePath:
    """Механизм §25 п.6 на живом пути — на ВОЗВРАЩЁННЫХ пунктах.

    ``NUTRITION_ITEMS`` пуст с DRF-1543, поэтому строки таблицы «флаг
    задан» проверяются через фикстуру ``nutrition_items``. Что видит
    человек СЕГОДНЯ — в :class:`TestNutritionItemsAreGoneFromTheLiveMenu`.
    """

    def test_flag_unset_means_no_nutrition_items_at_all(
        self, sent, fake_redis, concierge, health_consent, settings, nutrition_items
    ):
        settings.NUTRITION_ENABLED = False
        _welcomed(70401)

        max_handler.handle_global_max_event(
            _tap(payload=CALLBACK_EXTRA_OPEN, user_id=70401, callback_id="n-0")
        )

        buttons = _keyboard(sent[0])
        labels = [b["text"] for b in buttons]
        # Стража: подменю построено, соседние пункты в нём есть.
        # «История визитов» на эту роль больше не годится — она слита с
        # «Моими записями» (OD-UI-1), — и её место занимает «Помощь».
        assert "Помощь" in labels, labels
        assert "Назад" in labels, labels
        for item in _GATED_ITEMS:
            assert item.label not in labels, labels

    def test_flag_set_without_consent_leads_to_the_consent_request(
        self, sent, fake_redis, concierge, nutrition_on, health_consent, nutrition_items
    ):
        _welcomed(70402)

        max_handler.handle_global_max_event(
            _tap(payload=CALLBACK_EXTRA_OPEN, user_id=70402, callback_id="n-00")
        )
        payloads = _payloads(sent[0])
        assert f"{CALLBACK_HEALTH_NEED_PREFIX}food_scan" in payloads, payloads
        assert f"{CALLBACK_HEALTH_NEED_PREFIX}food_diary" in payloads, payloads

        max_handler.handle_global_max_event(
            _tap(
                payload=f"{CALLBACK_HEALTH_NEED_PREFIX}food_scan",
                user_id=70402,
                callback_id="n-1",
            )
        )

        assert len(sent) == 2, sent
        assert sent[1]["text"] == HEALTH_REQUEST_TEXT
        # Тап привёл на запрос согласия, а НЕ в поверхность.
        assert "open_food_scan" not in _open_app_payloads(sent[1])
        assert CALLBACK_HEALTH_DECLINE in _payloads(sent[1])

    def test_flag_set_with_consent_opens_the_surface(
        self, sent, fake_redis, concierge, nutrition_on, health_consent, nutrition_items
    ):
        """Ботовый пункт отвечает фразой, экранный — предупреждением."""
        health_consent(True)
        _welcomed(70403)

        max_handler.handle_global_max_event(
            _tap(payload=CALLBACK_EXTRA_OPEN, user_id=70403, callback_id="n-2")
        )

        payloads = _payloads(sent[0])
        assert payloads, _keyboard(sent[0])
        assert "дневник питания" in payloads, payloads
        assert "cb:open:food_scan" in payloads, payloads
        # И ни один пищевой пункт не ведёт на запрос согласия.
        assert not [p for p in payloads if p.startswith(CALLBACK_HEALTH_NEED_PREFIX)]


# --------------------------------------------------------------------------- #
# 4a. DRF-1543: сегодня пищевых пунктов в живом меню нет                       #
# --------------------------------------------------------------------------- #
class TestNutritionItemsAreGoneFromTheLiveMenu:
    """Вариант A DRF-1543 — глазами человека на пилоте.

    Замер боевого контура: ``NUTRITION_ENABLED = true``. То есть самая
    «разрешительная» строка таблицы, и именно в ней человек с согласием
    ``HEALTH`` доходил до падающего экрана. Пунктов быть не должно.
    """

    def test_the_main_menu_offers_seven_items_and_no_food(
        self, sent, fake_redis, concierge, nutrition_on, health_consent
    ):
        """Питание живёт в «Ещё», а не в главном меню (§37)."""
        health_consent(True)
        _welcomed(70601)

        max_handler.handle_global_max_event(
            _msg(text="что ты умеешь?", user_id=70601, mid="m-1543")
        )

        buttons = _keyboard(sent[0])
        labels = [b["text"] for b in buttons]
        payloads = _payloads(sent[0]) + _open_app_payloads(sent[0])
        text = sent[0]["text"]
        # Положительная стража НА ТЕХ ЖЕ ДАННЫХ: семь пунктов на месте,
        # payload каждого не изменился, перечень в тексте собран.
        assert len(buttons) == len(_SEVEN_MAIN), labels
        for label, payload in _SEVEN_MAIN:
            assert label in labels, labels
            assert payload in payloads, payloads
        assert MAIN_ITEMS[0].line in text, text
        # И только теперь отрицание.
        assert "Дневник питания" not in labels, labels
        assert "Сканер еды" not in labels, labels
        assert not [p for p in payloads if p.startswith(CALLBACK_HEALTH_NEED_PREFIX)], payloads

    def test_the_food_scanner_is_still_gone_but_the_diary_came_back(
        self, sent, fake_redis, concierge, nutrition_on, health_consent
    ):
        """§37 п.5 отменил §33 ТОЛЬКО для дневника — сканер остаётся снят.

        За сканером по-прежнему нет живого экрана (``guardProd`` бросает
        ``StubNotWiredError``), и признак «нет пути — нет кнопки» на него
        распространяется без изменений.
        """
        health_consent(True)
        _welcomed(70603)

        max_handler.handle_global_max_event(
            _tap(payload=CALLBACK_EXTRA_OPEN, user_id=70603, callback_id="x-1")
        )

        labels = [b["text"] for b in _keyboard(sent[0])]
        payloads = _payloads(sent[0]) + _open_app_payloads(sent[0])
        # Стража: дневник вернулся, и он ботовый — payload это фраза.
        assert "Дневник питания" in labels, labels
        assert "дневник питания" in payloads, payloads
        # Отрицание: сканер не вернулся вместе с ним.
        assert "Сканер еды" not in labels, labels
        assert "open_food_scan" not in payloads, payloads

    def test_a_stale_food_payload_from_chat_history_answers_with_the_menu(
        self, sent, fake_redis, concierge, nutrition_on, health_consent
    ):
        """Кнопка из истории чата не просит медданные ради снятого экрана.

        Клавиатура живёт в переписке дольше выкладки. У людей с 06.09.2026
        15:32 ``cb:health:need:food_scan`` остался в ленте, и тап по нему
        обязан вернуть меню — а не запрос особой категории персданных ради
        поверхности, которой нет.
        """
        health_consent(True)
        _welcomed(70602)

        max_handler.handle_global_max_event(
            _tap(
                payload=f"{CALLBACK_HEALTH_NEED_PREFIX}food_scan",
                user_id=70602,
                callback_id="s-1",
            )
        )

        assert len(sent) == 1, sent
        # Стража: ответ есть и это рабочее меню.
        assert "cb:menu:book" in _payloads(sent[0]), _payloads(sent[0])
        # Отрицание: экрана согласия человек не увидел.
        assert sent[0]["text"] != HEALTH_REQUEST_TEXT, sent[0]["text"]
        assert CALLBACK_HEALTH_DECLINE not in _payloads(sent[0]), _payloads(sent[0])


# --------------------------------------------------------------------------- #
# 5. Отказ уважается                                                           #
# --------------------------------------------------------------------------- #
class TestRefusalIsRespected:
    """Канон 2.5 «без понуканий», 2.6 «автономия клиента абсолютна»."""

    def test_decline_returns_to_the_menu(
        self, sent, fake_redis, concierge, nutrition_on, health_consent, nutrition_items
    ):
        _welcomed(70501)

        max_handler.handle_global_max_event(
            _tap(
                payload=f"{CALLBACK_HEALTH_NEED_PREFIX}food_diary",
                user_id=70501,
                callback_id="d-1",
            )
        )
        max_handler.handle_global_max_event(
            _tap(payload=CALLBACK_HEALTH_DECLINE, user_id=70501, callback_id="d-2")
        )

        assert len(sent) == 2, sent
        assert sent[1]["text"] == HEALTH_DECLINED_TEXT
        # Возврат назад — это меню, а не пустой экран.
        assert "cb:menu:book" in _payloads(sent[1]), _payloads(sent[1])

    def test_no_second_consent_request_in_the_same_dialog(
        self, sent, fake_redis, concierge, nutrition_on, health_consent, nutrition_items
    ):
        _, conversation = _welcomed(70502)

        max_handler.handle_global_max_event(
            _tap(
                payload=f"{CALLBACK_HEALTH_NEED_PREFIX}food_scan",
                user_id=70502,
                callback_id="r-1",
            )
        )
        max_handler.handle_global_max_event(
            _tap(payload=CALLBACK_HEALTH_DECLINE, user_id=70502, callback_id="r-2")
        )
        max_handler.handle_global_max_event(
            _tap(
                payload=f"{CALLBACK_HEALTH_NEED_PREFIX}food_diary",
                user_id=70502,
                callback_id="r-3",
            )
        )

        # Стража: запрос БЫЛ показан ровно один раз, и отказ записан.
        assert sent[0]["text"] == HEALTH_REQUEST_TEXT, sent[0]["text"]
        assert HEALTH_DECLINE_ACTION_TYPE in _action_types(conversation)
        # Только теперь отрицание: третьего хода запрос не повторил.
        assert len(sent) == 3, sent
        assert sent[2]["text"] == HEALTH_DECLINED_EARLIER_TEXT
        assert sent[2]["text"] != HEALTH_REQUEST_TEXT
        assert CALLBACK_HEALTH_DECLINE not in _payloads(sent[2]), _payloads(sent[2])
        # И выход не мёртвый: клавиатура меню на месте.
        assert "cb:menu:book" in _payloads(sent[2]), _payloads(sent[2])

    def test_the_promise_survives_the_outbound_guard_eating_the_marker(
        self,
        sent,
        fake_redis,
        concierge,
        nutrition_on,
        health_consent,
        monkeypatch,
        nutrition_items,
    ):
        """Отказ помнится по ДВУМ следам, и первый переживает сторожа.

        Сторож исходящего при блокировке заменяет реплику целиком и
        ставит свой ``action_type`` — метка отказа пропадает. Реплика же
        ЧЕЛОВЕКА («Не сейчас») пишется раньше сторожа, и по ней обещание
        «больше не спрошу» продолжает держаться.
        """
        from apps.orchestrator.safety.gate import OUTBOUND_ACTION_TYPE, OutboundGuardOutcome

        _, conversation = _welcomed(70503)

        max_handler.handle_global_max_event(
            _tap(
                payload=f"{CALLBACK_HEALTH_NEED_PREFIX}food_scan",
                user_id=70503,
                callback_id="g-1",
            )
        )
        # Стража: запрос показан — значит, есть чему не повториться.
        assert sent[0]["text"] == HEALTH_REQUEST_TEXT, sent[0]["text"]

        # Блокируем РОВНО один ход. ``monkeypatch.undo()`` здесь нельзя:
        # он снял бы и заглушки фикстур — они делят один и тот же
        # ``monkeypatch`` на тест, — и следующий ход пошёл бы в живой Redis.
        blocking = {"on": True}
        real_guard = max_handler.guard_outbound

        def maybe_block(text, **kw):
            if blocking["on"]:
                return OutboundGuardOutcome(allowed=False, text="Тут нужен человек.")
            return real_guard(text, **kw)

        monkeypatch.setattr(max_handler, "guard_outbound", maybe_block)
        max_handler.handle_global_max_event(
            _tap(payload=CALLBACK_HEALTH_DECLINE, user_id=70503, callback_id="g-2")
        )
        blocking["on"] = False

        # Ходы записаны, и сторож действительно сработал — есть чему быть
        # потерянным; без этой стражи отрицание ниже зеленело бы на пустой
        # выборке.
        assert OUTBOUND_ACTION_TYPE in _action_types(conversation), _action_types(conversation)
        # Метка ответа бота действительно потеряна — иначе тест ничего не ловит.
        assert HEALTH_DECLINE_ACTION_TYPE not in _action_types(conversation), _action_types(
            conversation
        )
        # А реплика человека на месте, и по ней отказ помнится.
        assert health_tap_text()[CALLBACK_HEALTH_DECLINE] in _user_messages(conversation)

        max_handler.handle_global_max_event(
            _tap(
                payload=f"{CALLBACK_HEALTH_NEED_PREFIX}food_diary",
                user_id=70503,
                callback_id="g-3",
            )
        )
        assert sent[-1]["text"] == HEALTH_DECLINED_EARLIER_TEXT, sent[-1]["text"]

    def test_an_unreadable_history_never_claims_the_person_refused(
        self,
        sent,
        fake_redis,
        concierge,
        nutrition_on,
        health_consent,
        monkeypatch,
        nutrition_items,
    ):
        """Осторожность в поведении не обязана быть враньём в словах.

        Сбой чтения истории толкуется в пользу «не спрашивать» — но
        сказать при этом «я про согласие больше не напоминаю» человеку,
        который НИКОГДА не отказывался, значит утверждать про него
        неправду.
        """
        from apps.conversations.models import Message

        _welcomed(70504)

        real_manager = Message.all_tenants

        class _UnreadableHistory:
            """Ломается ровно на чтении; запись хода идёт как обычно."""

            def __init__(self, inner):
                self._inner = inner

            def filter(self, *a, **kw):
                raise RuntimeError("история недоступна")

            def __getattr__(self, name):
                return getattr(self._inner, name)

        monkeypatch.setattr(Message, "all_tenants", _UnreadableHistory(real_manager))
        max_handler.handle_global_max_event(
            _tap(
                payload=f"{CALLBACK_HEALTH_NEED_PREFIX}food_scan",
                user_id=70504,
                callback_id="u-1",
            )
        )

        # Стража: ход не потерян и человек получил рабочий выход.
        assert len(sent) == 1 and sent[0]["text"], sent
        assert "cb:menu:book" in _payloads(sent[0]), _payloads(sent[0])
        assert sent[0]["text"] == HEALTH_CHECK_FAILED_TEXT, sent[0]["text"]
        # И только теперь отрицание: ни запроса согласия, ни ложного «вы отказались».
        assert sent[0]["text"] != HEALTH_REQUEST_TEXT
        assert sent[0]["text"] != HEALTH_DECLINED_EARLIER_TEXT


# --------------------------------------------------------------------------- #
# 5-bis. Что тап семейства значит в ПЕРЕПИСКЕ                                  #
# --------------------------------------------------------------------------- #
class TestHealthTapsNeverLandRawInHistory:
    """Сырой ``cb:health:`` в истории — это сырой payload в промпте модели.

    Гейт персистенса на глобальном пути устроен списком исключений: форма,
    которую ни один резолвер не разобрал, пишется дословно и с ролью
    ``user``. Консьерж читает эту историю на следующих ходах, и у него
    есть нутриционные инструменты — то есть строку «cb:health:need:
    food_scan» он охотно истолкует как просьбу человека про еду, сразу
    после того как бот пообещал эту тему больше не поднимать.
    """

    def test_taps_are_stored_as_the_phrase_the_button_carried(
        self, sent, fake_redis, concierge, nutrition_on, health_consent, nutrition_items
    ):
        _, conversation = _welcomed(70801)

        max_handler.handle_global_max_event(_msg(text="что ты умеешь?", user_id=70801, mid="h-0"))
        max_handler.handle_global_max_event(
            _tap(
                payload=f"{CALLBACK_HEALTH_NEED_PREFIX}food_scan",
                user_id=70801,
                callback_id="h-1",
            )
        )
        max_handler.handle_global_max_event(
            _tap(payload=CALLBACK_HEALTH_DECLINE, user_id=70801, callback_id="h-2")
        )

        history = _user_messages(conversation)
        # Стража: выборка не пуста, и в ней то, что человек НАБРАЛ, плюс
        # решения, которые он принял кнопками.
        assert "что ты умеешь?" in history, history
        phrases = health_tap_text()
        assert phrases[f"{CALLBACK_HEALTH_NEED_PREFIX}food_scan"] in history, history
        assert phrases[CALLBACK_HEALTH_DECLINE] in history, history
        # И только теперь отрицание: сырых payload'ов среди них нет.
        assert [line for line in history if line.startswith("cb:")] == [], history

    def test_a_typed_lookalike_is_still_the_person_s_own_words(
        self, sent, fake_redis, concierge, nutrition_on, health_consent, nutrition_items
    ):
        """Разбор по ФОРМЕ, а не по префиксу (правило C01)."""
        typed = "cb:health: это что такое?"
        _, conversation = _welcomed(70802)

        max_handler.handle_global_max_event(_msg(text=typed, user_id=70802, mid="h-3"))

        history = _user_messages(conversation)
        assert history == [typed], history
        # Ход ушёл к консьержу как обычный текст, а не в экран согласия.
        assert concierge.call_count == 1
        assert sent[0]["text"] != HEALTH_REQUEST_TEXT


# --------------------------------------------------------------------------- #
# 5-ter. Первые ворота проверяются и на тапе, не только при отрисовке          #
# --------------------------------------------------------------------------- #
class TestFlagIsCheckedOnTheTapToo:
    def test_stale_keyboard_does_not_ask_for_health_consent_after_the_flag_went_off(
        self, sent, fake_redis, concierge, health_consent, settings, nutrition_items
    ):
        """Клавиатура в истории чата живёт дольше флага в окружении.

        Человек увидел меню при включённом питании, но не тапнул. Флаг
        выключили. Тап по старой кнопке не должен просить согласие на
        особую категорию персданных ради поверхности, которой больше нет.
        """
        settings.NUTRITION_ENABLED = True
        _welcomed(70803)
        max_handler.handle_global_max_event(
            _tap(payload=CALLBACK_EXTRA_OPEN, user_id=70803, callback_id="f-0")
        )
        # Стража: пункт действительно был нарисован, тап настоящий.
        assert f"{CALLBACK_HEALTH_NEED_PREFIX}food_scan" in _payloads(sent[0]), _payloads(sent[0])

        settings.NUTRITION_ENABLED = False
        max_handler.handle_global_max_event(
            _tap(
                payload=f"{CALLBACK_HEALTH_NEED_PREFIX}food_scan",
                user_id=70803,
                callback_id="f-1",
            )
        )

        assert len(sent) == 2, sent
        assert sent[1]["text"] != HEALTH_REQUEST_TEXT
        # Выход не мёртвый: человек получает меню — уже без пищевых пунктов.
        assert "cb:menu:book" in _payloads(sent[1]), _payloads(sent[1])
        assert not [p for p in _payloads(sent[1]) if p.startswith(CALLBACK_HEALTH_NEED_PREFIX)], (
            _payloads(sent[1])
        )


# --------------------------------------------------------------------------- #
# 5-quater. Незаконченное важнее оглавления                                    #
# --------------------------------------------------------------------------- #
class TestUnfinishedFlowsOutrankTheMenu:
    def test_help_mid_anketa_stays_with_the_anketa(self, sent, fake_redis, concierge):
        """«помощь» посреди анкеты — это ответ анкете, а не выход из неё.

        ``is_structured_nutrition_turn`` забирает ЛЮБОЙ текст, пока FSM
        жив. Если бы ветка меню стояла верхним ``elif`` лестницы, человек
        получил бы оглавление, а незакрытый вопрос анкеты остался бы
        висеть молча — и следующая же его реплика была бы съедена как
        ответ на этот вопрос.
        """
        from apps.skills.base import SkillResult

        _welcomed(70804)
        claimed: list[str] = []

        def fake_nutrition(*, text, attachments, bot_user, conversation, trace_id):
            claimed.append(text)
            return SkillResult(reply_text="Сколько вам лет?", action_type="nutrition_anketa")

        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(max_handler, "try_handle_structured_nutrition_turn", fake_nutrition)
            max_handler.handle_global_max_event(_msg(text="помощь", user_id=70804, mid="a-1"))

        # Стража: анкета ход получила и ответила своим вопросом.
        assert claimed == ["помощь"], claimed
        assert sent[0]["text"] == "Сколько вам лет?", sent[0]["text"]
        # И только теперь отрицание: оглавление её не перебило.
        assert "cb:menu:book" not in _payloads_or_empty(sent[0])


def _payloads_or_empty(call: dict) -> list[str]:
    """``_payloads``, но без требования клавиатуры — её тут может не быть."""
    if not call.get("attachments"):
        return []
    return _payloads(call)


# --------------------------------------------------------------------------- #
# 6. Вырождение конфигурации                                                   #
# --------------------------------------------------------------------------- #
class TestZeroConfigStillShipsAWorkingMenu:
    def test_menu_survives_without_any_miniapp_setting(
        self, sent, fake_redis, concierge, nutrition_on, health_consent, settings
    ):
        settings.MAX_BOT_WEB_APP = ""
        settings.MAX_MINIAPP_URL = ""
        _welcomed(70601)

        max_handler.handle_global_max_event(
            _msg(text="что ты умеешь?", user_id=70601, mid="m-zero")
        )

        # Стража на ТЕХ ЖЕ данных: клавиатура непуста и это ботовая половина.
        assert _keyboard(sent[0]), sent[0]
        payloads = _payloads(sent[0])
        assert payloads == [item.callback for item in MAIN_ITEMS if item.where == "bot"], payloads
        # И только теперь отрицание: ни одной кнопки, которую негде открыть.
        assert not _open_app_payloads(sent[0]), _keyboard(sent[0])
        link_buttons = [b for b in _keyboard(sent[0]) if b.get("type") == "link"]
        assert link_buttons == [], link_buttons


# --------------------------------------------------------------------------- #
# 7. Салонный путь своё меню не потерял                                        #
# --------------------------------------------------------------------------- #
class TestTenantPathKeptItsOwnMenu:
    """Парная положительная проверка: витрине добавили, салону не сломали."""

    def test_salon_help_and_fallback_are_untouched(self, sent, fake_redis, settings, monkeypatch):
        from uuid import uuid4

        from apps.skills.menu.replies import FALLBACK_TEXT, HELP_TEXT
        from apps.tenancy.context import tenant_scope, trace_id_scope
        from apps.tenancy.models import Tenant

        settings.STRICT_TENANT_SCOPE = "strict"
        tenant = Tenant.objects.create(slug="drf1491-salon", name="DRF-1491 Salon")

        def _salon_payload(text: str, mid: str) -> dict:
            return {
                "update_type": "message_created",
                "timestamp": 1731320000000,
                "message": {
                    "sender": {"user_id": 70701, "name": "Ольга"},
                    "recipient": {"chat_id": 70801, "chat_type": "dialog"},
                    "body": {"mid": mid, "seq": 1, "text": text, "attachments": []},
                },
            }

        from django.utils import timezone

        from apps.identity.services import resolve_or_create_bot_user

        with tenant_scope(tenant), trace_id_scope(str(uuid4())):
            bot_user = resolve_or_create_bot_user(
                channel="max", channel_user_id="70701", chat_id="70801"
            )
            bot_user.welcomed_at = timezone.now()
            bot_user.save(update_fields=["welcomed_at"])
            max_handler.handle_max_event(_salon_payload("помощь", "s-1"))
            max_handler.handle_max_event(_salon_payload("ааааа что происходит", "s-2"))

        assert len(sent) == 2, sent
        assert sent[0]["text"] == HELP_TEXT
        assert sent[1]["text"] == FALLBACK_TEXT
        # Салонная копия — про салон, и меню витрины её не подменило.
        assert "Формула тела" in sent[0]["text"]
