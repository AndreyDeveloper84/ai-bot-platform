"""Обещание без действия не выпускается (DRF-1827).

Приёмочный материал — диалог владельца 12.09 (DRF-1754): «ну я назвал тебе
конкретное место — спина» → «Ты прав, прости! Сейчас проверю.»; «и?» →
«Прости за задержку — запускаю проверку.» Ничего не проверялось: в обоих
случаях проза стояла рядом с вызовом инструмента, инструмент отказал, слова
ушли в чат. Сторож консьержа (DRF-1286) зовётся только когда вызова НЕТ.

Здесь тот же ход идёт через настоящий канал (обвязка — как в
``test_degraded_lines_1489``); подменена только модель.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, Mock

import pytest
from django.utils import timezone

from apps.channels.max import handler as max_handler
from apps.conversations.models import Message
from apps.conversations.services import resolve_active_global_conversation
from apps.identity.services.resolver import resolve_or_create_global_bot_user
from apps.llm.protocol import CompletionResult, ToolCall
from apps.orchestrator import concierge
from apps.orchestrator.memory import short_term
from apps.orchestrator.safety import outbound
from apps.orchestrator.safety.gate import guard_outbound

pytestmark = pytest.mark.django_db(transaction=True)

PROMISE_AND = "Прости за задержку — запускаю проверку."
PROMISE_CHECK = "Ты прав, прости! Сейчас проверю."
PROMISE_WAIT = "Секундочку, записываю!"
HONEST = "В Пензе есть массажисты — спина после сидячей работы обычно просит шейно-воротниковую зону. Какой район удобнее?"


# --------------------------------------------------------------------------- #
# Единица: класс action_promise                                               #
# --------------------------------------------------------------------------- #
class TestEvaluateActionPromise:
    @pytest.mark.parametrize("text", [PROMISE_AND, PROMISE_CHECK, PROMISE_WAIT, "Одну минуту!"])
    def test_owner_turn_promises_are_caught(self, text):
        verdict = outbound.evaluate_action_promise(text)
        assert verdict.blocked
        assert verdict.categories == (outbound.ACTION_PROMISE_CATEGORY,)
        assert verdict.text == outbound.ACTION_PROMISE_TEXT

    @pytest.mark.parametrize(
        "text",
        [
            "Подберите удобное время и напишите мне.",  # императив клиенту
            "Добавлю, что цены могут отличаться.",  # вводное слово
            "В Пензе есть массажисты. Какой район удобнее?",
            "",
        ],
    )
    def test_plain_answers_pass(self, text):
        verdict = outbound.evaluate_action_promise(text)
        assert verdict.allowed
        assert verdict.text == text

    def test_the_replacement_names_what_it_cannot_and_what_it_can(self):
        assert "не умею" in outbound.ACTION_PROMISE_TEXT
        assert "могу" in outbound.ACTION_PROMISE_TEXT
        # Сама замена не должна быть обещанием.
        assert outbound.evaluate_action_promise(outbound.ACTION_PROMISE_TEXT).allowed

    def test_concierge_and_guard_share_one_lexicon(self):
        assert concierge._PROMISE_STEMS is outbound.ACTION_PROMISE_STEMS


class TestGuardOutboundActed:
    def test_unknown_acted_keeps_the_old_behaviour(self):
        assert guard_outbound(PROMISE_AND, surface="test").allowed

    def test_acted_true_lets_a_promise_after_a_tool_through(self):
        assert guard_outbound(
            "Вот варианты — покажу ещё, если нужно.", surface="test", acted=True
        ).allowed

    def test_acted_false_blocks_the_promise(self):
        outcome = guard_outbound(PROMISE_AND, surface="test", acted=False)
        assert outcome.blocked
        assert outcome.categories == (outbound.ACTION_PROMISE_CATEGORY,)
        assert outcome.text == outbound.ACTION_PROMISE_TEXT

    def test_acted_false_passes_an_honest_line(self):
        assert guard_outbound(HONEST, surface="test", acted=False).allowed


class TestToolActed:
    def test_empty_trace_is_no_action(self):
        assert concierge._tool_acted(None) is False
        assert concierge._tool_acted(()) is False

    def test_declined_tool_is_no_action(self):
        trace = ({"tool": "health_screening", "arguments": {}, "result": "declined_prose"},)
        assert concierge._tool_acted(trace) is False

    def test_executed_tool_is_an_action(self):
        trace = ({"tool": "show_masters", "arguments": {"city": "Пенза"}},)
        assert concierge._tool_acted(trace) is True


# --------------------------------------------------------------------------- #
# Канал: реплики владельца                                                    #
# --------------------------------------------------------------------------- #
@pytest.fixture(autouse=True)
def _channel_harness(settings, monkeypatch):
    settings.GLOBAL_BOT_ONBOARDING = True
    monkeypatch.setattr(
        "apps.channels.max.outbound.send_chat_action", lambda **kwargs: {"ok": True}
    )
    monkeypatch.setattr(max_handler, "resolve_and_log_turn_intent", MagicMock())
    monkeypatch.setattr(max_handler, "maybe_weave_question", lambda _c, _b, reply: reply)
    from apps.orchestrator.memory.tests.test_short_term import _FakeRedis

    monkeypatch.setattr(short_term, "_redis_client", lambda: _FakeRedis())


@pytest.fixture
def sent(monkeypatch):
    calls: list[dict] = []

    def fake_send(*, chat_id, text, attachments=None, timeout=10.0):
        calls.append({"chat_id": chat_id, "text": text, "attachments": attachments})
        return {"ok": True}

    monkeypatch.setattr(max_handler, "send_message", fake_send)
    return calls


def _welcomed_user(user_id: int):
    from apps.consent.services import record_global_consent

    bot_user = resolve_or_create_global_bot_user(
        channel="max", channel_user_id=str(user_id), chat_id="8899"
    )
    bot_user.welcomed_at = timezone.now()
    bot_user.save(update_fields=["welcomed_at"])
    record_global_consent(
        bot_user,
        consent_type="personal_data",
        source="test:drf1827",
        document_version="welcome-s2-v1",
    )
    bot_user.refresh_from_db()
    return bot_user, resolve_active_global_conversation(bot_user)


def _msg(*, text: str, user_id: int, mid: str) -> dict:
    return {
        "update_type": "message_created",
        "timestamp": 1731320000000,
        "message": {
            "sender": {"user_id": user_id, "name": "Андрей"},
            "recipient": {"chat_id": 8899, "chat_type": "dialog"},
            "body": {"mid": mid, "seq": 1, "text": text, "attachments": []},
        },
    }


def _prose(text: str) -> CompletionResult:
    return CompletionResult(
        text=text,
        tool_calls=[],
        prompt_tokens=30,
        completion_tokens=12,
        model="gpt-4o-mini",
        provider="openai",
        finish_reason="stop",
    )


def _tool_with_prose(name: str, arguments: dict, text: str) -> CompletionResult:
    """Модель и сказала, и вызвала — форма живого хода 12.09."""
    return CompletionResult(
        text=text,
        tool_calls=[ToolCall(id="c1", name=name, arguments=arguments)],
        prompt_tokens=30,
        completion_tokens=12,
        model="gpt-4o-mini",
        provider="openai",
        finish_reason="tool_calls",
    )


def _model(monkeypatch, result: CompletionResult) -> AsyncMock:
    """Провайдер отвечает одним и тем же на каждый вызов — в том числе на
    forced retry с tool_choice=required (DRF-1286), который он игнорирует."""
    provider = AsyncMock()
    provider.complete.return_value = result
    router = Mock()
    router.get_provider.return_value = provider
    monkeypatch.setattr(concierge, "get_router", lambda: router)
    return provider


_UID = iter(range(78201, 78299))


def _screen(sent, *, text: str):
    user_id = next(_UID)
    _bot_user, conversation = _welcomed_user(user_id)
    max_handler.handle_global_max_event(_msg(text=text, user_id=user_id, mid=f"m{user_id}"))
    return sent[-1]["text"], conversation


class TestOwnerTurns:
    def test_and_then_what_gets_no_running_check(self, monkeypatch, sent):
        """«и?» → модель обещает «запускаю проверку», инструмента нет; forced
        retry возвращает то же. На экран уходит честная строка."""
        provider = _model(monkeypatch, _prose(PROMISE_AND))
        screen, conversation = _screen(sent, text="и?")

        assert provider.complete.await_count >= 2  # forced retry состоялся и не помог
        assert screen == outbound.ACTION_PROMISE_TEXT
        assert screen != PROMISE_AND
        # Транскрипт следующего хода несёт честную строку, не обещание.
        row = Message.all_tenants.filter(conversation=conversation, role="assistant").latest(
            "created_at"
        )
        assert row.content == outbound.ACTION_PROMISE_TEXT

    def test_promise_beside_a_declined_tool_is_caught(self, monkeypatch, sent):
        """Форма 12.09: проза рядом с вызовом, инструмент отказал. Раньше
        проза уходила в чат как есть (ветка declined_prose)."""
        _model(monkeypatch, _tool_with_prose("log_water", {"drink_text": "чай"}, PROMISE_WAIT))
        monkeypatch.setattr(concierge, "execute_nutrition_tool", lambda *a, **kw: None)

        screen, _ = _screen(sent, text="выпил чай")

        assert screen == outbound.ACTION_PROMISE_TEXT
        assert screen != PROMISE_WAIT

    def test_an_honest_answer_is_untouched(self, monkeypatch, sent):
        _model(monkeypatch, _prose(HONEST))
        screen, _ = _screen(sent, text="и?")
        assert screen == HONEST

    def test_honest_prose_beside_a_declined_tool_still_reaches_the_person(self, monkeypatch, sent):
        """DRF-1542 сохраняется: после вето ход идёт обратно к модели, и её
        честный текст (без обещания) доезжает. Сторож — это стем И
        отсутствие действия, а не любой ход с acted=False."""
        _model(monkeypatch, _tool_with_prose("log_water", {"drink_text": "чай"}, HONEST))
        monkeypatch.setattr(concierge, "execute_nutrition_tool", lambda *a, **kw: None)

        screen, _ = _screen(sent, text="выпил чай")

        assert screen == HONEST
