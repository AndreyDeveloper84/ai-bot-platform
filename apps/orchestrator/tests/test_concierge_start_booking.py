"""DRF-1354 — the concierge can START a booking, not only describe one.

## The trace this file is written against

Live pilot, 24.08 07:52–07:53. The owner tried four times and never booked::

    07:52:41  владелец | запиши к Архипкину Денису на завтра
    07:52:43  бот      | (пустое сообщение)
    07:52:46  бот      | В Пензе есть несколько мастеров: — Архипкин Денис …
                         Если хочешь записаться к Архипкину Денису, дай знать!
    07:53:14  владелец | даю знать
    07:53:20  бот      | …Если хочешь записаться к Архипкину Денису, дай знать!

Four separate defects sit in those five lines, and this module pins one test
per defect:

1. **No verb.** The concierge roster (``CONCIERGE_TOOL_SPECS``) held only
   tools that SHOW things. Booking was reachable exclusively by tapping a
   master card, so a model told to «предложи записаться» could only describe
   the act. → :class:`TestStartBookingTool`.
2. **No buttons either.** The DRF-1266 multi-pass prose reply replaced the
   deterministic card render, and the render was the only thing that ever
   attached ``cb:discover:book:`` buttons. The paragraph the owner read had
   nothing under it to tap. → :class:`TestProseKeepsTheKeyboard`.
3. **Empty bot messages.** ai-core writes an assistant row per pass, and a
   tool-selection pass carries no text. → :class:`TestNoEmptyAssistantRows`.
4. **The transcript did not hold the reply.** Every deterministic branch
   returned text the model never produced while the persisted row carried the
   model's empty content — so the next turn's history could not see what the
   bot had just shown. → :class:`TestTranscriptHoldsWhatWasSent`.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from apps.llm.protocol import CompletionResult, ToolCall
from apps.orchestrator import concierge
from apps.orchestrator.concierge import (
    CONCIERGE_TOOL_SPECS,
    START_BOOKING_TOOL_SPEC,
    generate_concierge_reply,
)
from apps.orchestrator.discovery import DiscoveryReply

TRACE_ID = str(uuid.uuid4())


def _router_returning(provider: AsyncMock) -> Mock:
    router = Mock()
    router.get_provider.return_value = provider
    return router


def _card(name: str = "Архипкин Денис", master_id: str = "m1", service_id=None) -> SimpleNamespace:
    return SimpleNamespace(
        tenant_id="t1",
        master_id=master_id,
        name=name,
        specialization="",
        rating=None,
        city="Пенза",
        service_id=service_id,
        service_name="Массаж классический" if service_id else "",
    )


def _tool_result(name: str, args: dict) -> CompletionResult:
    return CompletionResult(
        text="",
        tool_calls=[ToolCall(id="c1", name=name, arguments=args)],
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
        channel_user_id="drf1354-uid",
        chat_id="drf1354-chat",
    )
    conversation = resolve_active_global_conversation(bot_user)
    return bot_user, conversation


def _rows(conversation):
    from apps.conversations.models import Message

    return list(Message.all_tenants.filter(conversation=conversation).order_by("created_at"))


class TestRoster:
    def test_start_booking_is_armed(self) -> None:
        """The roster the model is handed must contain the booking verb — the
        whole ticket is that it did not."""
        assert START_BOOKING_TOOL_SPEC["name"] == "start_booking"
        assert START_BOOKING_TOOL_SPEC in CONCIERGE_TOOL_SPECS

    def test_master_is_the_only_required_argument(self) -> None:
        """Requiring a service too would recreate DRF-968: the bot asking for
        something the person did not say before it will do what they did."""
        assert START_BOOKING_TOOL_SPEC["parameters"]["required"] == ["master"]


@pytest.mark.django_db(transaction=True)
class TestStartBookingTool:
    def test_the_live_turn_now_enters_booking(self, monkeypatch) -> None:
        """«запиши к Архипкину Денису на завтра» → the handoff runs with the
        resolved master, and the user sees the booking entrypoint's reply."""
        provider = AsyncMock()
        provider.complete.return_value = _tool_result(
            "start_booking", {"master": "Архипкин Денис", "service": "массаж"}
        )
        monkeypatch.setattr(concierge, "get_router", lambda: _router_returning(provider))
        find = Mock(return_value=[_card(service_id="s1")])
        monkeypatch.setattr(concierge, "find_masters_by_name", find)
        handoff = Mock(
            return_value=DiscoveryReply(
                text="Выберите дату записи к Архипкину Денису:",
                action_data={"attachments": [{"type": "inline_keyboard", "payload": {}}]},
            )
        )
        monkeypatch.setattr(concierge, "handoff_to_booking", handoff)
        bot_user, conversation = _bot_user_and_conversation()

        reply = generate_concierge_reply(
            "запиши к Архипкину Денису на завтра",
            bot_user=bot_user,
            conversation=conversation,
            trace_id=TRACE_ID,
        )

        assert reply.text == "Выберите дату записи к Архипкину Денису:"
        assert reply.action_data is not None
        # The name went to the CATALOG, not to the model's judgement.
        assert find.call_args.args[0] == "Архипкин Денис"
        # The handoff got the ids the catalog resolved, service context included.
        assert handoff.call_args.kwargs["tenant_id"] == "t1"
        assert handoff.call_args.kwargs["master_id"] == "m1"
        assert handoff.call_args.kwargs["service_id"] == "s1"
        # One model call: the tool answered the turn, no second pass spent.
        assert provider.complete.await_count == 1

    def test_two_denises_ask_one_closable_question(self, monkeypatch) -> None:
        """The ticket's negative proof: «запиши к Денису» with two Денисов must
        produce a re-ask the person can close in one word (or one tap) — never
        the five-name list of 07:53:33."""
        provider = AsyncMock()
        provider.complete.return_value = _tool_result("start_booking", {"master": "Денис"})
        monkeypatch.setattr(concierge, "get_router", lambda: _router_returning(provider))
        monkeypatch.setattr(
            concierge,
            "find_masters_by_name",
            lambda *a, **kw: [_card(), _card("Денис Кузнецов", master_id="m2")],
        )
        handoff = Mock()
        monkeypatch.setattr(concierge, "handoff_to_booking", handoff)
        bot_user, conversation = _bot_user_and_conversation()

        reply = generate_concierge_reply(
            "запиши к Денису",
            bot_user=bot_user,
            conversation=conversation,
            trace_id=TRACE_ID,
        )

        # A question about the NAME, not a redraw of the catalog.
        assert "Уточните" in reply.text
        assert "Архипкин Денис" in reply.text
        assert "Денис Кузнецов" in reply.text
        # …closable with one tap, on the same callback grammar as the cards.
        assert reply.action_data is not None
        buttons = reply.action_data["attachments"][0]["payload"]["buttons"]
        assert [b["callback"] for b in buttons] == [
            "cb:discover:book:t1:m1",
            "cb:discover:book:t1:m2",
        ]
        # Nobody is booked on a guess.
        handoff.assert_not_called()

    def test_unknown_name_is_answered_not_looped(self, monkeypatch) -> None:
        """A miss says so and names what was looked for. It must NOT fall back
        to the model's prose, which is where «дай знать» came from."""
        provider = AsyncMock()
        provider.complete.return_value = _tool_result("start_booking", {"master": "Иванов Пётр"})
        monkeypatch.setattr(concierge, "get_router", lambda: _router_returning(provider))
        monkeypatch.setattr(concierge, "find_masters_by_name", lambda *a, **kw: [])
        handoff = Mock()
        monkeypatch.setattr(concierge, "handoff_to_booking", handoff)
        bot_user, conversation = _bot_user_and_conversation()

        reply = generate_concierge_reply(
            "запиши к Иванову Петру",
            bot_user=bot_user,
            conversation=conversation,
            trace_id=TRACE_ID,
        )

        assert "Иванов Пётр" in reply.text
        assert "дай знать" not in reply.text.lower()
        handoff.assert_not_called()


@pytest.mark.django_db(transaction=True)
class TestProseKeepsTheKeyboard:
    def test_multipass_prose_carries_the_booking_buttons(self, monkeypatch) -> None:
        """DRF-1266 traded the card render for warm prose and silently dropped
        the only booking affordance the surface had. The words stay the
        model's; the keyboard comes back."""
        provider = AsyncMock()
        provider.complete.side_effect = [
            _tool_result("show_masters", {"city": "Пенза", "specialization": "массаж"}),
            _text_result("В Пензе массаж делают Архипкин Денис и Сазонова Инна."),
        ]
        monkeypatch.setattr(concierge, "get_router", lambda: _router_returning(provider))
        monkeypatch.setattr(concierge, "discover_masters", lambda **kw: [_card()])
        monkeypatch.setattr(concierge, "service_coverage", lambda *a, **kw: ([], []))
        bot_user, conversation = _bot_user_and_conversation()

        reply = generate_concierge_reply(
            "хочу массаж в пензе",
            bot_user=bot_user,
            conversation=conversation,
            trace_id=TRACE_ID,
        )

        assert reply.text == "В Пензе массаж делают Архипкин Денис и Сазонова Инна."
        assert reply.action_data is not None
        buttons = reply.action_data["attachments"][0]["payload"]["buttons"]
        assert buttons[0]["label"] == "Записаться к Архипкин Денис"
        assert buttons[0]["callback"].startswith("cb:discover:book:t1:m1")


@pytest.mark.django_db(transaction=True)
class TestNoEmptyAssistantRows:
    def test_a_tool_selection_pass_writes_no_blank_row(self, monkeypatch) -> None:
        """Four blank bot messages stand in the pilot trace, one before each
        answer. They are ai-core's per-pass assistant row for a pass that
        carried a tool call and no text."""
        provider = AsyncMock()
        provider.complete.side_effect = [
            _tool_result("show_masters", {"city": "Пенза", "specialization": "массаж"}),
            _text_result("Вот кто делает массаж в Пензе."),
        ]
        monkeypatch.setattr(concierge, "get_router", lambda: _router_returning(provider))
        monkeypatch.setattr(concierge, "discover_masters", lambda **kw: [_card()])
        monkeypatch.setattr(concierge, "service_coverage", lambda *a, **kw: ([], []))
        bot_user, conversation = _bot_user_and_conversation()

        generate_concierge_reply(
            "хочу массаж в пензе",
            bot_user=bot_user,
            conversation=conversation,
            trace_id=TRACE_ID,
        )

        rows = _rows(conversation)
        assert [r.content for r in rows] == ["Вот кто делает массаж в Пензе."]


@pytest.mark.django_db(transaction=True)
class TestTranscriptHoldsWhatWasSent:
    def test_a_deterministic_reply_is_the_row(self, monkeypatch) -> None:
        """The budget-exhausted card render is text the model never produced.
        Before DRF-1354 the row for that turn held the model's empty
        tool-selection content, so the next turn's history had no record of
        the masters that had just been shown."""
        provider = AsyncMock()
        provider.complete.return_value = _tool_result(
            "show_masters", {"city": "Пенза", "specialization": "массаж"}
        )
        monkeypatch.setattr(concierge, "get_router", lambda: _router_returning(provider))
        monkeypatch.setattr(concierge, "discover_masters", lambda **kw: [_card()])
        monkeypatch.setattr(concierge, "service_coverage", lambda *a, **kw: ([], []))
        bot_user, conversation = _bot_user_and_conversation()

        reply = generate_concierge_reply(
            "хочу массаж в пензе",
            bot_user=bot_user,
            conversation=conversation,
            trace_id=TRACE_ID,
        )

        rows = _rows(conversation)
        assert [r.content for r in rows] == [reply.text]
        assert "Архипкин Денис" in rows[0].content

    def test_a_handoff_reply_is_the_row(self, monkeypatch) -> None:
        provider = AsyncMock()
        provider.complete.return_value = _tool_result("start_booking", {"master": "Архипкин Денис"})
        monkeypatch.setattr(concierge, "get_router", lambda: _router_returning(provider))
        monkeypatch.setattr(concierge, "find_masters_by_name", lambda *a, **kw: [_card()])
        monkeypatch.setattr(
            concierge,
            "handoff_to_booking",
            lambda **kw: DiscoveryReply(text="Выберите дату:"),
        )
        bot_user, conversation = _bot_user_and_conversation()

        generate_concierge_reply(
            "запиши к Архипкину Денису",
            bot_user=bot_user,
            conversation=conversation,
            trace_id=TRACE_ID,
        )

        assert [r.content for r in _rows(conversation)] == ["Выберите дату:"]


@pytest.mark.django_db(transaction=True)
class TestTheDayIsAlreadyStoredWhenTheHandoffReadsIt:
    def test_na_zavtra_lands_in_the_same_turn(self, monkeypatch) -> None:
        """«запиши к Архипкину Денису НА ЗАВТРА» names the master and the day
        in one sentence, so the tap path's assumption breaks: DRF-1325 stores
        the preference in the MAX handler AFTER the reply is built, which for
        this turn is after the handoff already read it. Without the earlier
        write the person names a day and gets a bare calendar."""
        from apps.orchestrator.time_preference import load_time_preference

        seen: dict = {}

        def _handoff(**kwargs):
            # Read at the moment the handoff runs, not afterwards — the
            # ordering is the whole assertion.
            conv = kwargs["global_bot_user"]
            del conv
            seen["pref"] = load_time_preference(conversation)
            return DiscoveryReply(text="Свободные окна на завтра:")

        provider = AsyncMock()
        provider.complete.return_value = _tool_result("start_booking", {"master": "Архипкин Денис"})
        monkeypatch.setattr(concierge, "get_router", lambda: _router_returning(provider))
        monkeypatch.setattr(concierge, "find_masters_by_name", lambda *a, **kw: [_card()])
        monkeypatch.setattr(concierge, "handoff_to_booking", _handoff)
        bot_user, conversation = _bot_user_and_conversation()

        generate_concierge_reply(
            "запиши к Архипкину Денису на завтра",
            bot_user=bot_user,
            conversation=conversation,
            trace_id=TRACE_ID,
        )

        assert seen["pref"] is not None
        assert seen["pref"].day_offset == 1


@pytest.mark.django_db(transaction=True)
class TestOneRowPerTurn:
    def test_discarded_model_prose_does_not_stay_in_the_row(self, monkeypatch) -> None:
        """A pass can carry prose AND a tool call. When the branch then answers
        deterministically, that sentence was never sent — so it must not be
        what the transcript (and the next turn's history) says the bot said."""
        provider = AsyncMock()
        chatty = CompletionResult(
            text="Секунду, посмотрю кто есть.",
            tool_calls=[
                ToolCall(
                    id="c1",
                    name="show_masters",
                    arguments={"city": "Пенза", "specialization": "массаж"},
                )
            ],
            prompt_tokens=10,
            completion_tokens=5,
            model="gpt-4o-mini",
            provider="openai",
            finish_reason="tool_calls",
        )
        provider.complete.return_value = chatty
        monkeypatch.setattr(concierge, "get_router", lambda: _router_returning(provider))
        monkeypatch.setattr(concierge, "discover_masters", lambda **kw: [_card()])
        monkeypatch.setattr(concierge, "service_coverage", lambda *a, **kw: ([], []))
        bot_user, conversation = _bot_user_and_conversation()

        reply = generate_concierge_reply(
            "хочу массаж в пензе",
            bot_user=bot_user,
            conversation=conversation,
            trace_id=TRACE_ID,
        )

        rows = _rows(conversation)
        assert [r.content for r in rows] == [reply.text]
        assert "Секунду" not in rows[0].content

    def test_token_totals_of_every_pass_ride_the_single_row(self, monkeypatch) -> None:
        """One row per turn must not mean one PASS's cost per turn: the row
        carried the last pass's tokens before, which under-reported every
        multi-pass turn in the transcript."""
        provider = AsyncMock()
        provider.complete.side_effect = [
            _tool_result("show_masters", {"city": "Пенза", "specialization": "массаж"}),
            _text_result("Вот кто есть."),
        ]
        monkeypatch.setattr(concierge, "get_router", lambda: _router_returning(provider))
        monkeypatch.setattr(concierge, "discover_masters", lambda **kw: [_card()])
        monkeypatch.setattr(concierge, "service_coverage", lambda *a, **kw: ([], []))
        bot_user, conversation = _bot_user_and_conversation()

        generate_concierge_reply(
            "хочу массаж в пензе",
            bot_user=bot_user,
            conversation=conversation,
            trace_id=TRACE_ID,
        )

        rows = _rows(conversation)
        assert len(rows) == 1
        # 10 + 20 in, 5 + 8 out — the two _tool_result / _text_result passes.
        assert (rows[0].tokens_in, rows[0].tokens_out) == (30, 13)
