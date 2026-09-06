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
  ведёт на запрос согласия, а не в поверхность;
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
from apps.skills.menu.marketplace import (
    BOT_ITEMS,
    CALLBACK_HEALTH_DECLINE,
    CALLBACK_HEALTH_NEED_PREFIX,
    HEALTH_DECLINE_ACTION_TYPE,
    HEALTH_DECLINED_EARLIER_TEXT,
    HEALTH_DECLINED_TEXT,
    HEALTH_REQUEST_TEXT,
    NUTRITION_ITEMS,
)

pytestmark = pytest.mark.django_db

_CHAT_ID = 7711


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
        for item in BOT_ITEMS:
            assert item.callback in payloads, payloads
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
        """C01 не тронут: у новичка «что ты умеешь» забирает приветствие."""
        resolve_or_create_global_bot_user(
            channel="max", channel_user_id="70104", chat_id=str(_CHAT_ID)
        )

        max_handler.handle_global_max_event(_msg(text="что ты умеешь?", user_id=70104, mid="m-new"))

        assert len(sent) == 1, sent
        # Стража: ответ вообще пришёл — и это приветствие, а не оглавление.
        assert sent[0]["text"], sent[0]
        assert "Я Ayla" in sent[0]["text"] or "Привет" in sent[0]["text"], sent[0]["text"]
        assert "открывается отдельным экраном" not in sent[0]["text"]


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
    """Каждый нарисованный ``cb:`` payload кто-то разбирает."""

    def test_bot_payloads_resolve_to_a_route(self, sent, fake_redis, concierge, health_consent):
        from apps.channels.max.quick_actions import resolve_tap_text
        from apps.orchestrator.discovery import CALLBACK_CATALOG_SALONS, execute_catalog_callback

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
            else:
                raise AssertionError(f"нарисован payload, которого никто не ждёт: {payload}")

    def test_screen_payloads_are_declared_routes(self, sent, fake_redis, concierge):
        from apps.skills.welcome.skill import MINIAPP_ROUTES

        _welcomed(70302)
        max_handler.handle_global_max_event(
            _msg(text="что ты умеешь?", user_id=70302, mid="m-draw2")
        )

        slugs = _open_app_payloads(sent[0])
        assert slugs, _keyboard(sent[0])
        for slug in slugs:
            assert slug in MINIAPP_ROUTES, slug


# --------------------------------------------------------------------------- #
# 4. Питание: двое ворот на живом пути                                         #
# --------------------------------------------------------------------------- #
class TestNutritionGatesOnTheLivePath:
    def test_flag_unset_means_no_nutrition_items_at_all(
        self, sent, fake_redis, concierge, health_consent, settings
    ):
        settings.NUTRITION_ENABLED = False
        _welcomed(70401)

        max_handler.handle_global_max_event(_msg(text="что ты умеешь?", user_id=70401, mid="m-n0"))

        buttons = _keyboard(sent[0])
        labels = [b["text"] for b in buttons]
        # Стража: меню построено, экранные пункты в нём есть.
        assert "👤 Профиль" in labels, labels
        for item in NUTRITION_ITEMS:
            assert item.label not in labels, labels

    def test_flag_set_without_consent_leads_to_the_consent_request(
        self, sent, fake_redis, concierge, nutrition_on, health_consent
    ):
        _welcomed(70402)

        max_handler.handle_global_max_event(_msg(text="что ты умеешь?", user_id=70402, mid="m-n1"))
        payloads = _payloads(sent[0])
        assert f"{CALLBACK_HEALTH_NEED_PREFIX}food_scan" in payloads, payloads

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
        self, sent, fake_redis, concierge, nutrition_on, health_consent
    ):
        health_consent(True)
        _welcomed(70403)

        max_handler.handle_global_max_event(_msg(text="что ты умеешь?", user_id=70403, mid="m-n2"))

        slugs = _open_app_payloads(sent[0])
        assert slugs, _keyboard(sent[0])
        for item in NUTRITION_ITEMS:
            assert item.callback in slugs, slugs
        # И ни один пищевой пункт не ведёт на запрос согласия.
        assert not [p for p in _payloads(sent[0]) if p.startswith(CALLBACK_HEALTH_NEED_PREFIX)]


# --------------------------------------------------------------------------- #
# 5. Отказ уважается                                                           #
# --------------------------------------------------------------------------- #
class TestRefusalIsRespected:
    """Канон 2.5 «без понуканий», 2.6 «автономия клиента абсолютна»."""

    def test_decline_returns_to_the_menu(
        self, sent, fake_redis, concierge, nutrition_on, health_consent
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
        self, sent, fake_redis, concierge, nutrition_on, health_consent
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
                payload=f"{CALLBACK_HEALTH_NEED_PREFIX}wellness",
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
        assert payloads == [item.callback for item in BOT_ITEMS], payloads
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
