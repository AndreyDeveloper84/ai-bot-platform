"""DRF-1286 — «модель пообещала и не позвала инструмент»: detection, one
forced-tool retry, and the metric row that keeps its cost visible.

The failure this covers is silent by construction: the turn succeeds, the
text is well-formed, tokens are billed, latency is normal — and nothing
happens. It is the same failure the DRF-1102 audit cited when it added the
regex short-circuit (`generate_direct_show_masters_reply`), so these tests
double as the evidence that the bypass is treating a curable symptom.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from apps.llm.protocol import CompletionResult, ToolCall
from apps.orchestrator import concierge
from apps.orchestrator.concierge import (
    _looks_like_promise_without_tool,
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


def _promise(text: str = "Секундочку, сейчас подберу вам мастеров в Пензе!") -> CompletionResult:
    """A textbook promise-without-tool answer: prose, no tool_calls."""
    return CompletionResult(
        text=text,
        tool_calls=[],
        prompt_tokens=40,
        completion_tokens=12,
        model="gpt-4o-mini",
        provider="openai",
        finish_reason="stop",
    )


def _show_masters() -> CompletionResult:
    return CompletionResult(
        text="",
        tool_calls=[
            ToolCall(
                id="c1",
                name="show_masters",
                arguments={"city": "Пенза", "specialization": "массаж"},
            )
        ],
        prompt_tokens=45,
        completion_tokens=9,
        model="gpt-4o-mini",
        provider="openai",
        finish_reason="tool_calls",
    )


def _text(text: str) -> CompletionResult:
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
        channel_user_id="drf1286-uid",
        chat_id="drf1286-chat",
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


def _tool_choices(provider: AsyncMock) -> list[object]:
    """The `tool_choice` kwarg of every complete() call, in order."""
    return [call.kwargs.get("tool_choice") for call in provider.complete.await_args_list]


# ---------------------------------------------------------------------------
# Detector
# ---------------------------------------------------------------------------


class TestPromiseDetector:
    @pytest.mark.parametrize(
        "text",
        [
            # first-person commitments — what the live model actually writes
            "Секундочку, сейчас подберу вам мастеров!",
            "Сейчас посмотрю, кто есть в Пензе.",
            "Гляну свободные окна и вернусь.",
            "Уточню и покажу подходящие варианты.",
            "Одну минуту, ищу — найду для вас лучших.",
            "Подождите немного, пожалуйста.",
            # announces a result that, with no tool call, does not exist
            "Вот варианты, которые вам подойдут:",
            # joint-action framing of the same promise
            "Давайте подберём мастера под ваш запрос.",
        ],
    )
    def test_promises_detected(self, text: str) -> None:
        assert _looks_like_promise_without_tool(text) is True

    @pytest.mark.parametrize(
        "text",
        [
            "",
            "Здравствуйте! Чем могу помочь?",
            "К сожалению, я не подскажу по медицинским вопросам.",
            "В каком городе вам удобно?",
            # ── the legacy false positives this list deliberately drops ──
            # bare stems ("подбер", "посмотр") also match imperatives aimed
            # at the CLIENT. Forcing a tool call onto these turns would be
            # a regression, not a fix.
            "Подберите удобное для вас время и напишите мне.",
            "Посмотрите профиль мастера перед записью.",
            "Рассмотрите вариант записи на будни — обычно свободнее.",
        ],
    )
    def test_non_promises_ignored(self, text: str) -> None:
        assert _looks_like_promise_without_tool(text) is False

    def test_none_is_not_a_promise(self) -> None:
        assert _looks_like_promise_without_tool(None) is False


# ---------------------------------------------------------------------------
# Reproduction + closure
# ---------------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
class TestForcedToolRetry:
    def test_promise_without_tool_is_retried_and_closed(self, monkeypatch) -> None:
        """Reproduce the failure, then show the retry closes it.

        Without the retry this turn ends as prose: «сейчас подберу» and no
        masters — the exact 23.08 «покажи массажистов в пензе» outcome the
        regex bypass was introduced to paper over.
        """
        provider = AsyncMock()
        provider.complete.side_effect = [
            _promise(),
            _show_masters(),
            _text("Вот кто подойдёт: Анна"),
        ]
        monkeypatch.setattr(concierge, "get_router", lambda: _router_returning(provider))
        monkeypatch.setattr(concierge, "discover_masters", lambda **kwargs: [_card()])
        bot_user, conversation = _bot_user_and_conversation()

        reply = generate_concierge_reply(
            "покажи массажистов в пензе",
            bot_user=bot_user,
            conversation=conversation,
            trace_id=TRACE_ID,
        )

        # The turn reached the tool and produced real data.
        assert "Анна" in reply.text
        # Call 1 = the promise (unforced), call 2 = the forced retry,
        # call 3 = the DRF-1266 follow-up pass (unforced again).
        assert _tool_choices(provider) == [None, "required", None]

    def test_retry_reuses_the_same_messages(self, monkeypatch) -> None:
        """The forced pass is a REPEAT of the same turn, not a new prompt.

        Anything else would make the retry a second, differently-primed
        question — and its answer would not be comparable to the first.
        """
        provider = AsyncMock()
        provider.complete.side_effect = [_promise(), _show_masters(), _text("готово")]
        monkeypatch.setattr(concierge, "get_router", lambda: _router_returning(provider))
        monkeypatch.setattr(concierge, "discover_masters", lambda **kwargs: [_card()])
        bot_user, conversation = _bot_user_and_conversation()

        generate_concierge_reply(
            "покажи массажистов в пензе",
            bot_user=bot_user,
            conversation=conversation,
            trace_id=TRACE_ID,
        )

        first, forced = provider.complete.await_args_list[0], provider.complete.await_args_list[1]
        assert first.args[0] == forced.args[0]
        assert first.kwargs["tools"] == forced.kwargs["tools"]

    def test_second_attempt_never_spawns_a_third(self, monkeypatch) -> None:
        """One retry, not a loop — the model that ignored the force once
        will ignore it again, and the bill would triple."""
        provider = AsyncMock()
        # Both answers promise and call nothing. The detector matches the
        # second one too — the guard is structural, not textual.
        provider.complete.side_effect = [_promise(), _promise("Сейчас посмотрю, минутку!")]
        monkeypatch.setattr(concierge, "get_router", lambda: _router_returning(provider))
        bot_user, conversation = _bot_user_and_conversation()

        reply = generate_concierge_reply(
            "покажи массажистов в пензе",
            bot_user=bot_user,
            conversation=conversation,
            trace_id=TRACE_ID,
        )

        assert provider.complete.await_count == 2
        assert _tool_choices(provider) == [None, "required"]
        # The FIRST answer is kept: shipping the second tool-less reply
        # would buy the client nothing.
        assert "подберу" in reply.text

    def test_no_retry_when_the_model_called_the_tool(self, monkeypatch) -> None:
        provider = AsyncMock()
        provider.complete.side_effect = [_show_masters(), _text("Вот мастера")]
        monkeypatch.setattr(concierge, "get_router", lambda: _router_returning(provider))
        monkeypatch.setattr(concierge, "discover_masters", lambda **kwargs: [_card()])
        bot_user, conversation = _bot_user_and_conversation()

        generate_concierge_reply(
            "покажи массажистов в пензе",
            bot_user=bot_user,
            conversation=conversation,
            trace_id=TRACE_ID,
        )

        assert _tool_choices(provider) == [None, None]

    def test_no_retry_for_an_ordinary_text_reply(self, monkeypatch) -> None:
        """Plain conversation must not be forced into a tool call."""
        provider = AsyncMock()
        provider.complete.return_value = _text("Здравствуйте! Чем могу помочь?")
        monkeypatch.setattr(concierge, "get_router", lambda: _router_returning(provider))
        bot_user, conversation = _bot_user_and_conversation()

        reply = generate_concierge_reply(
            "привет",
            bot_user=bot_user,
            conversation=conversation,
            trace_id=TRACE_ID,
        )

        assert provider.complete.await_count == 1
        assert reply.text == "Здравствуйте! Чем могу помочь?"

    def test_not_armed_on_the_multipass_follow_up(self, monkeypatch) -> None:
        """DRF-1266's second pass is told NOT to call the tool again.

        A promise there ("сейчас посмотрю") is the model narrating over
        data it already has — forcing a tool call would fight the prompt
        and re-run the search for nothing.
        """
        provider = AsyncMock()
        provider.complete.side_effect = [
            _show_masters(),
            _promise("Сейчас посмотрю: Анна подойдёт"),
        ]
        monkeypatch.setattr(concierge, "get_router", lambda: _router_returning(provider))
        monkeypatch.setattr(concierge, "discover_masters", lambda **kwargs: [_card()])
        bot_user, conversation = _bot_user_and_conversation()

        generate_concierge_reply(
            "покажи массажистов в пензе",
            bot_user=bot_user,
            conversation=conversation,
            trace_id=TRACE_ID,
        )

        assert provider.complete.await_count == 2
        assert _tool_choices(provider) == [None, None]

    def test_retry_failure_keeps_the_first_answer(self, monkeypatch) -> None:
        """The retry is an optimisation. Its failure must not cost the
        client the reply we already have."""
        provider = AsyncMock()
        provider.complete.side_effect = [_promise(), RuntimeError("vendor 500")]
        monkeypatch.setattr(concierge, "get_router", lambda: _router_returning(provider))
        bot_user, conversation = _bot_user_and_conversation()

        reply = generate_concierge_reply(
            "покажи массажистов в пензе",
            bot_user=bot_user,
            conversation=conversation,
            trace_id=TRACE_ID,
        )

        assert "подберу" in reply.text
        assert provider.complete.await_count == 2

    def test_provider_ignoring_the_force_keeps_the_first_answer(self, monkeypatch) -> None:
        """A provider without real forced-tool support degrades to one
        wasted call, never to a broken turn.

        This is the operator-flip scenario: if a provider silently ignores
        `tool_choice`, the client must still get an answer — and the
        WASTED call must be the one billed to the discard row, otherwise
        the first attempt is counted twice and the retry's cost vanishes.
        """
        provider = AsyncMock()
        # Distinct token counts so the accounting is checkable:
        # promise = 40/12, forced-but-tool-less = 20/8.
        provider.complete.side_effect = [_promise(), _text("всё ещё просто текст")]
        monkeypatch.setattr(concierge, "get_router", lambda: _router_returning(provider))
        bot_user, conversation = _bot_user_and_conversation()

        reply = generate_concierge_reply(
            "покажи массажистов в пензе",
            bot_user=bot_user,
            conversation=conversation,
            trace_id=TRACE_ID,
        )

        assert "подберу" in reply.text
        discarded, used = _metrics()
        # The discarded call is the FORCED one here, not the first.
        assert discarded.fallback_triggered is True
        assert (discarded.llm_tokens_input, discarded.llm_tokens_output) == (20, 8)
        # The answer we shipped is the promise attempt — its own tokens.
        assert (used.llm_tokens_input, used.llm_tokens_output) == (40, 12)


# ---------------------------------------------------------------------------
# Metrics — «дорого» must be separable from «часто ошибается»
# ---------------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
class TestForcedToolRetryMetrics:
    def test_discarded_attempt_and_retry_get_separate_rows(self, monkeypatch) -> None:
        provider = AsyncMock()
        provider.complete.side_effect = [_promise(), _show_masters(), _text("Анна свободна")]
        monkeypatch.setattr(concierge, "get_router", lambda: _router_returning(provider))
        monkeypatch.setattr(concierge, "discover_masters", lambda **kwargs: [_card()])
        bot_user, conversation = _bot_user_and_conversation()

        generate_concierge_reply(
            "покажи массажистов в пензе",
            bot_user=bot_user,
            conversation=conversation,
            trace_id=TRACE_ID,
        )

        rows = _metrics()
        # Three LLM calls in the turn → three rows, indices 1..3. The
        # field keeps its documented meaning (index of the LLM call
        # within the turn) rather than being redefined.
        assert [r.llm_pass_index for r in rows] == [1, 2, 3]

        discarded, forced, follow_up = rows
        # Row 1 — the promise we threw away. Its tokens were still billed,
        # so they are recorded rather than folded into the retry's row.
        assert discarded.fallback_triggered is True
        assert discarded.outcome == "fallback"
        assert discarded.llm_tokens_input == 40
        assert discarded.llm_tokens_output == 12
        # Rows 2-3 — the answers we used. Nothing else on this path sets
        # fallback_triggered, so `WHERE fallback_triggered` counts exactly
        # the promise-without-tool events and their wasted cost.
        assert forced.fallback_triggered is False
        assert follow_up.fallback_triggered is False
        # Same skill slug everywhere: cost dashboards filtering
        # skill_selected='concierge' keep seeing the whole turn.
        assert {r.skill_selected for r in rows} == {"concierge"}

    def test_no_retry_leaves_indices_untouched(self, monkeypatch) -> None:
        """Regression guard for DRF-1266: without a retry the LLM-call
        counter and the pass counter stay identical."""
        provider = AsyncMock()
        provider.complete.side_effect = [_show_masters(), _text("словами")]
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
        assert not any(r.fallback_triggered for r in rows)

    def test_retry_does_not_eat_the_multipass_budget(self, monkeypatch, settings) -> None:
        """A forced retry is an extra LLM call, NOT an extra pass.

        If it consumed the DRF-1266 budget, the turn would end on the
        deterministic card render instead of the model's own wording —
        a silent quality regression rather than a visible failure.
        """
        settings.CONCIERGE_MAX_LLM_PASSES = 2
        provider = AsyncMock()
        provider.complete.side_effect = [_promise(), _show_masters(), _text("Анна свободна завтра")]
        monkeypatch.setattr(concierge, "get_router", lambda: _router_returning(provider))
        monkeypatch.setattr(concierge, "discover_masters", lambda **kwargs: [_card()])
        bot_user, conversation = _bot_user_and_conversation()

        reply = generate_concierge_reply(
            "покажи массажистов в пензе",
            bot_user=bot_user,
            conversation=conversation,
            trace_id=TRACE_ID,
        )

        assert provider.complete.await_count == 3
        assert reply.text == "Анна свободна завтра"
