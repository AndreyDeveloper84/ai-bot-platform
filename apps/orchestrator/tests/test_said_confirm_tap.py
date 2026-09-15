"""Подтверждение сказанного одним тапом (DRF-1878, бриф окна «Мозг», п.4).

Условия готовности, каждое своим тестом:
* тап «Да» не пишет сырой callback в историю: там метка кнопки, как у тапов
  еды (DRF-990); факт жив и переписан свежей строкой; модель получает метку —
  ``TestYesTap``;
* тап «Другое» — вопрос бота без модели, открыт ``said.city``; новый сказанный
  город вытесняет Пензу — ``TestOtherTap``;
* кнопка, за которой факта уже нет, — ответ без модели, в историю ничего —
  ``TestStaleTap``;
* инструмент предлагается модели только при сказанных фактах, а вызов рисует
  вопрос и кнопки ботом — ``TestConciergeOffer``.
"""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock, Mock

import pytest
from django.utils import timezone

from apps.channels.max import handler as max_handler
from apps.conversations.models import Message
from apps.conversations.services import resolve_active_global_conversation
from apps.identity.models import MemoryEntry
from apps.llm.protocol import CompletionResult, ToolCall
from apps.orchestrator import concierge, said_memory
from apps.orchestrator.discovery import DiscoveryReply
from apps.orchestrator.memory import short_term
from apps.orchestrator.open_question import open_question, pending_question
from apps.orchestrator.tests.test_memory_erasure_matrix import _bot_user, _consents
from apps.orchestrator.tests.test_said_memory import SHOW_MASTERS_PENZA, _said

pytestmark = pytest.mark.django_db(transaction=True)

CHAT_ID = 8899
SHOW_MASTERS_SAMARA = ({"tool": "show_masters", "arguments": {"city": "Самара"}},)

_UID = iter(range(79601, 79699))


@pytest.fixture(autouse=True)
def _known_cities(monkeypatch):
    monkeypatch.setattr("apps.marketplace.discovery._known_cities", lambda: ["Пенза", "Самара"])


@pytest.fixture
def sent(settings, monkeypatch):
    settings.GLOBAL_BOT_ONBOARDING = True
    monkeypatch.setattr(
        "apps.channels.max.outbound.send_chat_action", lambda **kwargs: {"ok": True}
    )
    monkeypatch.setattr(max_handler, "resolve_and_log_turn_intent", MagicMock())
    monkeypatch.setattr(max_handler, "maybe_weave_question", lambda _c, _b, reply: reply)
    calls: list[dict] = []

    def fake_send(**kwargs):
        calls.append(kwargs)
        return {"ok": True}

    monkeypatch.setattr(max_handler, "send_message", fake_send)
    from apps.orchestrator.memory.tests.test_short_term import _FakeRedis

    monkeypatch.setattr(short_term, "_redis_client", lambda: _FakeRedis())
    return calls


def _person(settings):
    uid = str(next(_UID))
    bot_user = _bot_user(uid)
    bot_user.ayla_user_id_is_proxy = False
    bot_user.welcomed_at = timezone.now()
    bot_user.save(update_fields=["ayla_user_id_is_proxy", "welcomed_at"])
    _consents(bot_user, settings)
    return uid, bot_user, resolve_active_global_conversation(bot_user)


def _person_with_penza(settings):
    uid, bot_user, conversation = _person(settings)
    said_memory.record_said_facts(
        bot_user, conversation, "хочу массаж в Пензе", tool_trace=SHOW_MASTERS_PENZA
    )
    assert _said(bot_user) == {"city": "Пенза"}
    offer = said_memory.confirm_offer(bot_user, said_memory.KEY_CITY)
    assert offer is not None
    # Так открыл бы вопрос консьерж, нарисовав кнопки (см. TestConciergeOffer).
    open_question(conversation, said_memory.said_question_id("city"), asked_text=offer.question)
    return uid, bot_user, conversation


def _tap(uid: str, payload: str) -> dict:
    return {
        "update_type": "message_callback",
        "timestamp": 1731320000000,
        "callback": {
            "timestamp": 1731320000500,
            "callback_id": f"cb-{uuid.uuid4()}",
            "payload": payload,
            "user": {"user_id": int(uid), "name": "Андрей", "lang": "ru"},
        },
        "message": {
            "recipient": {"chat_id": CHAT_ID, "chat_type": "dialog"},
            "body": {"mid": "m-offer", "seq": 1, "text": "Ищем?", "attachments": []},
        },
    }


def _text(uid: str, text: str, *, mid: str) -> dict:
    return {
        "update_type": "message_created",
        "timestamp": 1731320000000,
        "message": {
            "sender": {"user_id": int(uid), "name": "Андрей"},
            "recipient": {"chat_id": CHAT_ID, "chat_type": "dialog"},
            "body": {"mid": mid, "seq": 1, "text": text, "attachments": []},
        },
    }


def _user_rows(conversation) -> list[str]:
    return list(
        Message.all_tenants.filter(conversation=conversation, role="user")
        .order_by("created_at")
        .values_list("content", flat=True)
    )


def _city_statuses(bot_user) -> list[str]:
    # ``content`` зашифрован (EncryptedJSONField): ключ по нему не ищется в
    # базе, только после чтения.
    return sorted(
        row.status
        for row in MemoryEntry.objects.filter(user_id=bot_user.ayla_user_id)
        if isinstance(row.content, dict) and row.content.get("key") == "city"
    )


def _spy_concierge(monkeypatch, text: str) -> MagicMock:
    spy = MagicMock(return_value=DiscoveryReply(text=text))
    monkeypatch.setattr("apps.orchestrator.concierge.generate_concierge_reply", spy)
    return spy


class TestYesTap:
    def test_label_not_payload_in_history_fact_refreshed_model_gets_the_label(
        self, settings, monkeypatch, sent
    ):
        uid, bot_user, conversation = _person_with_penza(settings)
        assert _city_statuses(bot_user) == ["active"]
        spy = _spy_concierge(monkeypatch, "Смотрю мастеров в Пензе")

        max_handler.handle_global_max_event(_tap(uid, "cb:said:city:yes"))

        rows = _user_rows(conversation)
        assert rows == ["Да, Пенза"]
        assert not any(row.startswith("cb:") for row in rows)
        # Факт жив и переписан: свежая строка, прежняя — superseded.
        assert _said(bot_user) == {"city": "Пенза"}
        assert _city_statuses(bot_user) == ["active", "superseded"]
        spy.assert_called_once()
        assert "Да, Пенза" in str(spy.call_args)
        assert "cb:said" not in str(spy.call_args)
        assert sent[-1]["text"] == "Смотрю мастеров в Пензе"


class TestOtherTap:
    def test_bot_asks_without_the_model_and_the_new_city_displaces_penza(
        self, settings, monkeypatch, sent
    ):
        uid, bot_user, conversation = _person_with_penza(settings)
        spy = _spy_concierge(monkeypatch, "не должно вызваться")

        max_handler.handle_global_max_event(_tap(uid, "cb:said:city:other"))

        spy.assert_not_called()
        assert sent[-1]["text"] == said_memory.OTHER_QUESTIONS["city"]
        conversation.refresh_from_db()
        question = pending_question(conversation)
        assert question is not None and question.question_id == "said.city"
        assert _user_rows(conversation) == ["Другой город"]
        assert _said(bot_user) == {"city": "Пенза"}

        # Следующий ход: человек назвал город, модель искала в нём — тот же
        # писатель, что на любом ходу (ход модели здесь заменён его трассой).
        said_memory.record_said_facts(
            bot_user, conversation, "в Самаре", tool_trace=SHOW_MASTERS_SAMARA
        )
        assert _said(bot_user) == {"city": "Самара"}


class TestStaleTap:
    def test_tap_without_the_fact_answers_without_the_model_and_writes_nothing(
        self, settings, monkeypatch, sent
    ):
        uid, bot_user, conversation = _person_with_penza(settings)
        settings.CONCIERGE_MEMORY_ENABLED = False  # факта для чтения больше нет
        spy = _spy_concierge(monkeypatch, "не должно вызваться")

        max_handler.handle_global_max_event(_tap(uid, "cb:said:city:yes"))

        spy.assert_not_called()
        assert sent[-1]["text"] == said_memory.STALE_TEXT
        # empty-assert-ok: устаревший тап не пишет реплику по построению; соседний TestYesTap доказывает, что живой тап пишет метку
        assert _user_rows(conversation) == []


class TestTapForm:
    def test_typed_look_alikes_are_not_taps(self, settings):
        _uid, bot_user, _conversation = _person_with_penza(settings)

        assert said_memory.resolve_said_tap("cb:said: Пенза", bot_user) is None
        assert said_memory.resolve_said_tap("cb:said:city:maybe", bot_user) is None
        assert said_memory.resolve_said_tap("да, Пенза", bot_user) is None
        tap = said_memory.resolve_said_tap("cb:said:city:yes", bot_user)
        assert tap == said_memory.SaidTap(key="city", verdict="yes", history_text="Да, Пенза")

    def test_buttons_and_history_labels_come_from_one_builder(self, settings):
        _uid, bot_user, conversation = _person(settings)
        said_memory.record_said_facts(
            bot_user,
            conversation,
            "хочу массаж в Пензе после работы",
            tool_trace=SHOW_MASTERS_PENZA,
        )

        on_screen = {
            button["callback"]: button["label"]
            for key in ("city", "visit_context")
            for button in said_memory.confirm_keyboard(said_memory.confirm_offer(bot_user, key))[
                "attachments"
            ][0]["payload"]["buttons"]
        }

        assert on_screen == said_memory.said_tap_labels(bot_user)
        assert on_screen == {
            "cb:said:city:yes": "Да, Пенза",
            "cb:said:city:other": "Другой город",
            "cb:said:visit_context:yes": "Да, после работы",
            "cb:said:visit_context:other": "Другое время",
        }


class TestConciergeOffer:
    def test_tool_is_offered_only_when_there_is_something_to_confirm(self, settings):
        _uid, bot_user, conversation = _person(settings)
        names = {spec["name"] for spec in concierge._tools_offered("привет", conversation)}
        assert said_memory.CONFIRM_SAID_FACT_TOOL not in names

        said_memory.record_said_facts(
            bot_user, conversation, "хочу массаж в Пензе", tool_trace=SHOW_MASTERS_PENZA
        )
        names = {spec["name"] for spec in concierge._tools_offered("привет", conversation)}
        assert said_memory.CONFIRM_SAID_FACT_TOOL in names

    def test_tool_call_renders_the_bot_question_with_buttons_and_opens_it(
        self, settings, monkeypatch, sent
    ):
        uid, bot_user, conversation = _person(settings)
        said_memory.record_said_facts(
            bot_user, conversation, "хочу массаж в Пензе", tool_trace=SHOW_MASTERS_PENZA
        )
        provider = AsyncMock()
        provider.complete.return_value = CompletionResult(
            text="",
            tool_calls=[ToolCall(id="c1", name="confirm_said_fact", arguments={"key": "city"})],
            prompt_tokens=20,
            completion_tokens=10,
            model="gpt-4o-mini",
            provider="openai",
            finish_reason="tool_calls",
        )
        router = Mock()
        router.get_provider.return_value = provider
        monkeypatch.setattr(concierge, "get_router", lambda: router)

        max_handler.handle_global_max_event(_text(uid, "что посоветуешь?", mid="m-ask"))

        assert sent[-1]["text"] == "Ищем в городе Пенза, как обычно?"
        assert "Да, Пенза" in str(sent[-1].get("attachments"))
        assert "cb:said:city:other" in str(sent[-1].get("attachments"))
        conversation.refresh_from_db()
        question = pending_question(conversation)
        assert question is not None and question.question_id == "said.city"
