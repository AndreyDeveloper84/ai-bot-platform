"""W5 concierge wiring tests (DRF-241): OpenAI-shape mapping, store adapter,
tool dispatcher, and the end-to-end concierge turn via ayla-ai-core AIConcierge.
"""

from __future__ import annotations

import json
from datetime import date
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from apps.llm.protocol import CompletionResult, ToolCall
from apps.orchestrator import concierge, discovery
from apps.orchestrator.concierge import (
    GlobalConversationStore,
    _dispatch_tool,
    _to_openai_shape,
    generate_concierge_reply,
)


def _card(name: str):
    """A minimal public card — enough for the renderer, no catalog needed."""
    from uuid import uuid4

    from apps.marketplace.dto import MasterCard

    return MasterCard(
        tenant_id=uuid4(),
        master_id=uuid4(),
        name=name,
        specialization="",
        rating=None,
        photo_url="",
        city="Пенза",
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


class TestBuildConciergeSystemPrompt:
    """DRF-988 — the concierge system prompt must carry the current date."""

    def test_today_grounding_block(self) -> None:
        prompt = concierge.build_concierge_system_prompt(today=date(2026, 8, 10))

        assert "Сегодня: 2026-08-10 (понедельник)" in prompt
        assert "часовой пояс" in prompt

    def test_today_defaults_to_clock(self, monkeypatch) -> None:
        monkeypatch.setattr(concierge.timezone, "localdate", lambda: date(2026, 8, 10))

        prompt = concierge.build_concierge_system_prompt()

        assert "Сегодня: 2026-08-10" in prompt


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

    def test_system_prompt_contains_current_date(self, monkeypatch) -> None:
        captured: dict = {}

        async def _complete(messages, model: str = "", tools=None):  # noqa: ANN001
            captured["messages"] = messages
            return CompletionResult(text="ok")

        provider = AsyncMock()
        provider.complete.side_effect = _complete
        monkeypatch.setattr(concierge, "get_router", lambda: _router_returning(provider))
        monkeypatch.setattr(concierge.timezone, "localdate", lambda: date(2026, 8, 10))
        bot_user, conversation = self._bot_user_and_conversation()

        generate_concierge_reply("привет", bot_user=bot_user, conversation=conversation)

        system = captured["messages"][0]["content"]
        # DRF-988: without the current date the model lives at its training
        # cutoff and rejects real near-future booking dates as far future.
        assert "Сегодня: 2026-08-10" in system

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
            service_id=None,
            service_name="",
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

    def test_show_masters_without_criteria_asks_instead_of_listing_catalogue(
        self, monkeypatch
    ) -> None:
        """DRF-1201 — prohibition #22 on the LIVE concierge path.

        A criteria-less ``show_masters`` call must become a clarifying turn,
        not the whole cross-tenant catalogue in alphabetical order. The
        marketplace read must not happen at all.
        """
        tc = ToolCall(id="c1", name="show_masters", arguments={})
        provider = AsyncMock()
        provider.complete.return_value = CompletionResult(text="", tool_calls=[tc])
        monkeypatch.setattr(concierge, "get_router", lambda: _router_returning(provider))

        def _must_not_run(**kwargs):  # noqa: ANN003
            raise AssertionError(f"catalogue read reached with no criteria: {kwargs}")

        monkeypatch.setattr(concierge, "discover_masters", _must_not_run)
        bot_user, conversation = self._bot_user_and_conversation()

        reply = generate_concierge_reply(
            "покажи мастеров", bot_user=bot_user, conversation=conversation
        )

        assert reply.text == discovery.NO_CRITERIA_QUESTION
        assert "Вот мастера" not in reply.text
        assert reply.action_data is None
        # Still persisted by the concierge store, like every other turn it owns.
        assert reply.persisted is True

    def test_direct_show_masters_blank_turn_asks_instead_of_listing_catalogue(
        self, monkeypatch
    ) -> None:
        """Same guard on the deterministic pre-LLM path (DRF-1102 branch)."""

        def _must_not_run(**kwargs):  # noqa: ANN003
            raise AssertionError(f"catalogue read reached with no criteria: {kwargs}")

        monkeypatch.setattr(concierge, "discover_masters", _must_not_run)

        reply = concierge.generate_direct_show_masters_reply("   ")

        assert reply.text == discovery.NO_CRITERIA_QUESTION

    def test_llm_error_falls_back_and_not_persisted(self, monkeypatch) -> None:
        provider = AsyncMock()
        provider.complete.side_effect = RuntimeError("boom")
        monkeypatch.setattr(concierge, "get_router", lambda: _router_returning(provider))
        bot_user, conversation = self._bot_user_and_conversation()

        reply = generate_concierge_reply("привет", bot_user=bot_user, conversation=conversation)

        assert reply.text.strip()
        assert reply.persisted is False


@pytest.mark.django_db(transaction=True)
class TestDirectShowMastersDeclinesOnZero:
    """DRF-1283 — zero results are a handoff signal, not a reply.

    ``generate_direct_show_masters_reply`` returns ``None`` when the search
    matched nobody. The handler reads that as «this layer could not resolve
    the turn» and runs the concierge instead of sending a no-match line
    (routing pinned in ``apps/channels/tests/test_global_zero_result_llm_fallback.py``).
    """

    def _bot_user_and_conversation(self):
        from apps.conversations.services import resolve_active_global_conversation
        from apps.identity.services import resolve_or_create_global_bot_user

        bot_user = resolve_or_create_global_bot_user(
            channel="max",
            channel_user_id="drf1283-uid",
            chat_id="drf1283-chat",
        )
        return bot_user, resolve_active_global_conversation(bot_user)

    def test_zero_cards_returns_none(self, monkeypatch) -> None:
        monkeypatch.setattr(concierge, "discover_masters", lambda **kw: [])

        assert concierge.generate_direct_show_masters_reply("хочу массаж") is None

    def test_cards_are_rendered_as_before(self, monkeypatch) -> None:
        card = _card("Массажист")
        monkeypatch.setattr(concierge, "discover_masters", lambda **kw: [card])

        reply = concierge.generate_direct_show_masters_reply("хочу массаж")

        assert reply is not None
        assert "Массажист" in reply.text
        assert reply.action_data is not None


@pytest.mark.django_db(transaction=True)
class TestDirectBranchIsCounted:
    """DRF-1283 — the deterministic branch writes an ``AIRequestMetric`` row.

    It answers the most common booking turn without a model and used to write
    nothing, so the busiest path in the funnel was absent from the table the
    pilot thresholds are computed from. That is a DENOMINATOR gap, not a cost
    gap: every per-request threshold (Cost per Request, Latency p95, Fallback
    Rate) was computed over a sample that systematically excluded the
    cheapest, fastest turns, and «what share of turns needs the model at all»
    could not be answered.
    """

    def _bot_user_and_conversation(self):
        from apps.conversations.services import resolve_active_global_conversation
        from apps.identity.services import resolve_or_create_global_bot_user

        bot_user = resolve_or_create_global_bot_user(
            channel="max",
            channel_user_id="drf1283-metric-uid",
            chat_id="drf1283-metric-chat",
        )
        return bot_user, resolve_active_global_conversation(bot_user)

    def _rows(self):
        from apps.observability.models import AIRequestMetric

        return list(AIRequestMetric.all_tenants.all())

    def test_answered_turn_writes_one_row_with_no_llm_columns(self, monkeypatch) -> None:
        monkeypatch.setattr(concierge, "discover_masters", lambda **kw: [_card("Массажист")])
        bot_user, conversation = self._bot_user_and_conversation()

        concierge.generate_direct_show_masters_reply(
            "хочу массаж", bot_user=bot_user, conversation=conversation
        )

        rows = self._rows()
        assert len(rows) == 1
        row = rows[0]
        assert row.skill_selected == "concierge_direct"
        assert row.outcome == "success"
        # NULL, not 0 — the schema's own encoding for «no LLM call». Zeros
        # would drag AVG(cost)/AVG(tokens) toward zero and replace an
        # under-count with a distortion.
        assert row.llm_model == ""
        assert row.llm_provider == ""
        assert row.llm_tokens_input is None
        assert row.llm_tokens_output is None
        assert row.llm_cost_usd is None
        # No model call happened, so there is no pass to index — 0 would read
        # as «a zeroth call».
        assert row.llm_pass_index is None

    def test_declined_turn_writes_nothing(self, monkeypatch) -> None:
        """No double-counting: the concierge writes the row for this turn."""
        monkeypatch.setattr(concierge, "discover_masters", lambda **kw: [])
        bot_user, conversation = self._bot_user_and_conversation()

        assert (
            concierge.generate_direct_show_masters_reply(
                "хочу массаж", bot_user=bot_user, conversation=conversation
            )
            is None
        )
        assert self._rows() == []
