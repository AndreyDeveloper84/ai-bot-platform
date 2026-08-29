"""DRF-990 — нажатия анкеты не ложатся в историю сырым payload'ом.

Дефект: гейт персистенса на глобальном пути
(``apps/channels/max/handler.py``) пропускал мимо истории только
``cb:book:*`` (DRF-988), ``cb:catalog:*`` (DRF-1304) и промежуточный тап
уточнения (DRF-1362). ``cb:anketa:*`` не входил ни в одно из семейств,
поэтому в глобальную историю ложилась буквальная строка
``cb:anketa:choice:gender:female`` с ролью ``user`` — и на следующем ходу
консьерж читал её как то, что человек написал ему словами.

DRF-1268 (детерминированная маршрутизация ``cb:anketa:*`` в nutrition-навык)
эту дыру НЕ закрывает: он про текущий ход, а история пишется для будущих.
Поэтому проверки здесь смотрят в таблицу сообщений, а не в ответ бота.

Правило контура: отрицательному утверждению нужна положительная стража на
тех же данных. Рядом с «в истории нет ``cb:``» всюду стоит проверка, что
история вообще непуста — иначе тест зеленел бы на пустой выборке.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from apps.channels.max import handler as max_handler
from apps.conversations.services import resolve_active_global_conversation
from apps.identity.services.resolver import resolve_or_create_global_bot_user
from apps.orchestrator.memory import short_term

pytestmark = pytest.mark.django_db


# --------------------------------------------------------------------------- #
# Оснастка — та же, что у C01 (apps/channels/tests/test_first_contact_c01.py)   #
# --------------------------------------------------------------------------- #
@pytest.fixture(autouse=True)
def _onboarding_on(settings):
    settings.GLOBAL_BOT_ONBOARDING = True


@pytest.fixture(autouse=True)
def _no_chat_actions(monkeypatch):
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
    """Шпион на месте модели: анкета до неё доходить не должна."""
    from apps.orchestrator.discovery import DiscoveryReply

    spy = MagicMock(return_value=DiscoveryReply(text="Расскажи чуть подробнее?", persisted=False))
    monkeypatch.setattr("apps.orchestrator.concierge.generate_concierge_reply", spy)
    return spy


def _tap(*, payload: str, user_id: int, callback_id: str, chat_id: int = 8899) -> dict:
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


def _msg(*, text: str, user_id: int, mid: str, chat_id: int = 8899) -> dict:
    return {
        "update_type": "message_created",
        "timestamp": 1731320000000,
        "message": {
            "sender": {"user_id": user_id, "name": "Ирина"},
            "recipient": {"chat_id": chat_id, "chat_type": "dialog"},
            "body": {"mid": mid, "seq": 1, "text": text, "attachments": []},
        },
    }


def _welcomed_user(user_id: int):
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
        source="test:drf990",
        document_version="welcome-s2-v1",
    )
    bot_user.refresh_from_db()
    return bot_user, resolve_active_global_conversation(bot_user)


def _user_messages(conversation) -> list[str]:
    from apps.conversations.models import Message

    return list(
        Message.all_tenants.filter(conversation_id=conversation.id, role="user")
        .order_by("created_at")
        .values_list("content", flat=True)
    )


# --------------------------------------------------------------------------- #
# Красный прогон DRF-990                                                       #
# --------------------------------------------------------------------------- #
class TestAnketaTapsDoNotLandInHistoryRaw:
    def test_gender_choice_tap_is_not_a_raw_payload_in_history(self, sent, fake_redis, concierge):
        """Дословный сценарий тикета: тап по «Женский» после старта анкеты.

        Утверждение отрицательное («ни одной строки на ``cb:``»), поэтому
        рядом стоит положительная стража: история непуста и в ней ровно тот
        один ход, который человек в неё внёс.
        """
        _, conversation = _welcomed_user(99001)

        max_handler.handle_global_max_event(
            _tap(payload="cb:anketa:start", user_id=99001, callback_id="an-1")
        )
        max_handler.handle_global_max_event(
            _tap(payload="cb:anketa:choice:gender:female", user_id=99001, callback_id="an-2")
        )

        history = _user_messages(conversation)

        # Положительная стража: выборка не пуста — иначе «нет cb:» зелено ни о чём.
        assert history, "история пуста — отрицательная проверка ниже ничего не доказывает"
        assert not [line for line in history if line.startswith("cb:")], history

    def test_the_answer_survives_as_the_phrase_the_person_gave(
        self, sent, fake_redis, concierge
    ):
        """Ответ анкеты — это то, что человек сказал, и он остаётся в истории.

        Выбранный механизм — переписывание в фразу, а не пропуск: ответы
        анкеты (пол, цель) осмысленны для консьержа, и текстовые шаги того же
        опроса (возраст, рост, вес) в историю попадают всегда. Пропуск
        оставил бы историю, где «30» есть, а пола нет.
        """
        from apps.skills.nutrition_anketa.fsm import GENDER_CHOICES

        _, conversation = _welcomed_user(99002)

        max_handler.handle_global_max_event(
            _tap(payload="cb:anketa:start", user_id=99002, callback_id="an-3")
        )
        max_handler.handle_global_max_event(
            _tap(payload="cb:anketa:choice:gender:female", user_id=99002, callback_id="an-4")
        )

        assert _user_messages(conversation)[-1] == GENDER_CHOICES["female"]

    def test_goal_choice_tap_lands_as_the_label_too(self, sent, fake_redis, concierge):
        """Второй шаг с клавиатурой — той же машинкой, без второй таблицы."""
        from apps.skills.nutrition_anketa.fsm import GOAL_CHOICES

        _, conversation = _welcomed_user(99006)

        max_handler.handle_global_max_event(
            _tap(payload="cb:anketa:choice:goal:lose", user_id=99006, callback_id="an-9")
        )

        history = _user_messages(conversation)
        assert history, "история пуста — проверка ниже ничего не доказывает"
        assert history[-1] == GOAL_CHOICES["lose"]

    def test_edit_tap_is_navigation_and_leaves_no_user_turn(self, sent, fake_redis, concierge):
        """«Изменить вес» — навигация, а не реплика: в историю не попадает.

        Стража: ход при этом РАБОТАЕТ — бот переспрашивает вес, — поэтому
        отсутствие реплики здесь означает «нажатие не выдано за слова», а не
        «ничего не произошло».
        """
        _, conversation = _welcomed_user(99003)

        max_handler.handle_global_max_event(
            _tap(payload="cb:anketa:start", user_id=99003, callback_id="an-5")
        )
        max_handler.handle_global_max_event(
            _tap(payload="cb:anketa:edit:weight", user_id=99003, callback_id="an-6")
        )

        # Положительная стража: тап действительно отработал — бот переспросил вес.
        assert "вес" in sent[-1]["text"].lower(), sent[-1]["text"]
        history = _user_messages(conversation)
        assert not [line for line in history if line.startswith("cb:")], history

    def test_short_term_memory_gets_the_phrase_not_the_payload(
        self, sent, fake_redis, concierge
    ):
        """Короткая память — второй читатель того же хода, и он тоже чинится."""
        _, conversation = _welcomed_user(99004)

        max_handler.handle_global_max_event(
            _tap(payload="cb:anketa:start", user_id=99004, callback_id="an-7")
        )
        max_handler.handle_global_max_event(
            _tap(payload="cb:anketa:choice:gender:male", user_id=99004, callback_id="an-8")
        )

        recent = short_term.recall(conversation.id)
        user_turns = [m for m in recent if m.get("role") == "user"]
        assert user_turns, "короткая память пуста — проверка ниже ничего не доказывает"
        assert not [m for m in user_turns if str(m.get("content", "")).startswith("cb:")], recent

    def test_typed_lookalike_is_still_the_person_s_own_words(
        self, sent, fake_redis, concierge
    ):
        """Человек может НАБРАТЬ «cb:anketa:…» руками — подменять нельзя.

        Разбирается форма, а не префикс: строка, не совпавшая с формой
        колбэка, идёт в историю как есть.
        """
        typed = "cb:anketa: это я просто так написала"
        _, conversation = _welcomed_user(99005)

        max_handler.handle_global_max_event(_msg(text=typed, user_id=99005, mid="typed-990"))

        assert _user_messages(conversation) == [typed]
