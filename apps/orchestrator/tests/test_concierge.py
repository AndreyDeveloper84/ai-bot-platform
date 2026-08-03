"""W5 concierge wiring tests (DRF-241): OpenAI-shape mapping, store adapter,
tool dispatcher, and the end-to-end concierge turn via ayla-ai-core AIConcierge.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from apps.llm.protocol import CompletionResult, ToolCall
from apps.orchestrator import concierge
from apps.orchestrator.concierge import (
    GlobalConversationStore,
    _dispatch_tool,
    _to_openai_shape,
    generate_concierge_reply,
)


def _router_returning(provider: AsyncMock) -> Mock:
    router = Mock()
    router.get_provider.return_value = provider
    return router


class TestToOpenAIShape:
    def test_text_and_usage_mapped(self) -> None:
        shaped = _to_openai_shape(
            CompletionResult(
                text="привет", prompt_tokens=5, completion_tokens=7, finish_reason="stop"
            )
        )
        assert shaped.choices[0].message.content == "привет"
        assert shaped.choices[0].message.tool_calls is None
        assert shaped.usage.prompt_tokens == 5
        assert shaped.usage.completion_tokens == 7

    def test_tool_calls_mapped_to_function_namespace(self) -> None:
        tc = ToolCall(id="c1", name="show_masters", arguments={"city": "Пенза"})
        shaped = _to_openai_shape(CompletionResult(text="", tool_calls=[tc]))
        calls = shaped.choices[0].message.tool_calls
        assert calls[0].id == "c1"
        assert calls[0].function.name == "show_masters"
        assert json.loads(calls[0].function.arguments) == {"city": "Пенза"}


class TestDispatchTool:
    def _tc(self, name: str, arguments: str):
        return SimpleNamespace(function=SimpleNamespace(name=name, arguments=arguments))

    def test_show_masters_valid(self) -> None:
        result = _dispatch_tool(self._tc("show_masters", '{"city": "Пенза"}'), None)
        assert result.action_type == "show_masters"
        assert result.action_data["arguments"] == {"city": "Пенза"}

    def test_unknown_tool_clarifies(self) -> None:
        result = _dispatch_tool(self._tc("book_now", "{}"), None)
        assert result.action_type == "ask_clarification"
        assert result.action_data["reason"] == "unknown_tool:book_now"

    def test_malformed_arguments_clarify(self) -> None:
        result = _dispatch_tool(self._tc("show_masters", "{oops"), None)
        assert result.action_type == "ask_clarification"
        assert result.action_data["reason"] == "malformed_arguments"

    def test_non_dict_arguments_normalised(self) -> None:
        result = _dispatch_tool(self._tc("show_masters", "[1, 2]"), None)
        assert result.action_type == "show_masters"
        assert result.action_data["arguments"] == {}


@pytest.mark.django_db(transaction=True)
class TestGlobalConversationStore:
    def _bot_user(self):
        from apps.identity.services import resolve_or_create_global_bot_user

        return resolve_or_create_global_bot_user(
            channel="max",
            channel_user_id="w5-store-uid",
            chat_id="w5-store-chat",
        )

    def test_user_save_is_marker_assistant_persists(self) -> None:
        from apps.conversations.models import Message

        bot_user = self._bot_user()
        store = GlobalConversationStore(user_message_id=123)
        conversation = store.resolve_active_conversation(bot_user)

        marker = store.save_message(conversation, role="user", content="hi")
        # The channel handler persists user turns upstream — the store only
        # returns the marker so the turn can be excluded from LLM history.
        assert marker.id == 123
        assert Message.all_tenants.filter(conversation=conversation).count() == 0

        store.save_message(
            conversation,
            role="assistant",
            content="ответ",
            action_type="",
            tokens_in=3,
            tokens_out=4,
            # record_global_message carries no such kwargs — adapter drops them.
            action_data={"transient": True},
            tool_call={"transient": True},
        )
        rows = list(Message.all_tenants.filter(conversation=conversation))
        assert len(rows) == 1
        assert rows[0].role == "assistant"
        assert rows[0].content == "ответ"
        assert rows[0].tokens_in == 3
        assert rows[0].tokens_out == 4

    def test_load_recent_history_excludes_and_limits(self) -> None:
        bot_user = self._bot_user()
        store = GlobalConversationStore()
        conversation = store.resolve_active_conversation(bot_user)
        store.save_message(conversation, role="assistant", content="один")
        store.save_message(conversation, role="assistant", content="два")
        last = store.save_message(conversation, role="assistant", content="три")

        history = store.load_recent_history(conversation, exclude_id=last.id, limit=10)
        assert {m.content for m in history} == {"один", "два"}
        assert len(store.load_recent_history(conversation, limit=1)) == 1


@pytest.mark.django_db(transaction=True)
class TestGenerateConciergeReply:
    def _bot_user_and_conversation(self):
        from apps.conversations.services import resolve_active_global_conversation
        from apps.identity.services import resolve_or_create_global_bot_user

        bot_user = resolve_or_create_global_bot_user(
            channel="max",
            channel_user_id="w5-e2e-uid",
            chat_id="w5-e2e-chat",
        )
        conversation = resolve_active_global_conversation(bot_user)
        return bot_user, conversation

    def test_text_reply_persisted_via_store(self, monkeypatch) -> None:
        provider = AsyncMock()
        provider.complete.return_value = CompletionResult(text="Здравствуйте! Чем помочь?")
        monkeypatch.setattr(concierge, "get_router", lambda: _router_returning(provider))
        bot_user, conversation = self._bot_user_and_conversation()

        reply = generate_concierge_reply("привет", bot_user=bot_user, conversation=conversation)

        assert reply.text == "Здравствуйте! Чем помочь?"
        assert reply.persisted is True
        from apps.conversations.models import Message

        roles = [m.role for m in Message.all_tenants.filter(conversation=conversation)]
        assert roles == ["assistant"]

    def test_memory_block_and_boundaries_in_system_prompt(self, monkeypatch) -> None:
        captured: dict = {}

        async def _complete(messages, model: str = "", tools=None):  # noqa: ANN001
            captured["messages"] = messages
            return CompletionResult(text="ok")

        provider = AsyncMock()
        provider.complete.side_effect = _complete
        monkeypatch.setattr(concierge, "get_router", lambda: _router_returning(provider))
        bot_user, conversation = self._bot_user_and_conversation()

        generate_concierge_reply(
            "привет",
            bot_user=bot_user,
            conversation=conversation,
            memory_block="[ПАМЯТЬ О ПОЛЬЗОВАТЕЛЕ]\n- Обычно выбирает время: вечер",
        )

        system = captured["messages"][0]["content"]
        # W5 task 2: the memory block rides in the system prompt.
        assert "[ПАМЯТЬ О ПОЛЬЗОВАТЕЛЕ]" in system
        # W5 task 4: boundary rules present (helpful restraint + S8).
        assert "ничего не предлагать" in system
        assert "не врач" in system

    def test_no_memory_block_when_empty(self, monkeypatch) -> None:
        captured: dict = {}

        async def _complete(messages, model: str = "", tools=None):  # noqa: ANN001
            captured["messages"] = messages
            return CompletionResult(text="ok")

        provider = AsyncMock()
        provider.complete.side_effect = _complete
        monkeypatch.setattr(concierge, "get_router", lambda: _router_returning(provider))
        bot_user, conversation = self._bot_user_and_conversation()

        generate_concierge_reply("привет", bot_user=bot_user, conversation=conversation)

        system = captured["messages"][0]["content"]
        assert "[ПАМЯТЬ О ПОЛЬЗОВАТЕЛЕ]" not in system

    def test_show_masters_renders_cards_in_sync_scope(self, monkeypatch) -> None:
        tc = ToolCall(id="c1", name="show_masters", arguments={"city": "Пенза"})
        provider = AsyncMock()
        provider.complete.return_value = CompletionResult(text="", tool_calls=[tc])
        monkeypatch.setattr(concierge, "get_router", lambda: _router_returning(provider))
        card = SimpleNamespace(
            tenant_id="t1",
            master_id="m1",
            name="Анна",
            specialization="Массаж",
            rating=4.9,
            city="Пенза",
        )
        monkeypatch.setattr(concierge, "discover_masters", lambda **kwargs: [card])
        bot_user, conversation = self._bot_user_and_conversation()

        reply = generate_concierge_reply(
            "покажи мастеров", bot_user=bot_user, conversation=conversation
        )

        assert "Анна" in reply.text
        assert reply.persisted is True
        assert reply.action_data is not None
        buttons = reply.action_data["attachments"][0]["payload"]["buttons"]
        assert buttons[0]["callback"] == "cb:discover:book:t1:m1"

    def test_llm_error_falls_back_and_not_persisted(self, monkeypatch) -> None:
        provider = AsyncMock()
        provider.complete.side_effect = RuntimeError("boom")
        monkeypatch.setattr(concierge, "get_router", lambda: _router_returning(provider))
        bot_user, conversation = self._bot_user_and_conversation()

        reply = generate_concierge_reply("привет", bot_user=bot_user, conversation=conversation)

        assert reply.text.strip()
        assert reply.persisted is False
