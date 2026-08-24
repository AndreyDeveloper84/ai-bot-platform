"""DRF-1266 multi-pass concierge tests: tool result fed back to the model,
pass cap, exhaustion behaviour, per-pass AIRequestMetric emission (DRF-1211),
and the second-pass outbound safety contract.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from apps.llm.protocol import CompletionResult, ToolCall
from apps.orchestrator import concierge
from apps.orchestrator.concierge import (
    _build_tool_result_message,
    _max_llm_passes,
    generate_concierge_reply,
)

TRACE_ID = str(uuid.uuid4())


def _router_returning(provider: AsyncMock) -> Mock:
    router = Mock()
    router.get_provider.return_value = provider
    return router


def _card() -> SimpleNamespace:
    return SimpleNamespace(
        tenant_id="t1",
        master_id="m1",
        name="Анна",
        specialization="Массаж",
        rating=4.9,
        city="Пенза",
        service_id=None,
        service_name="",
    )


def _show_masters_result(args: dict | None = None) -> CompletionResult:
    return CompletionResult(
        text="",
        tool_calls=[ToolCall(id="c1", name="show_masters", arguments=args or {"city": "Пенза"})],
        prompt_tokens=10,
        completion_tokens=5,
        model="gpt-4o-mini",
        provider="openai",
        finish_reason="tool_calls",
    )


def _text_result(text: str) -> CompletionResult:
    return CompletionResult(
        text=text,
        prompt_tokens=20,
        completion_tokens=8,
        model="gpt-4o-mini",
        provider="openai",
        finish_reason="stop",
    )


def _bot_user_and_conversation():
    from apps.conversations.services import resolve_active_global_conversation
    from apps.identity.services import resolve_or_create_global_bot_user

    bot_user = resolve_or_create_global_bot_user(
        channel="max",
        channel_user_id="drf1266-uid",
        chat_id="drf1266-chat",
    )
    conversation = resolve_active_global_conversation(bot_user)
    return bot_user, conversation


def _metrics(trace_id: str = TRACE_ID):
    from apps.observability.models import AIRequestMetric

    return list(
        AIRequestMetric.all_tenants.filter(request_id=uuid.UUID(trace_id)).order_by(
            "llm_pass_index"
        )
    )


class TestMaxLlmPasses:
    def test_default_is_two(self, settings) -> None:
        settings.CONCIERGE_MAX_LLM_PASSES = 2
        assert _max_llm_passes() == 2

    def test_clamped_to_at_least_one(self, settings) -> None:
        settings.CONCIERGE_MAX_LLM_PASSES = 0
        assert _max_llm_passes() == 1

    def test_garbage_falls_back_to_two(self, settings) -> None:
        settings.CONCIERGE_MAX_LLM_PASSES = "lots"
        assert _max_llm_passes() == 2


class TestBuildToolResultMessage:
    def test_carries_question_and_card_data(self) -> None:
        msg = _build_tool_result_message(
            "какие окна свободны в четверг", [_card()], {"city": "Пенза"}
        )
        assert "какие окна свободны в четверг" in msg
        assert "Анна" in msg
        assert "Массаж" in msg
        assert "Пенза" in msg
        # Zero-rating is the absence of a rating (DRF-1224) — never rendered.
        assert "★ 4.9" in msg

    def test_empty_result_says_so(self) -> None:
        msg = _build_tool_result_message("покажи мастеров", [], {"city": "Пенза"})
        assert "никого не нашлось" in msg


@pytest.mark.django_db(transaction=True)
class TestMultiPassTurn:
    def test_tool_result_returns_to_model_and_reply_is_words(self, monkeypatch) -> None:
        """The core DRF-1266 loop: pass 1 calls show_masters, the executed
        result rides back as a plain user message, pass 2 answers in words."""
        provider = AsyncMock()
        provider.complete.side_effect = [
            _show_masters_result(),
            _text_result("В четверг у Анны свободны окна с 10:00."),
        ]
        monkeypatch.setattr(concierge, "get_router", lambda: _router_returning(provider))
        discover = Mock(return_value=[_card()])
        monkeypatch.setattr(concierge, "discover_masters", discover)
        bot_user, conversation = _bot_user_and_conversation()

        reply = generate_concierge_reply(
            "какие окна свободны в четверг",
            bot_user=bot_user,
            conversation=conversation,
            trace_id=TRACE_ID,
        )

        assert reply.text == "В четверг у Анны свободны окна с 10:00."
        assert reply.persisted is True
        # DRF-1354: the prose is the model's, but the booking keyboard rides
        # with it. This assertion used to read `action_data is None` — that
        # was the defect, not the contract: DRF-1266 swapped the card render
        # for prose and took the only booking affordance with it.
        assert reply.action_data is not None
        assert (
            reply.action_data["attachments"][0]["payload"]["buttons"][0]["callback"]
            == "cb:discover:book:t1:m1"
        )
        assert discover.call_count == 1
        assert provider.complete.await_count == 2

        # The second pass input is a PLAIN user message carrying both the
        # original question and the tool data (no tool protocol — the
        # Anthropic adapter would not assemble role="tool" blocks).
        second_messages = provider.complete.call_args_list[1].args[0]
        second_user = second_messages[-1]
        assert second_user["role"] == "user"
        assert "какие окна свободны в четверг" in second_user["content"]
        assert "Анна" in second_user["content"]

    def test_tool_payload_never_persisted_to_history(self, monkeypatch) -> None:
        """The synthetic second-pass message must not pollute the dialog
        history — the store's user-role write stays a no-op marker."""
        provider = AsyncMock()
        provider.complete.side_effect = [
            _show_masters_result(),
            _text_result("словами"),
        ]
        monkeypatch.setattr(concierge, "get_router", lambda: _router_returning(provider))
        monkeypatch.setattr(concierge, "discover_masters", lambda **kwargs: [_card()])
        bot_user, conversation = _bot_user_and_conversation()

        generate_concierge_reply(
            "покажи мастеров",
            bot_user=bot_user,
            conversation=conversation,
            trace_id=TRACE_ID,
        )

        from apps.conversations.models import Message

        rows = list(Message.all_tenants.filter(conversation=conversation))
        assert all(m.role == "assistant" for m in rows)
        assert not any("Инструмент show_masters" in (m.content or "") for m in rows)

    def test_budget_exhausted_renders_cards_deterministically(self, monkeypatch) -> None:
        """Model still asking for the tool on the last allowed pass → the
        deterministic card render (pre-DRF-1266 behaviour), never silence
        or a raw tool dump."""
        provider = AsyncMock()
        provider.complete.side_effect = [_show_masters_result(), _show_masters_result()]
        monkeypatch.setattr(concierge, "get_router", lambda: _router_returning(provider))
        monkeypatch.setattr(concierge, "discover_masters", lambda **kwargs: [_card()])
        bot_user, conversation = _bot_user_and_conversation()

        reply = generate_concierge_reply(
            "покажи мастеров",
            bot_user=bot_user,
            conversation=conversation,
            trace_id=TRACE_ID,
        )

        assert "Анна" in reply.text
        assert reply.persisted is True
        assert reply.action_data is not None
        buttons = reply.action_data["attachments"][0]["payload"]["buttons"]
        assert buttons[0]["callback"] == "cb:discover:book:t1:m1"
        assert provider.complete.await_count == 2

    def test_cap_one_restores_single_pass(self, monkeypatch, settings) -> None:
        """CONCIERGE_MAX_LLM_PASSES=1 — exact pre-DRF-1266 behaviour."""
        settings.CONCIERGE_MAX_LLM_PASSES = 1
        provider = AsyncMock()
        provider.complete.return_value = _show_masters_result()
        monkeypatch.setattr(concierge, "get_router", lambda: _router_returning(provider))
        monkeypatch.setattr(concierge, "discover_masters", lambda **kwargs: [_card()])
        bot_user, conversation = _bot_user_and_conversation()

        reply = generate_concierge_reply(
            "покажи мастеров",
            bot_user=bot_user,
            conversation=conversation,
            trace_id=TRACE_ID,
        )

        assert "Анна" in reply.text
        assert provider.complete.await_count == 1

    def test_llm_error_on_second_pass_renders_collected_cards(self, monkeypatch) -> None:
        """A failing follow-up pass with tool data already in hand renders
        the cards instead of the generic fallback line."""
        provider = AsyncMock()
        provider.complete.side_effect = [
            _show_masters_result(),
            RuntimeError("boom"),
        ]
        monkeypatch.setattr(concierge, "get_router", lambda: _router_returning(provider))
        monkeypatch.setattr(concierge, "discover_masters", lambda **kwargs: [_card()])
        bot_user, conversation = _bot_user_and_conversation()

        reply = generate_concierge_reply(
            "покажи мастеров",
            bot_user=bot_user,
            conversation=conversation,
            trace_id=TRACE_ID,
        )

        assert "Анна" in reply.text
        assert reply.persisted is True

    def test_ask_clarification_does_not_consume_second_pass(self, monkeypatch) -> None:
        """A clarification IS the user-facing reply — no follow-up pass."""
        provider = AsyncMock()
        provider.complete.return_value = CompletionResult(
            text="",
            tool_calls=[
                ToolCall(
                    id="c1",
                    name="ask_clarification",
                    arguments={"question": "В каком городе?", "options": ["Пенза"]},
                )
            ],
            model="gpt-4o-mini",
            provider="openai",
        )
        monkeypatch.setattr(concierge, "get_router", lambda: _router_returning(provider))
        bot_user, conversation = _bot_user_and_conversation()

        reply = generate_concierge_reply(
            "хочу записаться",
            bot_user=bot_user,
            conversation=conversation,
            trace_id=TRACE_ID,
        )

        assert reply.text == "В каком городе?"
        assert provider.complete.await_count == 1


@pytest.mark.django_db(transaction=True)
class TestConciergeMetricEmission:
    """DRF-1211 — the live concierge path now writes AIRequestMetric."""

    def test_single_pass_writes_one_metric_row(self, monkeypatch) -> None:
        provider = AsyncMock()
        provider.complete.return_value = _text_result("Здравствуйте!")
        monkeypatch.setattr(concierge, "get_router", lambda: _router_returning(provider))
        bot_user, conversation = _bot_user_and_conversation()

        generate_concierge_reply(
            "привет",
            bot_user=bot_user,
            conversation=conversation,
            trace_id=TRACE_ID,
        )

        rows = _metrics()
        assert len(rows) == 1
        row = rows[0]
        assert row.llm_pass_index == 1
        assert row.outcome == "success"
        assert row.llm_provider == "openai"
        assert row.llm_model == "gpt-4o-mini"
        assert row.llm_tokens_input == 20
        assert row.llm_tokens_output == 8
        assert row.llm_cost_usd is not None  # gpt-4o-mini is priced
        assert row.latency_llm_ms is not None
        assert row.skill_selected == "concierge"
        assert row.bot_user_id == bot_user.id
        assert row.conversation_id == conversation.id
        # Sentinel global_bot tenant owns the row — the global path runs at
        # current_tenant()=None by design.
        from apps.identity.constants import GLOBAL_BOT_TENANT_SLUG

        assert row.tenant.slug == GLOBAL_BOT_TENANT_SLUG

    def test_multi_pass_writes_one_row_per_pass(self, monkeypatch) -> None:
        provider = AsyncMock()
        provider.complete.side_effect = [
            _show_masters_result(),
            _text_result("словами"),
        ]
        monkeypatch.setattr(concierge, "get_router", lambda: _router_returning(provider))
        monkeypatch.setattr(concierge, "discover_masters", lambda **kwargs: [_card()])
        bot_user, conversation = _bot_user_and_conversation()

        generate_concierge_reply(
            "покажи мастеров",
            bot_user=bot_user,
            conversation=conversation,
            trace_id=TRACE_ID,
        )

        rows = _metrics()
        assert [r.llm_pass_index for r in rows] == [1, 2]
        assert all(r.outcome == "success" for r in rows)

    def test_llm_error_writes_error_row(self, monkeypatch) -> None:
        provider = AsyncMock()
        provider.complete.side_effect = RuntimeError("boom")
        monkeypatch.setattr(concierge, "get_router", lambda: _router_returning(provider))
        bot_user, conversation = _bot_user_and_conversation()

        reply = generate_concierge_reply(
            "привет",
            bot_user=bot_user,
            conversation=conversation,
            trace_id=TRACE_ID,
        )

        assert reply.persisted is False
        rows = _metrics()
        assert len(rows) == 1
        assert rows[0].outcome == "error"
        assert rows[0].llm_pass_index == 1

    def test_metric_failure_never_breaks_turn(self, monkeypatch) -> None:
        """Observability is best-effort: a blowing recorder degrades to a
        WARN log, the user still gets the reply."""
        provider = AsyncMock()
        provider.complete.return_value = _text_result("Здравствуйте!")
        monkeypatch.setattr(concierge, "get_router", lambda: _router_returning(provider))

        def _boom(**kwargs):  # noqa: ANN003
            raise RuntimeError("db down")

        monkeypatch.setattr(concierge, "record_ai_request", _boom)
        bot_user, conversation = _bot_user_and_conversation()

        reply = generate_concierge_reply(
            "привет",
            bot_user=bot_user,
            conversation=conversation,
            trace_id=TRACE_ID,
        )

        assert reply.text == "Здравствуйте!"


@pytest.mark.django_db(transaction=True)
class TestSecondPassOutboundContract:
    """DRF-1266 safety requirement (§5): the second pass must not bypass
    what the first pass goes through. On the live global path the outbound
    gate is the handler's single DiscoveryReply → send route (an outbound
    safety check does not exist — known debt DRF-1210, out of scope), and
    the inbound gate runs once in the handler BEFORE the concierge. So the
    contract to pin here: a second-pass text reply is returned as the SAME
    plain DiscoveryReply shape as a first-pass text reply (persisted=True,
    no action_data) — nothing about multi-pass opens a side channel that
    skips the handler's outbound path."""

    def test_second_pass_text_reply_shape_matches_first_pass(self, monkeypatch) -> None:
        provider = AsyncMock()
        provider.complete.side_effect = [
            _show_masters_result(),
            _text_result("Ответ второго прохода."),
        ]
        monkeypatch.setattr(concierge, "get_router", lambda: _router_returning(provider))
        monkeypatch.setattr(concierge, "discover_masters", lambda **kwargs: [_card()])
        bot_user, conversation = _bot_user_and_conversation()

        second_pass_reply = generate_concierge_reply(
            "покажи мастеров",
            bot_user=bot_user,
            conversation=conversation,
            trace_id=TRACE_ID,
        )

        # Same shape as a first-pass plain-text reply: the handler cannot
        # tell (and must not need to tell) which pass produced the text.
        assert second_pass_reply.persisted is True
        assert second_pass_reply.action_data is None
        assert second_pass_reply.text == "Ответ второго прохода."

    def test_second_pass_text_capped_like_first_pass(self, monkeypatch) -> None:
        """The _MAX_REPLY_CHARS cap applies to the second pass's text too —
        the reply leaves through the same rendering rules."""
        provider = AsyncMock()
        provider.complete.side_effect = [
            _show_masters_result(),
            _text_result("я" * 5000),
        ]
        monkeypatch.setattr(concierge, "get_router", lambda: _router_returning(provider))
        monkeypatch.setattr(concierge, "discover_masters", lambda **kwargs: [_card()])
        bot_user, conversation = _bot_user_and_conversation()

        reply = generate_concierge_reply(
            "покажи мастеров",
            bot_user=bot_user,
            conversation=conversation,
            trace_id=TRACE_ID,
        )

        from apps.orchestrator.discovery import _MAX_REPLY_CHARS

        assert len(reply.text) == _MAX_REPLY_CHARS
