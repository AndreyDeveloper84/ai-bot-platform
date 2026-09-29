"""DRF-2176 (К-1, C02.2 DRF-1176) — мультивыбор ЖИВЬЁМ: от tool_call модели до провода.

Замер 20.09 (`docs/modules/03_client_journey_mockups_vs_reality.md`, C02):
экран ☑/☐ построен, но «единственный вызывающий `render_multiselect_
clarification` вне тестов — перерисовка после тапа»; живой путь модели
рисовал одиночные кнопки, а первый экран сеяли спаем
(`test_handler_clarification_multiselect.py::_open_multiselect`).

Здесь спая нет: провайдер LLM возвращает `ask_clarification` с
`mode=choose_many`, дальше — настоящий консьерж, настоящий рендер,
настоящий handler и настоящая запись строки ассистента. Три шага макета:

1. первый экран — ☐-кнопки, «Другое (расскажу сама)», без «Продолжить»;
2. тап — ТО ЖЕ сообщение перерисовано: ☑, «Выбрано: 1 из 3», «Продолжить»
   появилась;
3. «Продолжить» — накопленный выбор уходит в ход текстом, как если бы
   человек его набрал.

Отрицательная пара (по слову главного окна): тот же путь с `confirm_one`
рисует одиночные кнопки «тап = ответ» — байт в байт как до DRF-2176.
"""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock, Mock

import pytest
from django.utils import timezone

from apps.channels.max import handler as max_handler
from apps.conversations.models import Message
from apps.conversations.services import resolve_active_global_conversation
from apps.identity.services.resolver import resolve_or_create_global_bot_user
from apps.llm.protocol import CompletionResult, ToolCall
from apps.orchestrator import concierge, discovery
from apps.orchestrator.memory import short_term

pytestmark = pytest.mark.django_db(transaction=True)

_QUESTION = "Что из этого про тебя? Можно выбрать несколько."
_OPTIONS = ["Меньше отёчности", "Свежий вид", "Снять напряжение"]
_CHAT = 8877


@pytest.fixture(autouse=True)
def _harness(settings, monkeypatch):
    settings.GLOBAL_BOT_ONBOARDING = True
    settings.STRICT_TENANT_SCOPE = "strict"
    settings.STRICT_TENANT_REFUSE = True
    monkeypatch.setattr(
        "apps.channels.max.outbound.send_chat_action", lambda **kwargs: {"ok": True}
    )
    monkeypatch.setattr(max_handler, "resolve_and_log_turn_intent", MagicMock())
    monkeypatch.setattr(max_handler, "maybe_weave_question", lambda _c, _b, reply: reply)
    from apps.orchestrator.memory.tests.test_short_term import _FakeRedis

    monkeypatch.setattr(short_term, "_redis_client", lambda: _FakeRedis())


@pytest.fixture
def wire(monkeypatch):
    """Каждый исход на провод — с различением SEND и EDIT."""
    calls: list[dict] = []

    def fake_send(*, chat_id, text, attachments=None, timeout=10.0, bot=None):
        calls.append({"kind": "send", "text": text, "att": attachments})
        return {"ok": True}

    def fake_edit_or_send(*, chat_id, message_id, text, attachments=None, timeout=10.0, bot=None):
        calls.append({"kind": "edit", "mid": message_id, "text": text, "att": attachments})
        return True

    monkeypatch.setattr(max_handler, "send_message", fake_send)
    monkeypatch.setattr(max_handler, "edit_message_or_send", fake_edit_or_send)
    return calls


def _model(monkeypatch, mode: str) -> AsyncMock:
    """Провайдер: модель зовёт `ask_clarification` с данным режимом на каждый вызов."""
    result = CompletionResult(
        text="",
        tool_calls=[
            ToolCall(
                id="c1",
                name="ask_clarification",
                arguments={"question": _QUESTION, "options": _OPTIONS, "mode": mode},
            )
        ],
        prompt_tokens=30,
        completion_tokens=12,
        model="gpt-4o-mini",
        provider="openai",
        finish_reason="tool_calls",
    )
    provider = AsyncMock()
    provider.complete.return_value = result
    router = Mock()
    router.get_provider.return_value = provider
    monkeypatch.setattr(concierge, "get_router", lambda: router)
    return provider


_UID = iter(range(79301, 79399))


def _welcomed_user():
    from apps.consent.services import record_global_consent

    user_id = next(_UID)
    bot_user = resolve_or_create_global_bot_user(
        channel="max", channel_user_id=str(user_id), chat_id=str(_CHAT)
    )
    bot_user.welcomed_at = timezone.now()
    bot_user.save(update_fields=["welcomed_at"])
    record_global_consent(
        bot_user,
        consent_type="personal_data",
        source="test:drf2176",
        document_version="welcome-s2-v1",
    )
    bot_user.refresh_from_db()
    return user_id, resolve_active_global_conversation(bot_user)


def _msg(user_id: int, text: str, *, mid: str) -> dict:
    return {
        "update_type": "message_created",
        "timestamp": 1731320000000,
        "message": {
            "sender": {"user_id": user_id, "name": "Анна"},
            "recipient": {"chat_id": _CHAT, "chat_type": "dialog"},
            "body": {"mid": mid, "seq": 1, "text": text, "attachments": []},
        },
    }


def _tap(user_id: int, payload: str, *, mid: str) -> dict:
    return {
        "update_type": "message_callback",
        "timestamp": 1731320000000,
        "callback": {
            "timestamp": 1731320000500,
            "callback_id": f"cb-{uuid.uuid4()}",
            "payload": payload,
            "user": {"user_id": user_id, "name": "Анна", "lang": "ru"},
        },
        "message": {
            "recipient": {"chat_id": _CHAT, "chat_type": "dialog"},
            "body": {"mid": mid, "seq": 1, "text": _QUESTION, "attachments": []},
        },
    }


def _run(payload: dict) -> None:
    max_handler.handle_global_max_event(payload, trace_id=str(uuid.uuid4()))


def _labels(call: dict) -> list[str]:
    return [b["text"] for row in call["att"][0]["payload"]["buttons"] for b in row]


class TestChooseManyLive:
    def test_first_screen_comes_from_the_model_not_a_spy(self, wire, monkeypatch):
        _model(monkeypatch, "choose_many")
        user_id, conversation = _welcomed_user()

        _run(_msg(user_id, "хочу привести себя в порядок", mid="m-open"))

        first = wire[-1]
        assert first["kind"] == "send"
        assert first["text"] == _QUESTION
        labels = _labels(first)
        # Опции ☐ → «Другое (расскажу сама)»; «Продолжить» ещё нечего — её нет.
        assert labels[:3] == [f"{discovery.CLARIFY_MARK_OFF}{o}" for o in _OPTIONS]
        assert labels[3] == "Другое (расскажу сама)"
        assert discovery.CLARIFY_SUBMIT_LABEL not in labels
        # Строка ассистента несёт предложение — следующий тап читает опции из неё.
        row = Message.all_tenants.filter(conversation=conversation, role="assistant").latest(
            "created_at"
        )
        assert row.action_data["clarification"]["mode"] == "choose_many"
        assert row.action_data["clarification"]["options"] == _OPTIONS

    def test_tap_rewrites_the_same_message_and_reveals_continue(self, wire, monkeypatch):
        _model(monkeypatch, "choose_many")
        user_id, _conversation = _welcomed_user()
        _run(_msg(user_id, "хочу привести себя в порядок", mid="m-open"))

        _run(_tap(user_id, "cb:clarify:tg:0:0", mid="m-open"))

        redraw = wire[-1]
        assert redraw["kind"] == "edit"
        assert redraw["mid"] == "m-open"
        assert redraw["text"] == f"{_QUESTION}\n\nВыбрано: 1 из 3"
        labels = _labels(redraw)
        assert labels[0].startswith(discovery.CLARIFY_MARK_ON)
        assert labels[1].startswith(discovery.CLARIFY_MARK_OFF)
        assert labels[-1] == discovery.CLARIFY_SUBMIT_LABEL
        assert [c["kind"] for c in wire] == ["send", "edit"]

    def test_continue_submits_the_selection_as_the_persons_words(self, wire, monkeypatch):
        _model(monkeypatch, "choose_many")
        user_id, conversation = _welcomed_user()
        _run(_msg(user_id, "хочу привести себя в порядок", mid="m-open"))

        _run(_tap(user_id, "cb:clarify:ok:5", mid="m-open"))  # биты 0 и 2

        contents = [
            m.content
            for m in Message.all_tenants.filter(conversation=conversation, role="user").order_by(
                "created_at"
            )
        ]
        assert contents[-1] == "Меньше отёчности, Снять напряжение"
        assert not any(c.startswith("cb:clarify") for c in contents)


class TestConfirmOneIsUntouched:
    def test_same_path_with_confirm_one_draws_one_tap_one_answer(self, wire, monkeypatch):
        """Отрицательная пара: не-мультивыбор — одиночные кнопки, callback = текст опции."""
        _model(monkeypatch, "confirm_one")
        user_id, _conversation = _welcomed_user()

        _run(_msg(user_id, "хочу привести себя в порядок", mid="m-open"))

        first = wire[-1]
        assert first["kind"] == "send"
        assert first["text"] == _QUESTION
        buttons = [b for row in first["att"][0]["payload"]["buttons"] for b in row]
        assert [b["text"] for b in buttons] == _OPTIONS
        assert [b["payload"] for b in buttons] == _OPTIONS
        assert not any(
            b["text"].startswith((discovery.CLARIFY_MARK_ON, discovery.CLARIFY_MARK_OFF))
            for b in buttons
        )
        assert discovery.CLARIFY_SUBMIT_LABEL not in [b["text"] for b in buttons]
        assert discovery.CLARIFY_NONE_LABEL not in [b["text"] for b in buttons]
